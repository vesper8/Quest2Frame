# Third-party licensing and distribution boundaries

Audit date: 2026-09-26. This is a source/dependency inventory, not legal clearance for games, converted APKs, or binary releases.

## Original project files

The Python app, native adapter implementation, launcher template, build script, tests, and project documentation in this repository are offered under GPL-3.0-only. Third-party licenses reproduced under `LICENSES/` remain verbatim license documents; they are not relicensed under the project license. No OVR Port implementation or Valve Lepton implementation is copied into the committed source tree.

## OVR Port: distinguish application from runtime bundle

- **Application / CLI:** upstream identifies [ovrport/app](https://github.com/ovrport/app) as GPLv3. The inspected source revision is `1c7cb335c81369fbfd2957ad0edcb238956a8e3c`; its [LICENSE](https://github.com/ovrport/app/blob/1c7cb335c81369fbfd2957ad0edcb238956a8e3c/LICENSE) is GPL version 3. The locally installed CLI reports version 1.2.3. Public tags inspected did not include a matching 1.2.3 tag, so this audit does **not** assert that the inspected revision is the exact corresponding source of that JAR.
- **Runtime libraries:** the local bundle identifies itself as experimental `3.4.3-23204ea`, fetched from `https://files.crx.moe/ovrport/tracker/releases/download/3.4.3-23204ea/libraries.zip`. It includes vendor-specific loaders, platform libraries, and `libOVRPlugin.so`. The extracted bundle did not supply a complete license/notice inventory. **Do not infer that every binary is GPL merely because the patcher is GPL.** Redistribution permissions and exact corresponding sources remain unresolved.
- Both the CLI and runtime bundle are excluded from Git. Users provision them separately. If distributing them later, obtain the exact applicable licenses, copyright notices, and corresponding source/build materials required for each component; merely linking to an upstream homepage is not a blanket substitute for GPL binary-distribution obligations.
- The custom adapter dynamically delegates to a loader from this external bundle at runtime. Licensing the adapter source does not establish permission to redistribute that combined runtime or a patched game.

## OpenXR headers

The adapter uses `openxr.h` and `openxr_platform_defines.h` from [KhronosGroup/OpenXR-SDK](https://github.com/KhronosGroup/OpenXR-SDK), pinned by the build script to revision `f2448a8797c85814aa892efc1ab8707900fbcc78`. Each header bears **Apache-2.0 OR MIT** and its Khronos copyright notice. For this project's header use, choose the **MIT** alternative. The upstream MIT license is preserved in [LICENSES/OpenXR-MIT.txt](LICENSES/OpenXR-MIT.txt). Preserve the header notices and that license when distributing header copies or builds incorporating them. The headers themselves are downloaded into an ignored build-dependency directory, not committed.

## openxr-dispatcher-open (optional)

The optional **Open-source OpenXR dispatcher** setting builds [openxr-dispatcher-open](https://github.com/Molier/openxr-dispatcher-open) from a checkout the user clones under ignored `tools/` and puts it into that user's converted APK in place of OVR Port's closed `libopenxr_loader.so`. It is **MIT** licensed ([LICENSE](https://github.com/Molier/openxr-dispatcher-open/blob/main/LICENSE)) and bundles cJSON (MIT) and Khronos OpenXR headers (Apache-2.0 OR MIT), each with its own license file in that repository. No source or binary of it is committed here. Builds incorporating it must keep those notices.

## Python and native packaging dependencies

Only dependency declarations are committed, not these packages. The versions below describe the existing local build, not a complete future release SBOM.

| Component | Inspected version | License / status |
| --- | --- | --- |
| [Paramiko](https://github.com/paramiko/paramiko) | 5.0.0 | Installed metadata: LGPL-2.1; upstream LGPL 2.1 text reproduced in `LICENSES/Paramiko-LGPL-2.1.txt`. Retain its original copyright/license notices. |
| [cryptography](https://github.com/pyca/cryptography) | 46.0.3 | Apache-2.0 OR BSD-3-Clause per installed metadata; bundled native dependencies need their own inventory. |
| [bcrypt](https://github.com/pyca/bcrypt) | 5.0.0 | Apache-2.0 per installed metadata. |
| [PyNaCl](https://github.com/pyca/pynacl) | 1.6.2 | Apache-2.0 per installed metadata; bundled libsodium also has separate notices. |
| [Invoke](https://github.com/pyinvoke/invoke) | 3.0.3 | BSD-2-Clause per installed metadata. |
| [PyInstaller](https://pyinstaller.org/en/stable/license.html) | 6.22.3 | GPLv2-or-later with its distribution/bootloader exception, NOT plain MIT and NOT blanket GPL for packaged apps. Preserve the applicable exception and notices in releases. |
| [CPython](https://docs.python.org/3/license.html), Tcl/Tk | Python 3.13.11 locally | PSF and bundled-component licenses / Tcl-Tk notices; a frozen build contains these and requires a complete inventory. |

The source app lets users install and replace Paramiko normally. A frozen executable containing LGPL software needs a distribution plan that satisfies the applicable source, notice, modification/relinking, and reverse-engineering provisions. Keeping our source public or copying a license file alone is not sufficient to certify every frozen packaging arrangement. The existing EXE is ignored, local-only, and not approved here for public release.

## External tools and device-side software (not redistributed)

- Eclipse Temurin/OpenJDK 21: local distribution carries GPLv2 with Classpath Exception and additional component notices under its `legal/` directory. Retain those if redistributing the runtime.
- [Eclipse Compiler for Java (ECJ) 3.40.0](https://central.sonatype.com/artifact/org.eclipse.jdt/ecj/3.40.0): EPL-2.0. Downloaded from Maven Central only when `javac` is unavailable, to compile the original GPL-3.0-only `native/QuestAppLabels.java` helper. Compiler binaries are cached under ignored `tools/` and are not redistributed. Pinned SHA-256: `05cc22a24e7982970f63a405fc6c820bc80b806f27f3c5a6236fc475f8f7152b`. The compiler's license does not replace the helper source's license.
- Android SDK/NDK, build-tools and platform-tools: separately installed tools with component-specific notices and SDK terms. Do not apply this repository's license to their binaries.
- Valve Lepton: device-installed README states its compatibility tool is MIT and its Android root filesystem includes GPL-3.0 components. The original Valve shell sources copied locally for inspection are excluded under `tools/`; this repository does not redistribute Lepton or its Android images.
- Frida: used only for earlier local diagnostics; neither its server, scripts, nor packages are included or required by this source app.

## Game content, artwork, and release checklist

The Windows app can query Steam's store search endpoint with a game title to suggest artwork matches. This sends titles, not game binaries or saves. Users confirm a candidate or enter an App ID manually, and can disable automatic searches. Search availability and artwork download access do not grant redistribution rights to the returned content.

Games, saves, vendor SDK components embedded in games, converted APKs, and Steam CDN artwork are **not** covered by this project's GPL license. Availability through ADB or a public CDN is not permission to redistribute. Use content only where you have the necessary rights; software licensing does not settle platform terms or anti-circumvention law.

Before any binary release: inventory the actual shipped files and exact versions; verify runtime/vendor redistribution terms; preserve every applicable license/notice; provide required corresponding source and build instructions; address LGPL replacement/relinking obligations; and exclude personal credentials, keys, games, saves, and artwork unless separately authorized. Until then, distribute only the reviewed source tree.
