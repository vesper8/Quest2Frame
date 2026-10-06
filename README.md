# Quest to Frame

A Windows wizard for backing up a locally connected Quest game, converting it with an independently installed OVR Port CLI, and deploying it to Steam Frame over ADB and SSH. Includes per-game OpenXR resolution controls and optional Steam shortcuts/artwork.

This is the **Windows source edition**, not a ready-to-run installer. Connect each headset to your **PC**, one at a time; do not connect Quest directly to Frame for this workflow. See [GUIDE.md](GUIDE.md) for additional safety and recovery details.

## Installation

### PC prerequisites

- Windows x64, data-capable USB cables, and enough disk space for original games, conversion intermediates, and backups.
- Python 3.11 or newer with pip and Tcl/Tk (tested locally with Python 3.13). Check with `python --version` and `python -m tkinter`; close the Tk test window afterward.
- Git if cloning, or download and extract the repository ZIP. This private repository requires GitHub access.
- Android SDK Platform-Tools (ADB), Build-Tools **35.0.0**, and NDK (Side by side) **25.2.9519653**. Select these in Android Studio's SDK Manager, under SDK Tools with package details enabled. See [Google's SDK Manager instructions](https://developer.android.com/studio/intro/update#sdk-manager).
- Java **21**, Windows x64 ZIP distribution, such as the [Temurin 21 JDK](https://adoptium.net/temurin/releases/?version=21).
- The CLI distribution from [OVR Port 1.2.3](https://github.com/ovrport/app/releases/tag/1.2.3), containing `overportcli-1.2.3-all.jar`. That spelling is intentional; the GUI or Android APK is not a substitute.

Keep the PC and Frame on the same trusted network for SSH. Internet access is needed for dependency/runtime downloads and optional artwork.

### Download and prepare the source

Open PowerShell in a writable folder:

```powershell
git clone https://github.com/MichaelScottsman/Quest2Frame.git
cd Quest2Frame
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Extract the Java ZIP under `tools/java21/`, preserving its single JDK directory. Extract/copy the CLI JAR into `tools/ovrport-cli/`. The app requires these exact locations; setting `JAVA_HOME` or installing Java elsewhere is not enough.

```text
Quest2Frame/
  app.py
  Launch.cmd
  native/
    frame_bridge.c
  tools/
    java21/
      <extracted-jdk-directory>/
        bin/java.exe
    ovrport-cli/
      overportcli-1.2.3-all.jar
```

The default Android SDK location is `%LOCALAPPDATA%\Android\Sdk`. Build the compatibility adapter from the repository root:

```powershell
.\build-native.ps1
```

For a non-default SDK location, set these variables in the PowerShell window used to launch the app:

```powershell
$env:ANDROID_SDK_ROOT = 'D:\Android\Sdk'
$env:PATH = "$env:ANDROID_SDK_ROOT\platform-tools;$env:PATH"
.\build-native.ps1 -Sdk $env:ANDROID_SDK_ROOT
```

ADB discovery uses PATH first, then the default SDK location. Setting `ANDROID_SDK_ROOT` alone does not change ADB discovery. The build script fetches checksum-verified OpenXR headers and creates `native/libopenxr_loader_generic.so`. If PowerShell blocks a downloaded script, review it and use `Unblock-File .\build-native.ps1`; organization policy may require administrator assistance. Do not disable system-wide security settings.

OVR Port downloads its runtime during conversion; the app requests version `3.4.3-23204ea`. Its workspace and signing keys are stored under `tools/ovrport-workspace/`. These files are not included in Git.

## Headset prerequisites: developer mode and ADB

### Quest

1. Complete Meta's developer-account requirements and enable Developer Mode for your headset using the paired Meta Horizon mobile app. Follow [Meta's device setup instructions](https://developers.meta.com/horizon/documentation/native/android/mobile-device-setup/), including the Windows Oculus ADB driver installation.
2. Connect Quest to the PC with a **data** cable, put on the headset, and unlock it.
3. Accept the USB-debugging authorization prompt. Remember the computer only if you trust it. A file-access prompt is not the same as debugging authorization.
4. Verify that ADB lists the Quest serial with status `device`, not `unauthorized` or `offline`:

```powershell
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" devices -l
```

Use `adb devices -l` if ADB is on PATH. Authorization is per computer; approving another PC does not authorize this one. Disconnect Quest after checking it so you can prepare Frame.

### Steam Frame

1. Open **Steam Settings > System** on Frame and enable Developer Mode.
2. Open the **Developer** settings section and choose **Set User Password**. Set your own password; the app does not supply one. See [Valve's Frame development setup](https://partner.steamgames.com/doc/steamhardware/steamframe/setup).
3. Connect Frame to the PC by USB. Run `adb devices -l`. This app expects the native Linux USB ADB connection, identified as `frame`, with status `device`.
4. Leave Frame's Wi-Fi enabled. Note its hostname or Wi-Fi IP address and ensure the PC can reach it over SSH. The app defaults to host `frame`, user `steamos`, and port `22`; enter the password you set above.
5. Keep Frame attached for the app's first SSH connection. It verifies the network host key against the public key read over physical USB before trusting it.

**Native Frame ADB and Lepton ADB are different.** USB normally reaches Frame's Linux OS. A running Lepton Android container is accessed separately on TCP port 5555; see [Valve's Lepton ADB guide](https://partner.steamgames.com/doc/steamhardware/steamframe/adb_lepton). Do not substitute `localhost:5555` or an emulator for `frame` in this wizard. You do not need to start Lepton Development or manually forward port 5555 for this workflow: the generated game launcher handles its dedicated Lepton environment.

Frame must have Steam and Lepton available. This version expects a single Steam user profile. Developer mode exposes debugging services; use a trusted network and disable services later if no longer needed.

## Running the app

From the repository root, launch with the virtual environment created above:

```powershell
.\.venv\Scripts\python.exe app.py
```

Use that command each time. Alternatively, install dependencies into your normal Python with `python -m pip install -r requirements.txt`, then run `python app.py` or double-click `Launch.cmd`. **Launch.cmd uses pythonw on PATH; it does not automatically select .venv.** No prebuilt executable is supplied by this repository.

If the window immediately closes, launch from PowerShell to see the error.

## Transfer a game: step by step

### 0. Configure Frame

With **Frame connected to the PC**, enter its hostname/IP, SSH port, username, and password in **Frame setup**. Click **Test SSH and save connection details**. Only non-secret connection details are saved; the password stays in memory. Unknown or changed SSH host keys are not silently accepted.

After the test succeeds, disconnect Frame and connect Quest to the PC.

### 1. Select a Quest game

Unlock Quest and authorize debugging if prompted. Click **Scan Quest and list installed apps**, filter by app name or package ID, select games, and click **Use selected game(s)**. The app reads display names from Quest's package manager with a temporary shell helper (no helper app is installed), and uses those names for titles and artwork searches. Package IDs remain visible to distinguish similarly named apps. If name lookup is unavailable, IDs are shown as a fallback.

Name lookup requires an installed Android SDK platform (`platforms/*/android.jar`). If the local Java runtime lacks `javac`, the app downloads the pinned, SHA-256-verified Eclipse Java compiler 3.40.0 from Maven Central on first scan. It caches the compiler and generated helper under ignored `tools/`; subsequent scans do not require downloading them again.

### 2. Back up and convert

Choose a library title and resolution scale (50–200%; start at 100%). Leave physical-controller compatibility enabled for controller games; disable it for hand-tracking games. The foveation option disables an unsupported Quest rendering path.

**Headset Hz** sets a per-game display refresh-rate preference: **Default**, **72**, **80**, **90**, **120**, or **144**. Default leaves the game's/runtime's choice unchanged. These are candidate values, not a guarantee that every mode is supported. The adapter enables `XR_FB_display_refresh_rate` only when advertised, checks the runtime's supported rates, and requests the selected mode when the VR session begins. Unsupported modes leave the runtime's choice alone; requests may also be declined by the runtime. This is not a game FPS cap or a guarantee of rendering performance. Runtime decisions are logged under `FrameBridge` in Android logs.

The same option is available in each batch game's settings. New conversions include refresh-rate support; older APKs must be reconverted first, and the app rejects non-default refresh settings for them. Reconverting a specially patched game also requires reapplying its game-specific patches—keep Default for your existing working builds until then. For builds containing the new adapter, use **Apply resolution / refresh rate / compatibility settings** and restart the game; no further reconversion is needed. See the [OpenXR refresh-rate request specification](https://registry.khronos.org/OpenXR/specs/1.1/man/html/xrRequestDisplayRefreshRateFB.html).

With **Search Steam automatically** enabled, selecting a Quest package searches Steam for artwork candidates using its title or a package-name hint. Choose the correct game and edition from the candidate dropdown to fill the **Steam App ID**. Suggestions are ranked by title similarity, not guaranteed compatibility; no candidate is silently accepted. Edit the library title and click **Find artwork** to refine the search. If no ID has been selected, the app searches again using the APK's actual title after conversion.

You can still enter an App ID manually (the number after `/app/` in a Steam store URL), or leave it blank to skip artwork. Search failures do not prevent conversion. Searches send the game title to Steam, not APKs or saves; uncheck automatic search to disable automatic requests. The **Find artwork** button still performs a search when clicked.

Click **Back up Quest game and convert**. Leave Quest connected until conversion completes. The app copies the original APK, expansion files, and accessible external saves, runs OVR Port, applies the adapter, aligns/signs the APK, and verifies its signature. Original Quest files are not modified.

The progress bar shows the current stage, not an estimated overall completion percentage. Wi-Fi uploads report bytes and percentage per file; USB copies show percentages when ADB emits them. Conversion identifies each stage and reports APK repacking progress. Patching, signing, checksums, and other stages without a measurable percentage show an animated bar. The batch window also shows the current game number and the same live progress. A file reaching 100% does not mean installation is finished: verification and Steam setup still follow.

### 3. Install and play on Frame

Keep Frame awake on the same network as the PC for wireless installation, or connect it to the PC for USB installation. Choose the transfer mode and whether to add the game to Steam, then click **Install on Frame**.

Before installing, use **Refresh storage** and choose **Internal storage** or a detected **SD card / removable storage** destination. Each entry shows available space; no drive label is hard-coded. The card must already be mounted, writable, and formatted as ext4 or btrfs with execution allowed. The app does not format, mount, or change permissions on storage. Unsupported/read-only/noexec filesystems are omitted.

On an SD install, APKs, expansion files, external saves, and per-game Lepton data/shaders live under `<detected mount>/Applications/quest-frame/<package>/`. A small launcher, deployment record, and Steam artwork remain internal to keep the shortcut stable. Steam/Lepton's shared runtime and system caches can still use internal storage. The launcher finds the card by filesystem UUID at each launch, so changing its label or mount directory does not require a new shortcut. Keep the card inserted during installation and play; missing storage produces an error rather than selecting internal storage instead.

**Existing games are not automatically moved between drives.** Choose their current destination when updating. Switching an existing install to another drive is refused to avoid splitting saves or overwriting progress. An SD card must be mounted before launching a game. Reformatting changes its UUID and is not equivalent to renaming it.

**Wireless installation (default):** leave **Install wirelessly over Wi-Fi (SSH/SFTP)** checked. Keep Frame awake on the same network as the PC, enter its SSH credentials in step 0, and leave Quest connected to the PC if desired. After the first USB-verified SSH connection, Frame needs no USB cable for installs. Files transfer through authenticated SSH/SFTP into each game's dedicated Lepton installation, not into Lepton Development. No `adb connect`, open network ADB port, or running Lepton Development session is required. APK/OBB checksum verification, save preservation, internal/SD storage selection, and artwork work in both modes.

**USB installation:** uncheck the wireless option and connect Frame's native USB ADB interface. SSH still configures the launcher and optional Steam shortcut/artwork. Adding the shortcut briefly closes and restarts Steam in either mode, so finish any active game first. Keep the chosen connection active until installation completes. A failed Wi-Fi transfer reports an error; it does not silently switch to USB.

Open the new entry in Frame's Steam library. The first launch initializes its Lepton container and may take longer. Subsequent play does not require the PC.

## Change settings or resume later

### Multiple games / batch queue

1. On **Select Quest game**, Ctrl-click or Shift-click multiple packages (or use **Select all visible**), then **Use selected game(s)**. Changing the filter clears the selection.
2. In the batch window, select each row to edit its title, resolution, controller/foveation options, and artwork App ID. Click **Save game settings** before switching rows. Blank titles use the APK title. New games inherit the main window's resolution/compatibility settings, but never another game's title or artwork ID.
3. Select the games to process and click **Back up & convert selected**. Keep the original Quest attached for the entire batch. Games run sequentially; failures are recorded individually and remaining games continue.
4. If automatic artwork search is enabled on the main Convert tab, candidates are saved after each conversion. Select each row to review its candidates, choose the correct edition, and save. You can also enter a title and use **Find artwork by title** manually.
5. Choose wireless installation, or uncheck it and switch the USB connection to Frame. Select ready rows and click **Install selected ready games**. Each game keeps its own settings/artwork. With Steam integration enabled, Steam currently restarts **once per game**; finish active games first.

The batch window has its own **Install location** selector and **Refresh storage** button. Its selected destination applies to that installation run. To split a batch across internal storage and SD, install one subset, change the destination, then install the remaining subset. Storage selection is not restored from queue files; refresh and select the intended destination after reopening a queue.

Queues are automatically saved under `games/batches/` without SSH credentials. Use **Open batch / saved games** to reopen a queue after restarting the app. Cancel that file picker to create an empty queue, then use **Add saved conversions** to select existing `game.json` files. Duplicate packages are skipped.

Retries reuse completed backups/builds and skip already installed rows. Failed installs can be selected and retried; if a shortcut failed after file installation, the retry reinstalls the saved build. Interrupted jobs may need retrying. Select a failed row to see its error; the main activity log contains details. Wait for the active job to finish before closing either window.

Use **Open a previously converted game** and select `games/<package>/build-.../game.json`.

- Change resolution/controller options on step 2, then select **Apply resolution / compatibility settings only** on step 3. Restart the game afterward. With a verified host key, this needs SSH but not USB or a rebuild.
- Select **Add / repair Steam shortcut only** to retry library integration.
- Select Wi-Fi or connect Frame over USB, then select **Install on Frame** to reinstall the saved build.

Back up `games/` and `tools/ovrport-workspace/signatures/`. Existing external Frame saves are not overwritten with older Quest saves; app updates retain snapshots of replaced app/baked data. See [GUIDE.md](GUIDE.md) for details.

## Troubleshooting

### Uninstall a Quest2Frame game

On **Install on Frame**, open **Manage / uninstall Frame games**, then **Refresh installed games**. This uses the SSH credentials from Frame setup; USB is only needed for initial SSH verification. It detects managed internal and SD installations from deployment records and refuses unknown/unsafe paths. Insert and mount an SD card before uninstalling its games.

Select one game and click **Uninstall selected game**. Review the package and destination in the confirmation. Close active games first: uninstalling removes the selected Steam shortcut and restarts Steam. Other game installations and shortcuts are preserved.

**Preserve saves / Lepton data** is on by default. It retains the selected game's `lepton-data` and previous baked-data snapshots under a `.quest2frame-saves-<package>-<unique ID>` recovery folder beside the former game directory, on the same drive. The exact path is displayed afterward. This can retain substantial data, not just small save files; restoration is manual. Uncheck this option only if you also want to permanently delete that game's saves and baked data.

Uninstall removes game APK/OBB files, prior app copies, shaders, and the managed launcher directory. Quest originals, PC backups/builds, shared Steam/Lepton runtimes, and unrelated games are not deleted. Steam's cached grid artwork and timestamped shortcut backups remain. Local batch queues/build manifests are not rewritten; a fresh queue can reinstall the saved build later.

The Steam shortcuts file is backed up before removal. Interrupted cleanup may leave a `.quest2frame-uninstall-...` directory; the error reports recovery paths. Do not delete these blindly. Full uninstall testing uses temporary fixtures; installed-game discovery has been checked on Frame without removing real games.

| Symptom | What to check |
| --- | --- |
| No headset in ADB | Data cable, PC USB port, headset awake, developer mode, and the appropriate Windows ADB driver. |
| Quest is `unauthorized` | Put on Quest and accept USB debugging; reconnect if the prompt is not visible. |
| Only an emulator or `localhost:5555` appears | Connect Frame to the PC over USB and check for its native `frame` connection. |
| SSH cannot resolve `frame` | Use Frame's Wi-Fi IP. Ensure the PC can reach it and the network does not isolate clients. |
| SSH authentication fails | Check developer mode, user `steamos`, and your user password from Frame's Developer settings. |
| First SSH connection needs verification | Attach Frame by USB. Do not bypass a changed-host-key warning without verifying the device. |
| Java/OVR Port missing | Check the exact tools layout, JDK subdirectory, and JAR filename above. |
| SDK tool or adapter missing | Install Build-Tools/NDK, check SDK paths, and run `build-native.ps1`. |
| Converted game fails | Compatibility varies; split APKs, DRM/platform services, and unusual rendering/input paths may not work. |

Only single-APK ARM64 games are supported. Protected/private Quest saves may not transfer. Resolution scales recommended width **and** height; 150% is roughly 2.25 times the pixels, and a game may ignore the recommendation. Do not run multiple conversions simultaneously.

Tests with the virtual environment: `.\.venv\Scripts\python.exe -m unittest discover -s . -v`. Private-fixture tests skip on a fresh clone. These instructions were checked against the implementation; a clean-machine installation has not been tested.

## Source-only repository

### Testing graphics and headset profiles without copying OBBs

Open an existing `game.json` with **Open saved build**, then choose resolution,
headset refresh rate, and **Reported headset** under **Review & convert**.
Choose **Rebuild APK only**, then **Install APK only** on the installation page.
The Quest does not need to be connected. Rebuild uses the existing converted APK,
preserves its native compatibility fixes, and creates a separate signed build.
APK-only installation requires an existing deployment; it uses that deployment's
storage UUID/location and does not copy, replace, or remove OBBs or saves. Close
the game first. The previous APK is retained on Frame for recovery.

**Default** preserves the original identity. **Quest 2** and **Quest 3** are
experimental, build-time overrides for the pinned OVR Port runtime's OpenXR
system name and Meta headset-model UUID, plus OVR Port's Unreal GameActivity
model constant. Unknown runtime binaries are rejected. These are not global
Android device-property changes and cannot guarantee every game's device check
will agree. No hardware capabilities, extensions, assets, or performance are
added by selecting Quest 3. Keep the original build to revert. Rebuilds retain a
reference to that original APK, so do not delete it while testing profiles.

Resolution and refresh-rate changes can also use **Apply settings only** on
compatible builds (restart the game); changing reported headset requires an APK
rebuild. Refresh rate remains limited to modes supported by the runtime. An old
custom adapter without refresh-rate support is rejected rather than overwritten.

**Fix black screen (swapchain rectangles)** is an optional build setting for
games that play audio but submit invalid eye-image rectangles, such as the
observed Moss II and Vampire Survivors failures. It corrects only 1–2 pixel
rounding overflows; it is not a general fix for every black screen. Enable it
under **Review & convert** (or per-game batch settings), rebuild, then install
the APK. This uses the existing compatibility adapter and requires the Android
NDK. New builds can be rebuilt with the option off using their saved original
APK. If the original APK already contains the fix, open a pre-fix build to
remove it. Existing game data and saves are retained by APK-only installation.

### Optional: open-source OpenXR dispatcher

OVR Port's runtime bundle puts a closed OpenXR dispatcher into every converted
APK as `lib/<abi>/libopenxr_loader.so`. **Open-source OpenXR dispatcher** under
**Review & convert** (or per-game batch settings) replaces it with
[openxr-dispatcher-open](https://github.com/Molier/openxr-dispatcher-open)
(MIT), a drop-in replacement with the same exports. It is off by default; with
it off, conversion is unchanged.

The app builds the dispatcher from a local checkout with the same NDK compiler
as the Frame adapter. No binary is downloaded and CMake is not required:

```powershell
git clone https://github.com/Molier/openxr-dispatcher-open tools\openxr-dispatcher-open
```

To use a checkout elsewhere, set `$env:OPENXR_DISPATCHER_SOURCE` before
launching the app. Without the NDK compiler, the app uses the checkout's
`prebuilt\<abi>\libopenxr_loader.so` built by its `build.sh` (Linux/WSL).

After OVR Port's conversion and before signing, only `libopenxr_loader.so` is
replaced, for each ABI the APK contains (arm64-v8a and armeabi-v7a are
supported; other ABIs are refused). The vendor loaders, the Frame adapter in
the generic-loader slot, and OVR Port's config stay as they are. The activity
log names each replaced file and its hash. With the black-screen fix, the
runtime inside that adapter is replaced instead. The dispatcher reads OVR
Port's config file and properties and logs every gap between what the game asks
and what the runtime offers under the `OpenXRDispatch` tag in Android logs,
next to the adapter's `FrameBridge` lines.

**Rebuild APK only** can switch it on for an existing build. A build converted
with the option on cannot switch back; reconvert instead. **Reported headset**
must stay **Default**, because those overrides patch OVR Port's closed
dispatcher.

This repository contains our Python/C/shell source and documentation. It does **not** distribute Quest games, patched APKs, game saves, Steam artwork, signing keys, device configuration, downloaded OVR Port binaries, Android SDK/NDK, Java, Lepton, or the locally built Windows executable. `.gitignore` protects these local files without deleting them.

The existing workspace is configured to run the app. A fresh clone needs the external tools described in the app guide; this is not a self-contained installer. To compile the adapter from a fresh clone, run `./build-native.ps1` with an installed Android NDK. That script fetches the two OpenXR headers from a pinned upstream revision and verifies their SHA-256 hashes.

Run source: `python -m pip install -r requirements.txt`, then `python app.py` (or `Launch.cmd`).

Tests: `python -m unittest discover -s . -v`. Tests using private game/Steam fixtures skip when those files are unavailable.

## License

Original project source and documentation are licensed under **GPL-3.0-only**, as stated in [LICENSE](LICENSE). This is a project licensing choice, not a claim that invoking a GPL command-line tool automatically licenses every caller under GPL. Third-party materials retain their own licenses; this project's license does not grant rights to game content or artwork.

Read [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before distributing anything beyond this source tree. The locally built EXE and downloaded runtime bundle are **not cleared for redistribution by this audit**.
