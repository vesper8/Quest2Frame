# SPDX-License-Identifier: GPL-3.0-only
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from bridge import (Bridge, DISPATCHER_MARKER, check_dispatcher_build, check_dispatcher_headset,
                    digest, dispatcher_entries, open_dispatcher_value)
import test_app

PREFIX = 'lib/arm64-v8a/'
CLOSED = b'\x7fELF closed OVR Port dispatcher'
OTHER = {PREFIX+'libopenxr_loader_generic.so': b'\x7fELF OVR Port generic loader',
         PREFIX+'libopenxr_loader_pico.so': b'\x7fELF vendor loader',
         PREFIX+'liboverport.config.so': b'{"version": 1}',
         PREFIX+'libgame.so': b'\x7fELF game', 'assets/game-data': b'keep me'}


def fake_run(output_root):
    """Stand in for SDK/NDK tools; the clang call emits a fake dispatcher per target."""
    calls = []
    def run(args, check=True):
        args = [str(a) for a in args]; calls.append(args)
        if args[0].endswith('aapt.exe'): return "package: name='com.test.game'\napplication-label:'Test'\n"
        if 'patch' in args:
            out = Path(next(a for a in args if a.startswith('--output=')).split('=', 1)[1])
            with zipfile.ZipFile(out/'ovrport.apk', 'w') as apk:
                apk.writestr(PREFIX+'libopenxr_loader.so', CLOSED)
                for name, data in OTHER.items(): apk.writestr(name, data)
        if args[0].endswith('clang.exe'):
            target = next(a for a in args if a.startswith('--target='))
            Path(args[args.index('-o')+1]).write_bytes(b'\x7fELF open '+target.encode()+b' '+DISPATCHER_MARKER)
        if args[0].endswith('zipalign.exe'): Path(args[-1]).write_bytes(Path(args[-2]).read_bytes())
        if 'sign' in args: Path(args[args.index('--out')+1]).write_bytes(Path(args[-1]).read_bytes())
        return ''
    return run, calls


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.source = self.root/'openxr-dispatcher-open'
        (self.source/'src/third_party').mkdir(parents=True)
        for name in ('next.c', 'instance.c', 'third_party/cJSON.c'): (self.source/'src'/name).write_text('')
        (self.root/'ndk/bin').mkdir(parents=True)
        self.clang = self.root/'ndk/bin/clang.exe'
        self.bridge = Bridge(lambda _: None)
        self.run, self.calls = fake_run(self.root)
        self.patches = [patch.dict(os.environ, {'OPENXR_DISPATCHER_SOURCE': str(self.source)}),
                        patch('bridge.ROOT', self.root),
                        patch.object(self.bridge, 'toolchain', return_value=(self.root/'java', self.root/'jar', self.root, self.clang)),
                        patch.object(self.bridge, 'run', side_effect=self.run)]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.tmp.cleanup()

    def test_entries_only_dispatchers(self):
        names = [PREFIX+'libopenxr_loader.so', 'lib/armeabi-v7a/libopenxr_loader.so', *OTHER]
        self.assertEqual(dispatcher_entries(names), {PREFIX+'libopenxr_loader.so': 'arm64-v8a',
                                                     'lib/armeabi-v7a/libopenxr_loader.so': 'armeabi-v7a'})
        # The black-screen adapter keeps its name; the runtime it wraps is replaced.
        wrapped = [PREFIX+'libopenxr_loader.so', PREFIX+'libq2f_original.so']
        self.assertEqual(dispatcher_entries(wrapped), {PREFIX+'libq2f_original.so': 'arm64-v8a'})
        self.assertEqual(dispatcher_entries(list(OTHER)), {})

    def test_builds_each_abi_with_ndk(self):
        names = [PREFIX+'libopenxr_loader.so', 'lib/armeabi-v7a/libopenxr_loader.so', *OTHER]
        result = self.bridge.open_dispatcher(names, self.root/'build')
        self.assertEqual(sorted(result), sorted(names[:2]))
        self.assertIn(b'aarch64-linux-android29', result[names[0]])
        self.assertIn(b'armv7a-linux-androideabi29', result[names[1]])
        for args in self.calls:
            self.assertIn('-Wl,-soname,libopenxr_loader.so', args)
            self.assertIn(str(self.source/'src/third_party/cJSON.c'), args)

    def test_missing_checkout_or_abi_is_refused(self):
        with patch.dict(os.environ, {'OPENXR_DISPATCHER_SOURCE': str(self.root/'missing')}):
            with self.assertRaisesRegex(RuntimeError, 'not found'): self.bridge.open_dispatcher([PREFIX+'libopenxr_loader.so'], self.root)
        with self.assertRaisesRegex(RuntimeError, 'x86_64'): self.bridge.open_dispatcher(['lib/x86_64/libopenxr_loader.so'], self.root)
        with self.assertRaisesRegex(RuntimeError, 'no libopenxr_loader.so'): self.bridge.open_dispatcher(list(OTHER), self.root)

    def test_prebuilt_used_only_without_ndk(self):
        with patch.object(self.bridge, 'toolchain', return_value=(None, None, None, None)):
            with self.assertRaisesRegex(RuntimeError, 'build.sh'): self.bridge.open_dispatcher([PREFIX+'libopenxr_loader.so'], self.root)
            (self.source/'prebuilt/arm64-v8a').mkdir(parents=True)
            (self.source/'prebuilt/arm64-v8a/libopenxr_loader.so').write_bytes(b'\x7fELF unrelated library')
            with self.assertRaisesRegex(RuntimeError, 'not an openxr-dispatcher-open build'):
                self.bridge.open_dispatcher([PREFIX+'libopenxr_loader.so'], self.root)
            (self.source/'prebuilt/arm64-v8a/libopenxr_loader.so').write_bytes(b'\x7fELF '+DISPATCHER_MARKER)
            self.assertIn(DISPATCHER_MARKER, self.bridge.open_dispatcher([PREFIX+'libopenxr_loader.so'], self.root)[PREFIX+'libopenxr_loader.so'])

    def convert(self, **options):
        backup = self.root/'backup'; backup.mkdir(exist_ok=True)
        with zipfile.ZipFile(backup/'base.apk', 'w') as apk: apk.writestr(PREFIX+'libgame.so', b'\x7fELF game')
        assets = self.root/'assets'; (assets/'native').mkdir(parents=True, exist_ok=True)
        (assets/'native/libopenxr_loader_generic.so').write_bytes(b'\x7fELF frame bridge Q2F_REFRESH_RATE_V1')
        with patch('bridge.ASSETS', assets), patch('bridge.time.strftime', return_value='build-'+str(len(self.calls))):
            folder = self.bridge.convert(backup, 'Test', **options)
        with zipfile.ZipFile(folder/'game.apk') as apk:
            return folder, {name: apk.read(name) for name in apk.namelist()}

    def test_default_conversion_unchanged(self):
        folder, files = self.convert()
        self.assertEqual(files[PREFIX+'libopenxr_loader.so'], CLOSED)
        self.assertFalse(any('clang' in args[0] for args in self.calls))
        self.assertNotIn('open_dispatcher', json.loads((folder/'game.json').read_text()))

    def test_conversion_replaces_only_dispatcher(self):
        folder, files = self.convert(open_dispatcher=True)
        self.assertIn(DISPATCHER_MARKER, files[PREFIX+'libopenxr_loader.so'])
        for name, data in OTHER.items():
            if name != PREFIX+'libopenxr_loader_generic.so': self.assertEqual(files[name], data)
        # The Frame adapter still takes the generic slot and wraps OVR Port's generic loader.
        self.assertEqual(files[PREFIX+'libopenxr_loader_original.so'], OTHER[PREFIX+'libopenxr_loader_generic.so'])
        self.assertIn(b'frame bridge', files[PREFIX+'libopenxr_loader_generic.so'])
        game = json.loads((folder/'game.json').read_text())
        self.assertTrue(open_dispatcher_value(game)); check_dispatcher_build(game)

    def test_rebuild_enables_and_refuses_removal(self):
        folder, _ = self.convert()
        rebuilt = self.bridge.rebuild_apk(folder, 'Test', open_dispatcher=True)
        with zipfile.ZipFile(rebuilt/'game.apk') as apk:
            self.assertIn(DISPATCHER_MARKER, apk.read(PREFIX+'libopenxr_loader.so'))
            self.assertEqual(apk.read(PREFIX+'libopenxr_loader_pico.so'), OTHER[PREFIX+'libopenxr_loader_pico.so'])
        check_dispatcher_build(json.loads((rebuilt/'game.json').read_text()))
        # The original build is the baseline, so turning the option off again restores OVR Port's dispatcher.
        restored = self.bridge.rebuild_apk(rebuilt, 'Test', open_dispatcher=False)
        with zipfile.ZipFile(restored/'game.apk') as apk: self.assertEqual(apk.read(PREFIX+'libopenxr_loader.so'), CLOSED)
        self.assertNotIn('built_open_dispatcher', json.loads((restored/'game.json').read_text()))
        converted, _ = self.convert(open_dispatcher=True)
        with self.assertRaisesRegex(ValueError, 'Reconvert'): self.bridge.rebuild_apk(converted, 'Test', open_dispatcher=False)

    def test_setting_checks(self):
        check_dispatcher_build({})
        with self.assertRaisesRegex(ValueError, 'Rebuild APK'): check_dispatcher_build({}, True)
        with self.assertRaisesRegex(ValueError, 'Rebuild APK'): check_dispatcher_build({'open_dispatcher': True})
        check_dispatcher_headset(True, 'Default'); check_dispatcher_headset(False, 'Quest 3')
        with self.assertRaisesRegex(ValueError, 'Reported headset'): check_dispatcher_headset(True, 'Quest 3')
        with self.assertRaisesRegex(ValueError, 'Reported headset'): self.convert(open_dispatcher=True, headset='Quest 3')


class DispatcherUITests(unittest.TestCase):
    setUp=test_app.ArtworkUITests.setUp
    tearDown=test_app.ArtworkUITests.tearDown

    def test_default_off(self):
        self.assertFalse(self.app.options()['open_dispatcher'])
        self.app.open_dispatcher.set(True)
        self.assertTrue(self.app.options()['open_dispatcher'])
