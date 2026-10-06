# SPDX-License-Identifier: GPL-3.0-only
"""Quest backup, OVR Port conversion and per-game Lepton deployment."""
from pathlib import Path, PurePosixPath
import base64
import difflib
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import zipfile
import uuid
import paramiko
from steam_shortcuts import upsert, app_id, remove as remove_shortcut
from storage import PROBE, select_storage, card_launcher
from headset_profile import headset_value, patch_runtime, patch_unreal_smali

APP = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
ROOT = APP
ASSETS = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
LIB_VERSION = '3.4.3-23204ea'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

def artwork_query(title):
    return re.sub(r'\s*\(Quest\)\s*$', '', title, flags=re.I).strip()

def suggested_title(package):
    known = {'com.enhanceexperience.tetriseffect':'Tetris Effect: Connected',
             'com.beatgames.beatsaber':'Beat Saber', 'com.Armature.VR4':'Resident Evil 4'}
    if package in known: return known[package]
    name = package.rsplit('.', 1)[-1].replace('_', ' ')
    return re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', name).strip()

def artwork_candidates(title):
    """Return suggestions only; the user must confirm the correct Steam edition."""
    query = artwork_query(title)[:150]
    if not query: return []
    url = 'https://store.steampowered.com/api/storesearch/?' + urllib.parse.urlencode(
        {'term':query, 'l':'english', 'cc':'US'})
    request = urllib.request.Request(url, headers={'User-Agent':'Quest2Frame/1.0'})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.load(response)
    def normalized(text): return re.sub(r'[^a-z0-9]', '', text.casefold())
    candidates = []; seen = set()
    for item in payload.get('items', [])[:30]:
        ident = str(item.get('id', '')); name = item.get('name')
        if not ident.isdigit() or not isinstance(name, str) or ident in seen: continue
        seen.add(ident)
        score = difflib.SequenceMatcher(None, normalized(query), normalized(name)).ratio()
        candidates.append({'id':ident, 'title':name, 'score':score})
    return sorted(candidates, key=lambda item:item['score'], reverse=True)[:10]

def valid_package(value):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+', value):
        raise ValueError('Invalid Android package name')
    return value

def refresh_value(value=0):
    if str(value).strip().lower() == 'default': return 0
    rate = float(value)
    if rate not in (0, 72, 80, 90, 120, 144):
        raise ValueError('Refresh rate must be Default, 72, 80, 90, 120, or 144 Hz')
    return int(rate)

def check_refresh_adapter(folder, rate):
    if not refresh_value(rate): return
    with zipfile.ZipFile(Path(folder) / 'game.apk') as apk:
        name = 'lib/arm64-v8a/libopenxr_loader_generic.so'
        supported = name in apk.namelist() and b'Q2F_REFRESH_RATE_V1' in apk.read(name)
    if not supported:
        raise ValueError('This older build needs reconversion for refresh-rate control. Keep Default to retain its existing compatibility fixes.')

def settings_text(scale=100, controllers=True, foveation=True, refresh_rate=0):
    if not 50 <= float(scale) <= 200: raise ValueError('Resolution must be 50–200%')
    return f'scale={float(scale)/100:.3f}\ncontroller_fix={int(controllers)}\nfoveation_fix={int(foveation)}\nrefresh_rate={refresh_value(refresh_rate)}\n'

def rect_fix_value(game):
    return bool(game.get('rect_fix', game.get('compatibility_fix') == 'swapchain-rectangle-rounding'))

def check_rect_build(game, selected=None):
    built = bool(game.get('built_rect_fix', game.get('compatibility_fix') == 'swapchain-rectangle-rounding'))
    if (rect_fix_value(game) if selected is None else bool(selected)) != built:
        raise ValueError('Black-screen fix changed. Use Rebuild APK only before installing or applying settings.')

def check_headset_build(game, headset=None):
    selected = headset_value(game.get('headset', 'Default') if headset is None else headset)
    if selected != game.get('built_headset', 'Default'):
        raise ValueError('Reported headset changed. Use Rebuild APK only before installing or applying settings.')

# openxr-dispatcher-open (MIT) targets; its start-up log line doubles as a build marker.
DISPATCHER_TARGETS = {'arm64-v8a':['--target=aarch64-linux-android29'],
                      'armeabi-v7a':['--target=armv7a-linux-androideabi29', '-mthumb']}
DISPATCHER_MARKER = b'open dispatcher %s: next loader'

def dispatcher_source():
    return Path(os.environ.get('OPENXR_DISPATCHER_SOURCE') or ROOT / 'tools/openxr-dispatcher-open')

def dispatcher_entries(names):
    """Map each OVR Port dispatcher in an APK to its ABI; a wrapped arm64 runtime keeps its adapter."""
    entries = {}
    for name in names:
        match = re.fullmatch(r'lib/([^/]+)/libopenxr_loader\.so', name)
        if not match: continue
        wrapped = f'lib/{match[1]}/libq2f_original.so'
        entries[wrapped if wrapped in names else name] = match[1]
    return entries

def open_dispatcher_value(game):
    return bool(game.get('open_dispatcher', False))

def check_dispatcher_build(game, selected=None):
    built = bool(game.get('built_open_dispatcher', False))
    if (open_dispatcher_value(game) if selected is None else bool(selected)) != built:
        raise ValueError('OpenXR dispatcher changed. Use Rebuild APK only before installing or applying settings.')

def check_dispatcher_headset(open_dispatcher, headset):
    if open_dispatcher and headset_value(headset) != 'Default':
        raise ValueError("Reported headset overrides patch OVR Port's dispatcher. Keep Default with the open-source OpenXR dispatcher.")

def digest(path):
    with open(path, 'rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()

def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)

def launcher_text(base, ident, lepton, storage_setup=''):
    return (ASSETS / 'launcher.sh').read_text(encoding='utf-8').replace('@APP_DIR@', shlex.quote(base)).replace('@APP_ID@', str(int(ident))).replace('@LEPTON@', shlex.quote(lepton)).replace('@STORAGE_SETUP@',storage_setup)

class Bridge:
    def __init__(self, log=print, progress=None):
        self.log = log
        self.progress_callback = progress or (lambda stage, percent: None)
        self.progress_stage = ''
        self.progress_prefix = ''
        self.adb = shutil.which('adb') or str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Android/Sdk/platform-tools/adb.exe')

    def progress(self, stage, percent=None):
        self.progress_stage = stage
        self.progress_callback(self.progress_prefix + stage, percent)

    def run(self, args, check=True):
        p = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             encoding='utf-8', errors='replace', creationflags=NO_WINDOW)
        lines = []; previous = None
        for line in p.stdout:
            lines.append(line)
            clean = re.sub(r'^[|/\\-] +(?:\[[^\]]*\] +)?', '', line.strip())
            if str(args[0]) == str(self.adb) and any(str(a) in ('push','pull') for a in args):
                match = re.search(r'\[\s*(\d+(?:\.\d+)?)%', line)
                if match: self.progress(self.progress_stage, min(100, float(match[1])))
            if clean and clean != previous and not clean.startswith('Q2F_ICON='): self.log(clean)
            previous = clean
        p.wait(); output = ''.join(lines)
        if check and p.returncode: raise RuntimeError(f'Command failed ({Path(str(args[0])).name}):\n{output[-2500:]}')
        return output

    def adb_run(self, serial, *args, check=True):
        return self.run([self.adb, '-s', serial, *args], check)

    def shell(self, serial, *args, check=True):
        return self.adb_run(serial, 'shell', shlex.join(str(a) for a in args), check=check)

    def devices(self):
        text = self.run([self.adb, 'devices'])
        return [line.split()[0] for line in text.splitlines() if len(line.split()) == 2 and line.split()[1] == 'device']

    def quests(self):
        result = []
        for serial in self.devices():
            model = self.shell(serial, 'getprop', 'ro.product.model', check=False).strip()
            maker = self.shell(serial, 'getprop', 'ro.product.manufacturer', check=False).strip()
            if 'quest' in model.lower() or maker.lower() in ('oculus', 'meta'):
                result.append((serial, model))
        return result

    def packages(self, serial):
        return sorted(x[8:].strip() for x in self.shell(serial, 'pm', 'list', 'packages', '-3').splitlines() if x.startswith('package:'))

    def label_helper(self):
        source=ASSETS/'native/QuestAppLabels.java'
        cache=ROOT/'tools/quest-labels'/digest(source)[:16]
        dex=cache/'classes.dex'
        if dex.exists():return dex
        java,_,build,_=self.toolchain()
        platforms=sorted((build.parent.parent/'platforms').glob('*/android.jar'))
        if not platforms:raise RuntimeError('Android SDK platform android.jar is required for app names')
        cache.mkdir(parents=True,exist_ok=True)
        javac=java.with_name('javac.exe')
        compiler=[javac]
        if not javac.exists():
            ecj=ROOT/'tools/ecj-3.40.0.jar'
            expected='05cc22a24e7982970f63a405fc6c820bc80b806f27f3c5a6236fc475f8f7152b'
            if not ecj.exists():
                self.log('Downloading Eclipse Java compiler 3.40.0 for app-name lookup (EPL-2.0).')
                with urllib.request.urlopen('https://repo.maven.apache.org/maven2/org/eclipse/jdt/ecj/3.40.0/ecj-3.40.0.jar',timeout=60) as response:
                    data=response.read()
                if hashlib.sha256(data).hexdigest()!=expected:raise RuntimeError('Java compiler checksum mismatch')
                ecj.write_bytes(data)
            if digest(ecj)!=expected:raise RuntimeError('Java compiler checksum mismatch')
            compiler=[java,'-jar',ecj,'-proc:none']
        self.run([*compiler,'-encoding','UTF-8','-source','8','-target','8',
                  '-classpath',platforms[-1],'-d',cache,source])
        self.run([java,'-cp',build/'lib/d8.jar','com.android.tools.r8.D8','--min-api','29',
                  '--lib',platforms[-1],'--output',cache,cache/'QuestAppLabels.class'])
        return dex

    def installed_apps(self, serial):
        packages=self.packages(serial);labels={};icons={}
        if not packages:return []
        remote='/data/local/tmp/q2f-labels-'+uuid.uuid4().hex+'.dex'
        try:
            helper=self.label_helper()
            self.adb_run(serial,'push',helper,remote)
            for offset in range(0,len(packages),50):
                group=[valid_package(p) for p in packages[offset:offset+50]]
                output=self.shell(serial,'env','CLASSPATH='+remote,'app_process','/system/bin','QuestAppLabels',*group)
                for line in output.splitlines():
                    if line.startswith('Q2F_ICON='):
                        try:
                            record=json.loads(line[len('Q2F_ICON='):])
                            if isinstance(record,dict) and record.get('package') in group and isinstance(record.get('icon'),str) and len(record['icon'])<100000:
                                data=base64.b64decode(record['icon'],validate=True)
                                if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>=24 and 0<int.from_bytes(data[16:20],'big')<=96 and 0<int.from_bytes(data[20:24],'big')<=96:
                                    icons[record['package']]=record['icon']
                        except (ValueError,TypeError):pass
                    if line.startswith('Q2F_LABELS='):
                        found=json.loads(line[len('Q2F_LABELS='):])
                        labels.update({p:n for p,n in found.items() if p in group and isinstance(n,str) and n.strip()})
        except Exception as exc:
            self.log('App name lookup unavailable; showing package IDs where necessary: '+str(exc))
        finally:
            try:self.shell(serial,'rm','-f',remote,check=False)
            except (OSError,RuntimeError):pass
        return sorted([dict(package=p,title=labels.get(p,p),**({'icon':icons[p]} if p in icons else {})) for p in packages],key=lambda a:(a['title'].casefold(),a['package']))

    def backup(self, serial, package):
        valid_package(package)
        paths = [x[8:] for x in self.shell(serial, 'pm', 'path', package).splitlines() if x.startswith('package:')]
        if not paths: raise RuntimeError('Package disappeared or Quest is disconnected')
        if len(paths) != 1: raise RuntimeError('This game uses split APKs. This version supports single-APK games only; nothing was changed.')
        folder = ROOT / 'games' / package / time.strftime('backup-%Y%m%d-%H%M%S')
        folder.mkdir(parents=True, exist_ok=False)
        self.log('Backing up APK. Keep the Quest connected until conversion finishes.')
        self.progress('Backup: copying Quest APK')
        self.adb_run(serial, 'pull', paths[0], folder / 'base.apk')
        warnings = []
        for kind in ('obb', 'data'):
            remote = f'/sdcard/Android/{kind}/{package}'
            exists = self.shell(serial, 'sh', '-c', f'test -d {shlex.quote(remote)} && echo PRESENT', check=False)
            if 'PRESENT' not in exists: continue
            target = folder / kind; target.mkdir()
            self.progress('Backup: copying Quest ' + kind)
            result = self.adb_run(serial, 'pull', remote + '/.', target, check=False)
            if 'error:' in result.lower() or 'permission denied' in result.lower():
                if kind == 'obb': raise RuntimeError(f'OBB backup incomplete. Retry with Quest unlocked. Partial backup: {folder}')
                warnings.append('Some external save/cache files were inaccessible. Private Android app data is not backed up.')
        self.progress('Backup: verifying APK')
        write_json(folder / 'backup.json', {'package': package, 'apk_sha256': digest(folder / 'base.apk'), 'warnings': warnings})
        for warning in warnings: self.log('WARNING: ' + warning)
        return folder

    def toolchain(self):
        sdk = Path(os.environ.get('ANDROID_SDK_ROOT') or Path(os.environ['LOCALAPPDATA']) / 'Android/Sdk')
        def pick(pattern):
            found = sorted(sdk.glob(pattern), reverse=True)
            if not found: raise RuntimeError(f'Android SDK tool missing: {sdk / pattern}')
            return found[0]
        java = next((ROOT / 'tools/java21').glob('*/bin/java.exe'), None)
        jar = ROOT / 'tools/ovrport-cli/overportcli-1.2.3-all.jar'
        if not java or not jar.exists(): raise RuntimeError('Keep this app beside the existing tools folder (Java 21 and OVR Port CLI).')
        clangs = sorted(sdk.glob('ndk/*/toolchains/llvm/prebuilt/windows-x86_64/bin/clang.exe'), reverse=True)
        return java, jar, pick('build-tools/*/aapt.exe').parent, clangs[0] if clangs else None

    def open_dispatcher(self, names, output):
        """Build openxr-dispatcher-open from a local checkout; return replacement APK entries."""
        entries = dispatcher_entries(names)
        if not entries: raise RuntimeError('OVR Port output has no libopenxr_loader.so; open dispatcher not applied')
        source = dispatcher_source()
        if not (source / 'src/next.c').exists():
            raise RuntimeError(f'Open-source OpenXR dispatcher not found at {source}. Clone https://github.com/Molier/openxr-dispatcher-open there or set OPENXR_DISPATCHER_SOURCE.')
        clang = self.toolchain()[3]
        libraries = {}
        for abi in sorted(set(entries.values())):
            if abi not in DISPATCHER_TARGETS: raise RuntimeError(f'Open-source OpenXR dispatcher does not support {abi}; nothing was replaced.')
            library = Path(output) / 'open-dispatcher' / abi / 'libopenxr_loader.so'
            library.parent.mkdir(parents=True, exist_ok=True)
            if clang:
                # Flags of its CMakeLists.txt plus the NDK toolchain defaults; no CMake or Ninja needed.
                self.run([clang, *DISPATCHER_TARGETS[abi], f'--sysroot={clang.parent.parent / "sysroot"}',
                          '-shared', '-fPIC', '-O2', '-std=gnu11', '-fvisibility=hidden', '-DANDROID', '-DNDEBUG',
                          '-ffunction-sections', '-fdata-sections', '-fstack-protector-strong', '-D_FORTIFY_SOURCE=2',
                          '-Wall', '-Wextra', '-Wno-unused-parameter', '-Wno-format-truncation',
                          '-I', source / 'vendor', '-I', source / 'src',
                          *sorted((source / 'src').glob('*.c')), source / 'src/third_party/cJSON.c',
                          '-Wl,-soname,libopenxr_loader.so', '-Wl,-Bsymbolic', '-Wl,--no-undefined', '-Wl,--gc-sections',
                          '-Wl,--build-id=sha1', '-Wl,-z,max-page-size=16384', '-Wl,-s', '-llog', '-ldl', '-lm', '-o', library])
            else:
                prebuilt = source / 'prebuilt' / abi / 'libopenxr_loader.so'
                if not prebuilt.exists(): raise RuntimeError(f'Android NDK compiler missing and no {prebuilt}; run its build.sh first.')
                shutil.copy2(prebuilt, library)
            data = library.read_bytes()
            if not data.startswith(b'\x7fELF') or DISPATCHER_MARKER not in data:
                raise RuntimeError(f'{library} is not an openxr-dispatcher-open build')
            libraries[abi] = data
        replacements = {}
        for name, abi in entries.items():
            data = libraries[abi]
            if name.endswith('/libq2f_original.so'):
                from build_passthrough import rename_soname
                data = rename_soname(data)
            replacements[name] = data
            self.log(f'Open-source OpenXR dispatcher replaces {name} (sha256 {hashlib.sha256(data).hexdigest()[:16]})')
        return replacements

    def convert(self, backup, title, scale=100, controllers=True, foveation=True, steam_id='', refresh_rate=0, headset='Default', rect_fix=False, open_dispatcher=False):
        self.progress('Conversion 1/7: inspecting APK')
        refresh_rate = refresh_value(refresh_rate)
        headset = headset_value(headset)
        check_dispatcher_headset(open_dispatcher, headset)
        backup = Path(backup)
        java, jar, build, clang = self.toolchain()
        source_apk = backup / 'base.apk'
        if not source_apk.exists(): source_apk = backup / 'apk/base.apk'
        badging = self.run([build / 'aapt.exe', 'dump', 'badging', source_apk])
        package = valid_package(re.search(r"package: name='([^']+)'", badging)[1])
        with zipfile.ZipFile(source_apk) as apk:
            if not any(n.startswith('lib/arm64-v8a/') for n in apk.namelist()):
                raise RuntimeError('Game has no ARM64 libraries; this conversion profile does not support it.')
        if steam_id and not str(steam_id).isdigit(): raise ValueError('Steam artwork App ID must contain digits only')
        folder = ROOT / 'games' / package / time.strftime('build-%Y%m%d-%H%M%S')
        folder.mkdir(parents=True, exist_ok=False)
        workspace = ROOT / 'tools/ovrport-workspace'
        self.log('Converting with pinned OVR Port ' + LIB_VERSION)
        self.progress('Conversion 2/7: OVR Port patching (percentage unavailable)')
        self.run([java, '-jar', jar, 'patch', f'--input={source_apk}', f'--output={folder}',
                  '--output-name=ovrport.apk', f'--workspace={workspace}', f'--version={LIB_VERSION}'])
        self.log('Building Frame compatibility adapter and resolution control…')
        self.progress('Conversion 3/7: preparing Frame adapter')
        library = folder / 'libopenxr_loader_generic.so'
        prebuilt = ASSETS / 'native/libopenxr_loader_generic.so'
        if prebuilt.exists():
            if b'Q2F_REFRESH_RATE_V1' not in prebuilt.read_bytes():
                raise RuntimeError('Bundled adapter is outdated. Run build-native.ps1 and retry conversion.')
            shutil.copy2(prebuilt, library)
        else:
            if not clang: raise RuntimeError('Missing bundled adapter and Android NDK compiler')
            include = ASSETS / 'native/include'
            if not (include / 'openxr/openxr.h').exists():
                raise RuntimeError('Run build-native.ps1 to obtain verified OpenXR headers and build the adapter.')
            self.run([clang, '--target=aarch64-linux-android29', f'--sysroot={clang.parent.parent / "sysroot"}',
                      '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-Werror', '-Wl,-Bsymbolic',
                      '-Wl,-soname,libopenxr_loader_generic.so', '-I', include,
                      ASSETS / 'native/frame_bridge.c', '-ldl', '-llog', '-o', library])
        prefix = 'lib/arm64-v8a/'
        original = prefix + 'libopenxr_loader_generic.so'
        with zipfile.ZipFile(folder / 'ovrport.apk') as src, zipfile.ZipFile(folder / 'unsigned.apk', 'w', zipfile.ZIP_DEFLATED) as dst:
            if original not in src.namelist(): raise RuntimeError('OVR Port output has no generic loader; incompatible game/profile')
            dispatchers = {}
            if open_dispatcher:
                self.progress('Conversion 3/7: building open-source OpenXR dispatcher')
                dispatchers = self.open_dispatcher(src.namelist(), folder)
            total = sum(item.file_size for item in src.infolist() if item.filename != original and not item.filename.startswith('META-INF/'))
            completed = 0
            self.progress('Conversion 4/7: repacking APK', 0)
            for item in src.infolist():
                if item.filename == original or item.filename.startswith('META-INF/'): continue
                dst.writestr(item, dispatchers[item.filename] if item.filename in dispatchers else src.read(item.filename))
                completed += item.file_size
                self.progress('Conversion 4/7: repacking APK', 100 * completed / max(total,1))
            dst.writestr(prefix + 'libopenxr_loader_original.so', src.read(original))
            dst.write(library, original)
            dst.writestr(prefix + 'libframe_settings.so', settings_text(scale, controllers, foveation, refresh_rate))
        self.progress('Conversion 5/7: aligning APK')
        self.run([build / 'zipalign.exe', '-f', '-P', '16', '4', folder / 'unsigned.apk', folder / 'aligned.apk'])
        key = workspace / 'signatures' / (package + '.keystore')
        self.progress('Conversion 6/7: signing APK')
        self.run([java, '-jar', build / 'lib/apksigner.jar', 'sign', '--ks', key, '--ks-pass', 'pass:password', '--out', folder / 'game.apk', folder / 'aligned.apk'])
        self.progress('Conversion 7/7: verifying signature and checksum')
        self.run([java, '-jar', build / 'lib/apksigner.jar', 'verify', '--verbose', folder / 'game.apk'])
        label = re.search(r"application-label:'([^']+)'", badging)
        manifest = {'package': package, 'title': title.strip() or (label[1] if label else package),
                    'backup': str(backup.resolve()), 'scale': float(scale), 'controllers': controllers,
                    'foveation': foveation, 'steam_id': str(steam_id), 'ovrport': LIB_VERSION, 'refresh_rate': refresh_rate,
                    'sha256': digest(folder / 'game.apk')}
        if open_dispatcher: manifest.update(open_dispatcher=True, built_open_dispatcher=True)
        write_json(folder / 'game.json', manifest)
        if headset != 'Default' or rect_fix:
            return self.rebuild_apk(folder, title, scale, controllers, foveation, steam_id, refresh_rate, headset, rect_fix, open_dispatcher)
        self.progress('Conversion complete', 100)
        self.log('Conversion complete. Wake Frame for Wi-Fi installation, or connect it by USB, then press Install.')
        return folder

    def rebuild_apk(self, folder, title, scale=100, controllers=True, foveation=True,
                    steam_id='', refresh_rate=0, headset='Default', rect_fix=None, open_dispatcher=None):
        """Repack a converted APK; reuse OBBs and preserve game-specific native fixes."""
        folder = Path(folder)
        game = json.loads((folder/'game.json').read_text())
        rect_fix = rect_fix_value(game) if rect_fix is None else bool(rect_fix)
        open_dispatcher = open_dispatcher_value(game) if open_dispatcher is None else bool(open_dispatcher)
        headset = headset_value(headset)
        check_dispatcher_headset(open_dispatcher, headset)
        config = settings_text(scale, controllers, foveation, refresh_rate)
        if digest(folder/'game.apk') != game['sha256']:
            raise ValueError('Source APK checksum mismatch; rebuild refused')
        baseline = Path(game.get('identity_base_apk', folder/'game.apk'))
        baseline_hash = game.get('identity_base_sha256', game['sha256'])
        if digest(baseline) != baseline_hash:
            raise ValueError('Original identity baseline changed; rebuild refused')
        java, jar, build, _ = self.toolchain()
        package = valid_package(game['package'])
        output = ROOT/'games'/package/('rebuild-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
        output.mkdir(parents=True, exist_ok=False)
        prefix = 'lib/arm64-v8a/'
        self.progress('APK rebuild: inspecting existing compatibility fixes')
        with zipfile.ZipFile(baseline) as src:
            wrapped = prefix+'libq2f_original.so' in src.namelist()
            if wrapped and rect_fix_value(game) and not rect_fix:
                raise ValueError('This original build already includes the black-screen fix. Open a build from before that fix to disable it.')
            runtime = prefix+('libq2f_original.so' if prefix+'libq2f_original.so' in src.namelist() else 'libopenxr_loader.so')
            replacements = {runtime: patch_runtime(src.read(runtime), headset),
                            prefix+'libframe_settings.so': config.encode()}
            has_open = DISPATCHER_MARKER in src.read(runtime)
            if has_open and not open_dispatcher:
                raise ValueError("This original build already includes the open-source OpenXR dispatcher. Reconvert to use OVR Port's dispatcher.")
            if open_dispatcher and not has_open:
                self.progress('APK rebuild: building open-source OpenXR dispatcher')
                replacements.update(self.open_dispatcher(src.namelist(), output))
            existing = src.read(prefix+'libopenxr_loader_generic.so')
            if refresh_value(refresh_rate) and b'Q2F_REFRESH_RATE_V1' not in existing:
                # Never silently replace a custom native fix to obtain a new capability.
                raise ValueError('This build lacks refresh-rate support. Reconvert first; its custom adapter was not replaced.')
            if headset == 'Quest 3':
                for name in src.namelist():
                    if not re.fullmatch(r'classes\d*\.dex', name): continue
                    raw = src.read(name)
                    if b'Quest 2' not in raw or (b'com/epicgames/ue4/GameActivity' not in raw and b'com/epicgames/unreal/GameActivity' not in raw): continue
                    self.progress('APK rebuild: updating Unreal headset model')
                    dex = output/name; dex.write_bytes(raw)
                    smali = output/(name+'.smali')
                    self.run([java, '-cp', jar, 'org.jf.baksmali.Main', 'disassemble', dex, '-o', smali])
                    changed = False
                    for relative in ('com/epicgames/ue4/GameActivity.smali', 'com/epicgames/unreal/GameActivity.smali'):
                        path = smali/relative
                        if not path.exists(): continue
                        text = path.read_text(encoding='utf-8'); patched = patch_unreal_smali(text)
                        if text != patched:
                            path.write_text(patched, encoding='utf-8'); changed = True
                    if changed:
                        rebuilt = output/('rebuilt-'+name)
                        self.run([java, '-cp', jar, 'org.jf.smali.Main', 'assemble', smali, '-o', rebuilt])
                        replacements[name] = rebuilt.read_bytes()
            self.progress('APK rebuild: repacking (OBBs untouched)', 0)
            with zipfile.ZipFile(output/'unsigned.apk', 'w', zipfile.ZIP_DEFLATED) as dst:
                entries = [i for i in src.infolist() if not i.filename.startswith('META-INF/')]
                for index, item in enumerate(entries):
                    dst.writestr(item, replacements.pop(item.filename, None) if item.filename in replacements else src.read(item.filename))
                    self.progress('APK rebuild: repacking (OBBs untouched)', 100*(index+1)/len(entries))
                for name, data in replacements.items(): dst.writestr(name, data)
        self.progress('APK rebuild: aligning and signing')
        self.run([build/'zipalign.exe', '-f', '-P', '16', '4', output/'unsigned.apk', output/'aligned.apk'])
        key = ROOT/'tools/ovrport-workspace/signatures'/(package+'.keystore')
        self.run([java, '-jar', build/'lib/apksigner.jar', 'sign', '--ks', key, '--ks-pass', 'pass:password', '--out', output/'game.apk', output/'aligned.apk'])
        self.run([java, '-jar', build/'lib/apksigner.jar', 'verify', '--verbose', output/'game.apk'])
        if rect_fix and not wrapped:
            from build_passthrough import build as build_rect_adapter
            self.progress('APK rebuild: adding black-screen compatibility fix')
            fixed = output/'rect-adapter'
            build_rect_adapter(output/'game.apk', fixed, bridge=self)
            shutil.copy2(fixed/'game.apk', output/'game.apk')
        game.update(title=title.strip() or game['title'], scale=float(scale), controllers=bool(controllers),
                    foveation=bool(foveation), steam_id=str(steam_id), refresh_rate=refresh_value(refresh_rate),
                    headset=headset, built_headset=headset, sha256=digest(output/'game.apk'),
                    rect_fix=rect_fix, built_rect_fix=rect_fix,
                    identity_base_apk=str(baseline.resolve()), identity_base_sha256=baseline_hash,
                    update_source_sha256=game.get('update_source_sha256', game['sha256']))
        if open_dispatcher: game.update(open_dispatcher=True, built_open_dispatcher=True)
        else:
            for key in ('open_dispatcher', 'built_open_dispatcher'): game.pop(key, None)
        write_json(output/'game.json', game)
        if (folder/'deployment.json').exists(): shutil.copy2(folder/'deployment.json', output/'deployment.json')
        self.progress('APK-only rebuild complete; OBBs unchanged', 100)
        return output

    def install_apk_only(self, folder, client, transport='wifi'):
        return self._install_apk_only(folder,client,transport)

    def _install_apk_only(self, folder, client, transport='wifi'):
        """Update one existing deployment, without traversing/copying OBBs or saves."""
        if transport not in ('usb', 'wifi'): raise ValueError('Select USB or Wi-Fi installation')
        folder = Path(folder); game = json.loads((folder/'game.json').read_text())
        check_headset_build(game); check_rect_build(game); check_dispatcher_build(game); check_refresh_adapter(folder, game.get('refresh_rate', 0))
        package = valid_package(game['package'])
        deployment = json.loads((folder/'deployment.json').read_text())
        if deployment['package'] != package: raise ValueError('Deployment/package mismatch')
        if digest(folder/'game.apk') != game['sha256']: raise ValueError('APK checksum mismatch')
        if transport == 'usb':
            if 'frame' not in self.devices(): raise ValueError('Connect Frame by USB or select Wi-Fi')
            if self.shell('frame','cat','/etc/machine-id').strip() != self.remote(client,'cat /etc/machine-id'):
                raise ValueError('USB Frame and SSH Frame do not match')
        base = self.deployment_base(client, deployment)
        anchor = deployment.get('anchor', deployment['remote'])
        q = shlex.quote
        # Resolve UUID again through deployment_base; never assume the SD label.
        actual = json.loads(self.remote(client, 'cat '+q(anchor+'/deployment.json')))
        if actual.get('package') != package or actual.get('appid') != deployment['appid'] or actual.get('storage_uuid','') != deployment.get('storage_uuid',''):
            raise ValueError('Installed deployment changed; reopen its current build')
        apk = base+'/lepton-app/game.apk'
        expected = game.get('update_source_sha256', game['sha256'])
        def preflight():
            running = self.remote(client, 'podman ps --format "{{.Names}}"')
            if 'lepton-steamlaunch-'+str(int(deployment['appid'])) in running.splitlines():
                raise ValueError('Close this game on Frame before updating its APK')
            if self.remote(client, 'sha256sum '+q(apk)).split()[0] != expected:
                raise ValueError('Installed APK differs from this rebuild source. Refusing to overwrite other compatibility fixes.')
        preflight()
        stage = apk+'.staging-'+uuid.uuid4().hex
        previous = base+'/previous-apk-'+uuid.uuid4().hex+'.apk'
        self.progress('APK-only installation: uploading APK; OBBs and saves untouched')
        self.transfer_file(client, folder/'game.apk', stage, transport)
        if self.remote(client, 'sha256sum '+q(stage)).split()[0] != game['sha256']:
            raise ValueError('APK verification failed; existing APK untouched')
        preflight()
        # Keep the old APK recoverable. Atomic rename exposes only a complete APK.
        self.remote(client, 'set -e; cp -p --reflink=auto '+q(apk)+' '+q(previous)+'; chmod 644 '+q(stage)+'; mv -f '+q(stage)+' '+q(apk))
        config = settings_text(game['scale'], game['controllers'], game['foveation'], game.get('refresh_rate',0))
        self.put_text(client, base+'/settings.conf', config)
        self.put_text(client, base+'/lepton-data/external/Android/data/'+package+'/files/framebridge.conf', config)
        game.pop('update_source_sha256', None)
        write_json(folder/'game.json', game)
        self.progress('APK updated; OBBs and saves unchanged', 100)
        self.log('Previous APK retained at '+previous+'. Restart the game to test the new settings.')

    def connect(self, host, username, password, port=22):
        """Trust an SSH key only after comparing it to the physically connected Frame."""
        if not host.strip() or not username.strip(): raise ValueError('Enter the Frame SSH host and username')
        client = paramiko.SSHClient()
        known = APP / 'known_hosts'
        if known.exists(): client.load_host_keys(str(known))
        key_host = host if int(port) == 22 else f'[{host}]:{port}'
        if not client.get_host_keys().lookup(key_host):
            if 'frame' not in self.devices():
                raise RuntimeError('First SSH connection: plug in Frame by USB so its host key can be verified securely.')
            key = self.shell('frame', 'cat', '/etc/ssh/ssh_host_ed25519_key.pub').strip().split()
            if len(key) < 2 or key[0] != 'ssh-ed25519': raise RuntimeError('Could not read Frame SSH host key')
            client.get_host_keys().add(key_host, key[0], paramiko.Ed25519Key(data=base64.b64decode(key[1])))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(host.strip(), port=int(port), username=username.strip(), password=password,
                       look_for_keys=False, allow_agent=False, timeout=10, auth_timeout=15)
        client.save_host_keys(str(known))
        self.log('SSH identity and authentication verified.')
        return client

    def remote(self, client, command, check=True):
        _, out, err = client.exec_command(command, timeout=60)
        output = out.read().decode('utf-8', 'replace') + err.read().decode('utf-8', 'replace')
        code = out.channel.recv_exit_status()
        if output.strip(): self.log(output.strip())
        if check and code: raise RuntimeError(f'Frame command failed ({code}): {output[-2000:]}')
        return output.strip()

    def put_text(self, client, remote, text):
        with client.open_sftp() as sftp:
            with sftp.file(remote + '.tmp', 'w') as f: f.write(text.encode('utf-8'))
            sftp.posix_rename(remote + '.tmp', remote)

    def artwork(self, steam_id, folder):
        if not str(steam_id).isdigit(): raise ValueError('Steam App ID must be numeric')
        folder = Path(folder); folder.mkdir(exist_ok=True)
        assets = {'portrait': 'library_600x900.jpg', 'landscape': 'header.jpg', 'hero': 'library_hero.jpg', 'logo': 'logo.png'}
        result = {}
        for kind, filename in assets.items():
            path = folder / filename
            try:
                url = f'https://cdn.akamai.steamstatic.com/steam/apps/{steam_id}/{filename}'
                with urllib.request.urlopen(url, timeout=30) as response: data = response.read()
                if not (data.startswith(b'\x89PNG') or data.startswith(b'\xff\xd8')): raise ValueError('Not an image')
                path.write_bytes(data); result[kind] = path
            except Exception as exc: self.log(f'Artwork {kind} unavailable: {exc}')
        return result

    def storage_locations(self, client):
        return json.loads(self.remote(client,'python3 -c '+shlex.quote(PROBE)))

    def manage_game(self, client, action, **values):
        request=dict(action=action,mounts=self.storage_locations(client),**values)
        code=(ASSETS/'manage_remote.py').read_text(encoding='utf-8')+'\nmain(json.loads('+repr(json.dumps(request))+'))\n'
        return json.loads(self.remote(client,'python3 -c '+shlex.quote(code)))

    def installed_games(self, client):
        return self.manage_game(client,'list')

    def uninstall(self, client, package, keep_saves=True, expected=None):
        package=valid_package(package)
        game=self.manage_game(client,'check',package=package)
        if expected is not None and game!={k:v for k,v in expected.items() if k!='error'}:
            raise RuntimeError('Installation changed since refresh. Refresh and review it again.')
        running=self.remote(client,'podman ps --format "{{.Names}}"').splitlines()
        if 'lepton-steamlaunch-'+str(game['appid']) in running:
            raise RuntimeError('Close the game before uninstalling it.')
        home=self.remote(client,'printf %s "$HOME"')
        with client.open_sftp() as sftp:
            users=[n for n in sftp.listdir(home+'/.local/share/Steam/userdata') if n!='0' and n.isdigit()]
        if len(users)!=1:raise RuntimeError('Expected exactly one Steam user; no files removed.')
        path=home+'/.local/share/Steam/userdata/'+users[0]+'/config/shortcuts.vdf'
        service=self.remote(client,'systemctl --user is-active steam.service',check=False)=='active'
        try:
            if service:self.remote(client,'systemctl --user stop steam.service')
            else:self.remote(client,'steam -shutdown >/dev/null 2>&1',check=False)
            for _ in range(40):
                if not self.remote(client,'pgrep -x steam',check=False):break
                time.sleep(1)
            else:raise RuntimeError('Steam did not close; no files removed.')
            with client.open_sftp() as sftp:
                try:
                    with sftp.file(path,'rb') as stream:original=stream.read()
                except FileNotFoundError:original=b''
                updated=remove_shortcut(original,'"'+game['anchor']+'/launch.sh"',game['appid'])
                # Validate the VDF before removing any game data; retain a restore copy.
                if original:
                    with sftp.file(path+'.backup-uninstall-'+str(time.time_ns()),'wb') as stream:stream.write(original)
                result=self.manage_game(client,'delete',package=package,expected=game,keep_saves=bool(keep_saves))
                if original!=updated:
                    with sftp.file(path+'.quest-frame.tmp','wb') as stream:stream.write(updated)
                    sftp.posix_rename(path+'.quest-frame.tmp',path)
            self.log('Uninstalled '+package)
            self.log('Saves preserved at '+result['saves'] if result['saves'] else 'No save backup retained on Frame.')
            return result
        finally:
            if service:self.remote(client,'systemctl --user start steam.service',check=False)
            else:self.remote(client,'systemd-run --user --collect /usr/bin/steam',check=False)

    def deployment_base(self, client, deployment):
        uuid=deployment.get('storage_uuid','')
        if not uuid:return deployment['remote']
        choice=select_storage(self.storage_locations(client),'uuid:'+uuid)
        return choice['mount']+'/Applications/quest-frame/'+valid_package(deployment['package'])

    def transfer_file(self, client, local, remote, transport):
        stage = 'Installing: ' + Path(local).name
        self.progress(stage)
        if transport == 'usb':
            self.adb_run('frame', 'push', '-Z', local, remote)
        elif transport == 'wifi':
            self.log('Uploading over SSH: ' + Path(local).name)
            last = [0.0]
            def transferred(done, total):
                now = time.monotonic()
                if done == total or now - last[0] >= .1:
                    last[0] = now
                    self.progress(f'{stage} ({done/1048576:.1f} / {total/1048576:.1f} MiB)', 100 * done / total if total else 100)
            with client.open_sftp() as sftp:
                sftp.put(str(local), remote, callback=transferred)
        else:
            raise ValueError('Select USB or Wi-Fi installation')
        self.progress(stage, 100)

    def install(self, folder, client, add_steam=True, artwork_id=None, storage_id='internal', transport='usb'):
        return self._install(folder,client,add_steam,artwork_id,storage_id,transport)

    def _install(self, folder, client, add_steam=True, artwork_id=None, storage_id='internal', transport='usb'):
        self.progress('Installation: checking APK, connection and storage')
        if transport not in ('usb', 'wifi'): raise ValueError('Select USB or Wi-Fi installation')
        folder = Path(folder); game = json.loads((folder / 'game.json').read_text())
        check_headset_build(game)
        check_rect_build(game)
        check_dispatcher_build(game)
        check_refresh_adapter(folder, game.get('refresh_rate', 0))
        package = valid_package(game['package']); backup = Path(game['backup'])
        obb_source = backup / 'obb'
        data_source = backup / 'data'
        if not obb_source.exists(): obb_source = backup / 'Android/obb' / package
        if not data_source.exists(): data_source = backup / 'Android/data' / package
        if digest(folder / 'game.apk') != game['sha256']: raise RuntimeError('APK checksum differs from build manifest')
        if transport == 'usb':
            if 'frame' not in self.devices(): raise RuntimeError('Plug in Frame by USB or select Wi-Fi installation.')
            # Verify SSH and USB point to the same physical device.
            usb_key = self.shell('frame', 'cat', '/etc/machine-id').strip()
            ssh_key = self.remote(client, 'cat /etc/machine-id')
            if usb_key != ssh_key: raise RuntimeError('USB Frame and SSH Frame do not match')
        home = self.remote(client, 'printf %s "$HOME"')
        storage=select_storage(self.storage_locations(client),storage_id)
        anchor=home+'/Applications/quest-frame/'+package
        base = storage['mount'] + '/Applications/quest-frame/' + package
        q = shlex.quote
        launcher = anchor + '/launch.sh'; exe = '"' + launcher + '"'
        ident = app_id(exe, game['title'])
        # Existing IDs must survive title changes; use the previous deployment metadata.
        old = self.remote(client, f'cat {q(anchor + "/deployment.json")}', check=False)
        try: old_deployment=json.loads(old)
        except ValueError: old_deployment={}
        if old_deployment and old_deployment.get('storage_uuid','')!=storage['uuid']:
            raise RuntimeError('Game already installed on a different storage device. Choose its existing location; moving existing games/saves is not supported automatically.')
        if old_deployment.get('appid'):ident=int(old_deployment['appid'])
        if storage['uuid'] and not old_deployment:
            if self.remote(client,'test -d '+q(anchor+'/lepton-app')+' && echo EXISTS',check=False):
                raise RuntimeError('Existing internal game found. Select internal storage to preserve its saves.')
        running = self.remote(client, f'podman ps --format "{{{{.Names}}}}"', check=False)
        if f'lepton-steamlaunch-{ident}' in running.splitlines():
            raise RuntimeError('Close this game on Frame before updating it. Other games will not be stopped.')
        lepton = home + '/.local/share/Steam/steamapps/common/Lepton/lepton'
        self.remote(client, f'test -x {q(lepton)}')
        required = (folder / 'game.apk').stat().st_size + sum(p.stat().st_size for p in obb_source.rglob('*') if p.is_file())
        required += sum(p.stat().st_size for p in data_source.rglob('*') if p.is_file())
        if storage['free'] < required*2 + 512*1024**2: raise RuntimeError('Not enough free space on selected storage for safe staged deployment')
        stamp = time.strftime('%Y%m%d-%H%M%S')
        stage = base + '/staging-' + stamp
        if storage['uuid']:
            self.remote(client,'mountpoint -q -- '+q(storage['mount']))
        self.remote(client, f'mkdir -p {q(anchor)} {q(stage + "/obb")} {q(base + "/lepton-data/external/Android/data/" + package + "/files")}')
        self.log('Installing over '+('Wi-Fi SSH/SFTP. Keep Frame awake and on the network.' if transport=='wifi' else 'USB ADB. Keep Frame connected.'))
        self.transfer_file(client, folder / 'game.apk', stage + '/game.apk', transport)
        for file in obb_source.rglob('*'):
            if file.is_file():
                relative = file.relative_to(obb_source).as_posix()
                dest = stage + '/obb/' + relative
                self.remote(client, 'mkdir -p ' + q(str(PurePosixPath(dest).parent)))
                self.transfer_file(client, file, dest, transport)
                self.progress('Installation: verifying ' + file.name)
                actual = self.remote(client, 'sha256sum ' + q(dest)).split()[0]
                if actual != digest(file): raise RuntimeError('OBB verification failed; previous installation untouched')
        self.progress('Installation: verifying transferred APK')
        if self.remote(client, 'sha256sum ' + q(stage + '/game.apk')).split()[0] != game['sha256']:
            raise RuntimeError('APK transfer verification failed; previous installation untouched')
        # Never overwrite Frame progress with an older Quest save on a repeat install.
        data_root = base + '/lepton-data/external/Android/data/' + package
        for file in data_source.rglob('*'):
            if not file.is_file(): continue
            relative = file.relative_to(data_source).as_posix()
            if relative == 'files/framebridge.conf': continue
            destination = data_root + '/' + relative
            self.progress('Installation: checking existing save ' + file.name)
            exists = self.remote(client, f'if test -e {q(destination)}; then echo EXISTS; fi')
            if exists: continue
            self.remote(client, 'mkdir -p ' + q(str(PurePosixPath(destination).parent)))
            self.transfer_file(client, file, destination, transport)
        self.progress('Installation: applying settings and preserving previous installation')
        self.put_text(client, data_root + '/files/framebridge.conf', settings_text(game['scale'], game['controllers'], game['foveation'], game.get('refresh_rate', 0)))
        self.put_text(client, base + '/settings.conf', settings_text(game['scale'], game['controllers'], game['foveation'], game.get('refresh_rate', 0)))
        # Preserve all prior application and baked private data on updates.
        self.remote(client, f'if test -d {q(base + "/lepton-app")}; then mv {q(base + "/lepton-app")} {q(base + "/previous-app-" + stamp)}; fi; mv {q(stage)} {q(base + "/lepton-app")}')
        self.remote(client, f'if test -d {q(base + "/lepton-data/baked")}; then cp -a --reflink=auto {q(base + "/lepton-data/baked")} {q(base + "/previous-baked-" + stamp)}; fi')
        script = launcher_text(base, ident, lepton,card_launcher(storage['uuid'],package) if storage['uuid'] else '')
        self.put_text(client, launcher, script)
        self.remote(client, 'chmod 755 ' + q(launcher))
        deployment={'remote':base,'anchor':anchor,'appid':ident,'package':package,'storage_uuid':storage['uuid']}
        write_json(folder / 'deployment.json',deployment)
        self.put_text(client, anchor + '/deployment.json', json.dumps(deployment))
        if add_steam:
            self.progress('Installation: artwork and Steam shortcut (Steam may restart)')
            arts = self.artwork(artwork_id or game['steam_id'], folder / 'artwork') if (artwork_id or game['steam_id']) else {}
            self.add_shortcut(client, anchor, game['title'], ident, arts)
        self.log('Installed. Launch from Steam; Lepton initializes the dedicated Android container on first launch.')
        self.progress('Installation complete', 100)
        return base, ident

    def add_shortcut(self, client, base, title, expected_id, arts):
        q = shlex.quote
        home = self.remote(client, 'printf %s "$HOME"')
        steam = home + '/.local/share/Steam'
        with client.open_sftp() as sftp:
            users = [n for n in sftp.listdir(steam + '/userdata') if n != '0' and n.isdigit()]
            if len(users) != 1: raise RuntimeError('Multiple/no Steam users found; game installed, but select a Steam account manually before adding its shortcut.')
        config = steam + '/userdata/' + users[0] + '/config'
        self.log('Closing Steam briefly to update its library safely. It will restart afterward.')
        service = self.remote(client, 'systemctl --user is-active steam.service', check=False) == 'active'
        if service: self.remote(client, 'systemctl --user stop steam.service')
        else: self.remote(client, 'steam -shutdown >/dev/null 2>&1', check=False)
        for _ in range(40):
            if not self.remote(client, 'pgrep -x steam', check=False): break
            time.sleep(1)
        else:
            if service: self.remote(client, 'systemctl --user start steam.service', check=False)
            raise RuntimeError('Steam did not close; library not modified. Close it and retry.')
        try:
            self.remote(client, 'mkdir -p ' + q(config + '/grid'))
            with client.open_sftp() as sftp:
                path = config + '/shortcuts.vdf'
                try:
                    with sftp.file(path, 'rb') as f: original = f.read()
                except FileNotFoundError: original = b''
                icon = base + '/artwork/' + arts.get('logo', arts.get('landscape', Path(''))).name if arts else ''
                updated, ident = upsert(original, '"' + base + '/launch.sh"', title, base, icon)
                if ident != expected_id: raise RuntimeError('Existing Steam shortcut ID differs from launcher; library not modified')
                if original:
                    with sftp.file(path + '.backup-' + time.strftime('%Y%m%d-%H%M%S'), 'wb') as f: f.write(original)
                with sftp.file(path + '.quest-frame.tmp', 'wb') as f: f.write(updated)
                sftp.posix_rename(path + '.quest-frame.tmp', path)
                self.remote(client, 'mkdir -p ' + q(base + '/artwork'))
                suffixes = {'portrait': 'p', 'landscape': '', 'hero': '_hero', 'logo': '_logo'}
                for kind, local in arts.items():
                    dest = config + '/grid/' + str(ident) + suffixes[kind] + local.suffix
                    try: sftp.stat(dest)
                    except FileNotFoundError: pass
                    else: sftp.posix_rename(dest, dest + '.backup-' + time.strftime('%Y%m%d-%H%M%S'))
                    sftp.put(str(local), dest)
                    sftp.put(str(local), base + '/artwork/' + local.name)
            self.log(f'Steam VR shortcut added: {title} ({ident}); {len(arts)} artwork assets.')
        finally:
            if service: self.remote(client, 'systemctl --user start steam.service', check=False)
            else: self.remote(client, 'systemd-run --user --collect --unit=quest-frame-steam-' + str(int(time.time())) + ' /usr/bin/steam', check=False)

    def update_settings(self, folder, client, scale, controllers, foveation, refresh_rate=0, headset=None, rect_fix=None, open_dispatcher=None):
        folder = Path(folder)
        check_refresh_adapter(folder, refresh_rate)
        if headset is not None:
            check_headset_build(json.loads((folder/'game.json').read_text()), headset)
        if rect_fix is not None:
            check_rect_build(json.loads((folder/'game.json').read_text()), rect_fix)
        if open_dispatcher is not None:
            check_dispatcher_build(json.loads((folder/'game.json').read_text()), open_dispatcher)
        deployment = json.loads((folder / 'deployment.json').read_text())
        package = valid_package(deployment['package'])
        base=self.deployment_base(client,deployment)
        path = base + '/lepton-data/external/Android/data/' + package + '/files/framebridge.conf'
        self.put_text(client, path, settings_text(scale, controllers, foveation, refresh_rate))
        self.put_text(client, base + '/settings.conf', settings_text(scale, controllers, foveation, refresh_rate))
        game = json.loads((folder / 'game.json').read_text())
        game.update(scale=float(scale), controllers=bool(controllers), foveation=bool(foveation), refresh_rate=refresh_value(refresh_rate))
        write_json(folder / 'game.json', game)
        self.log('Resolution/settings saved. Restart the game to apply. No APK rebuild is needed.')
