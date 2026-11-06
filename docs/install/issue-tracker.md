# Install issue tracker

Every known install problem, merged into unique issues and ranked by severity. For help with a failure, start with the [troubleshooting guide](troubleshooting.md). Live discussion is in [#3836](https://github.com/debpalash/VoiceStudio/issues/3836).

This list covers 515 GitHub reports, merged into 55 unique issues, as of 2026-11-06. The latest release is v0.5.7.

## How to read this list

- **ID:** `INST-<category>-<number>`. An ID belongs to one problem and is never reused. Rank can change, but the ID stays.
- **Severity:** uses the repo's priority labels.
  - `P0`: blocks first run or core use for many users.
  - `P1`: breaks or degrades a core workflow.
  - `P2`: important improvement or confusing setup.
  - `P3`: nice to have or polish.
- **Fixed** means a fix PR was merged. It doesn't mean the fix was retested on every platform. A PR that only warns about a problem or explains it doesn't count as a fix.

| Status | Meaning |
|---|---|
| Open | No fix yet |
| Partly fixed | Earlier reports were fixed, but newer reports are still open |
| Fixed on main | The latest fix merged after v0.5.7 and ships in the next release |
| Fixed in release | Every linked fix shipped in v0.5.7 or earlier |
| Declined | We chose not to fix it; the Next step column gives the reason |
| Closed, cause unknown | Closed without a confirmed cause, usually because the report had no log |

## Categories

Each category has a GitHub label, and every report below carries it.

| Code | Category | Label |
|---|---|---|
| `WIN` | Windows permissions and installer | `install:windows` |
| `NET` | Network and dependency downloads | `install:network` |
| `NATIVE` | Native crashes | `install:native-crash` |
| `BOOT` | Backend startup | `install:backend-startup` |
| `AMD` | AMD GPU setup | `install:amd` |
| `NVDA` | NVIDIA GPU setup | `install:nvidia` |
| `MAC` | macOS | `install:macos` |
| `LNX` | Linux | `install:linux` |
| `SRC` | Running from source | `install:source` |
| `SCRIPT` | Install script | `install:script` |
| `DOCK` | Docker | `install:docker` |
| `MODEL` | Model downloads | `install:models` |
| `ENG` | Engine installs | `install:engines` |
| `DEP` | System dependencies | `install:dependencies` |
| `UX` | Setup screens | `install:setup-ui` |
| `UPD` | Updates and uninstall | `install:update` |

## Summary

| Status | Unique issues |
|---|---|
| Open | 8 |
| Partly fixed | 10 |
| Fixed on main | 11 |
| Fixed in release | 23 |
| Declined | 1 |
| Closed, cause unknown | 2 |

## Ranked list

| Rank | ID | Severity | Problem | Status | Fix | Next step | Help | Reports |
|---|---|---|---|---|---|---|---|---|
| 1 | `INST-WIN-001` | P0 | Windows Smart App Control or antivirus blocks the unsigned Python files (`spawn UNKNOWN`) | Partly fixed | [#3640](https://github.com/debpalash/VoiceStudio/pull/3640), [#3810](https://github.com/debpalash/VoiceStudio/pull/3810) | Needs signed Python files ([#1779](https://github.com/debpalash/VoiceStudio/issues/1779)). Turning off Smart App Control works today. The merged PRs only warn and name the block. | [`WINDOWS_APP_CONTROL_BLOCKED`](troubleshooting.md#windows_app_control_blocked) | 53 |
| 2 | `INST-NATIVE-001` | P0 | Windows backend crashes with an access violation while loading torch or model data | Partly fixed | [#2467](https://github.com/debpalash/VoiceStudio/pull/2467), [#2499](https://github.com/debpalash/VoiceStudio/pull/2499), [#3385](https://github.com/debpalash/VoiceStudio/pull/3385) | Find the root cause in torch model loading. [#3507](https://github.com/debpalash/VoiceStudio/pull/3507) covers a different setup crash. | [`NATIVE_ACCESS_VIOLATION`](troubleshooting.md#native_access_violation) | 16 |
| 3 | `INST-NET-001` | P0 | Torch or dependency download stalls, restarts or fails on slow or restricted networks | Fixed on main | [#3557](https://github.com/debpalash/VoiceStudio/pull/3557), [#3576](https://github.com/debpalash/VoiceStudio/pull/3576), [#3785](https://github.com/debpalash/VoiceStudio/pull/3785) | Retest on the next release, then close the open reports. | [`RUNTIME_DOWNLOAD_STALLED`](troubleshooting.md#runtime_download_stalled) | 12 |
| 4 | `INST-MAC-001` | P0 | macOS says the app is damaged or will not open it after install | Fixed in release | [#3033](https://github.com/debpalash/VoiceStudio/pull/3033), [#3243](https://github.com/debpalash/VoiceStudio/pull/3243) |  | [`GATEKEEPER_QUARANTINE`](troubleshooting.md#gatekeeper_quarantine) | 9 |
| 5 | `INST-BOOT-001` | P1 | Port 3900 is already in use or cannot be bound | Partly fixed | [#1982](https://github.com/debpalash/VoiceStudio/pull/1982), [#2362](https://github.com/debpalash/VoiceStudio/pull/2362), [#3025](https://github.com/debpalash/VoiceStudio/pull/3025) | Reserved ports now recover, but a port held by another app still stops startup. Pick a free port instead. | [`PORT_IN_USE`](troubleshooting.md#port_in_use) | 7 |
| 6 | `INST-AMD-001` | P1 | System check reports `ROCm not configured`, or Linux ROCm setup fails | Partly fixed | [#3553](https://github.com/debpalash/VoiceStudio/pull/3553), [#3602](https://github.com/debpalash/VoiceStudio/pull/3602) | Setup still asks for an environment variable on AMD. Offer ROCm in setup. | [`ROCM_NOT_CONFIGURED`](troubleshooting.md#rocm_not_configured) | 7 |
| 7 | `INST-BOOT-002` | P1 | Backend gives no answer on port 3900 within the startup limit | Partly fixed | [#1232](https://github.com/debpalash/VoiceStudio/pull/1232), [#2283](https://github.com/debpalash/VoiceStudio/pull/2283), [#2461](https://github.com/debpalash/VoiceStudio/pull/2461) | Find the root cause on macOS. Open [#3724](https://github.com/debpalash/VoiceStudio/pull/3724) only improves the diagnosis. | [`STARTUP_TIMEOUT_SILENT`](troubleshooting.md#startup_timeout_silent) | 7 |
| 8 | `INST-AMD-002` | P1 | Linux gfx103x: the HSA override is applied too late and the ROCm choice is not kept | Partly fixed | [#1666](https://github.com/debpalash/VoiceStudio/pull/1666), [#2424](https://github.com/debpalash/VoiceStudio/pull/2424), [#2501](https://github.com/debpalash/VoiceStudio/pull/2501) | Apply the override earlier and keep the ROCm choice. | [`ROCM_NOT_CONFIGURED`](troubleshooting.md#rocm_not_configured) | 3 |
| 9 | `INST-NET-002` | P1 | Setup stops at the proxy instructions or misreports a proxy problem as DNS | Partly fixed | [#2245](https://github.com/debpalash/VoiceStudio/pull/2245) | Fix the setup flow so it continues after the proxy steps. | [`RUNTIME_DOWNLOAD_INTERRUPTED`](troubleshooting.md#runtime_download_interrupted) | 3 |
| 10 | `INST-AMD-003` | P1 | Linux AMD engines need the matching ROCm torch and honest routing | Partly fixed | [#2501](https://github.com/debpalash/VoiceStudio/pull/2501) | Review open [#3787](https://github.com/debpalash/VoiceStudio/pull/3787). | [`ROCM_NOT_CONFIGURED`](troubleshooting.md#rocm_not_configured) | 3 |
| 11 | `INST-NATIVE-002` | P1 | Backend exits with heap corruption on Windows | Open |  | Find the root cause. | [`NATIVE_CRASH`](troubleshooting.md#native_crash) | 1 |
| 12 | `INST-BOOT-003` | P1 | Backend exits on the idle setup screen after about two minutes | Open |  | Find why the backend exits while setup is idle. Open [#3727](https://github.com/debpalash/VoiceStudio/pull/3727) only explains the exit code. | [`BACKEND_STARTUP_FAILED`](troubleshooting.md#backend_startup_failed) | 1 |
| 13 | `INST-NATIVE-003` | P1 | Backend exits with an illegal instruction on older CPUs | Open |  | Find the root cause. | [`NATIVE_ILLEGAL_INSTRUCTION`](troubleshooting.md#native_illegal_instruction) | 1 |
| 14 | `INST-BOOT-004` | P1 | Backend runs but stops responding on port 3900 during startup | Fixed on main | [#3119](https://github.com/debpalash/VoiceStudio/pull/3119), [#3185](https://github.com/debpalash/VoiceStudio/pull/3185), [#3595](https://github.com/debpalash/VoiceStudio/pull/3595) | Ships in the next release. | [`STARTUP_TIMEOUT_OUTPUT`](troubleshooting.md#startup_timeout_output) | 30 |
| 15 | `INST-BOOT-005` | P1 | Runtime is incomplete after setup, so startup fails on a missing Python module | Fixed on main | [#3508](https://github.com/debpalash/VoiceStudio/pull/3508), [#3552](https://github.com/debpalash/VoiceStudio/pull/3552), [#3595](https://github.com/debpalash/VoiceStudio/pull/3595) | Ships in the next release. | [`BROKEN_VENV`](troubleshooting.md#broken_venv) | 20 |
| 16 | `INST-MODEL-001` | P1 | Model downloads fail: SSL errors, mirror redirects, proxies and bad cache paths | Fixed on main | [#890](https://github.com/debpalash/VoiceStudio/pull/890), [#1312](https://github.com/debpalash/VoiceStudio/pull/1312), [#3622](https://github.com/debpalash/VoiceStudio/pull/3622) | Ships in the next release. | [Downloading models](../downloading-models.md) | 10 |
| 17 | `INST-NVDA-001` | P1 | The bundled torch build does not support the NVIDIA GPU (old or very new) | Fixed on main | [#1997](https://github.com/debpalash/VoiceStudio/pull/1997), [#2149](https://github.com/debpalash/VoiceStudio/pull/2149), [#3553](https://github.com/debpalash/VoiceStudio/pull/3553) | Ships in the next release. | [Section 5a](troubleshooting.md#5a-backend-exits-on-the-first-generate-on-an-older-nvidia-gpu), [Section 5b](troubleshooting.md#5b-rtx-50-series-blackwell-sm_120-backend-crashes-during-ml_imports) | 10 |
| 18 | `INST-NATIVE-004` | P1 | Runtime setup crashes at the CUDNN8 probe on Windows | Fixed on main | [#3025](https://github.com/debpalash/VoiceStudio/pull/3025), [#3507](https://github.com/debpalash/VoiceStudio/pull/3507) | Ships in the next release. | [`NATIVE_ACCESS_VIOLATION`](troubleshooting.md#native_access_violation) | 8 |
| 19 | `INST-ENG-001` | P1 | CosyVoice 3: one-click install reports success but cannot generate | Fixed on main | [#94](https://github.com/debpalash/VoiceStudio/pull/94), [#3553](https://github.com/debpalash/VoiceStudio/pull/3553) | Ships in the next release. | [Section 11](troubleshooting.md#11-indextts--cosyvoice--chatterboxtts-clash) | 4 |
| 20 | `INST-WIN-002` | P1 | `EPERM` while renaming runtime files on Windows | Fixed on main | [#3023](https://github.com/debpalash/VoiceStudio/pull/3023), [#3507](https://github.com/debpalash/VoiceStudio/pull/3507) | Ships in the next release. | [`RUNTIME_FILES_LOCKED`](troubleshooting.md#runtime_files_locked) | 4 |
| 21 | `INST-BOOT-006` | P1 | Startup fails in `api_routes` with `'NoneType' object has no attribute 'splitlines'` | Fixed on main | [#3615](https://github.com/debpalash/VoiceStudio/pull/3615) | Close [#3818](https://github.com/debpalash/VoiceStudio/issues/3818) after the next release. | [`BACKEND_STARTUP_FAILED`](troubleshooting.md#backend_startup_failed) | 2 |
| 22 | `INST-MODEL-002` | P1 | Voice cloning fails because no speech-to-text model is installed | Fixed in release | [#2444](https://github.com/debpalash/VoiceStudio/pull/2444), [#2498](https://github.com/debpalash/VoiceStudio/pull/2498), [#2602](https://github.com/debpalash/VoiceStudio/pull/2602) |  |  | 11 |
| 23 | `INST-WIN-003` | P1 | Setup ignores the chosen drive or fails outside the default location | Fixed in release | [#1795](https://github.com/debpalash/VoiceStudio/pull/1795), [#2500](https://github.com/debpalash/VoiceStudio/pull/2500), [#3024](https://github.com/debpalash/VoiceStudio/pull/3024) |  | [`RUNTIME_FOLDER_DENIED`](troubleshooting.md#runtime_folder_denied) | 9 |
| 24 | `INST-LNX-001` | P1 | AppImage or deb opens to a blank window or never starts | Fixed in release | [#93](https://github.com/debpalash/VoiceStudio/pull/93), [#1265](https://github.com/debpalash/VoiceStudio/pull/1265) |  | [Section 4](troubleshooting.md#4-linux-blank-window-or-gpu-errors) | 9 |
| 25 | `INST-BOOT-007` | P1 | Startup fails in `ml_imports` because a torch DLL is missing or incomplete | Fixed in release | [#3150](https://github.com/debpalash/VoiceStudio/pull/3150) |  | [`BROKEN_VENV`](troubleshooting.md#broken_venv) | 6 |
| 26 | `INST-MODEL-003` | P1 | Incomplete model downloads are shown as installed | Fixed in release | [#626](https://github.com/debpalash/VoiceStudio/pull/626), [#2673](https://github.com/debpalash/VoiceStudio/pull/2673), [#3116](https://github.com/debpalash/VoiceStudio/pull/3116) |  |  | 5 |
| 27 | `INST-SCRIPT-001` | P1 | One-command install script fails or serves the wrong script | Fixed in release | [#61](https://github.com/debpalash/VoiceStudio/pull/61), [#1627](https://github.com/debpalash/VoiceStudio/pull/1627), [#1815](https://github.com/debpalash/VoiceStudio/pull/1815) |  | [script.md](script.md) | 5 |
| 28 | `INST-DOCK-001` | P1 | Docker image fails to build or start | Fixed in release | [#27](https://github.com/debpalash/VoiceStudio/pull/27), [#2072](https://github.com/debpalash/VoiceStudio/pull/2072) |  | [docker.md](docker.md) | 3 |
| 29 | `INST-UPD-001` | P1 | App will not start after an update | Fixed in release | [#1754](https://github.com/debpalash/VoiceStudio/pull/1754) |  |  | 1 |
| 30 | `INST-MAC-002` | P1 | Intel Macs cannot install torch for the local backend | Declined |  | Intel Macs run the app with a remote backend, as [macos.md](macos.md) explains. | [`UNSUPPORTED_PLATFORM`](troubleshooting.md#unsupported_platform) | 5 |
| 31 | `INST-SRC-001` | P2 | Running from source: Python environment missing or incomplete | Partly fixed | [#2602](https://github.com/debpalash/VoiceStudio/pull/2602) | Waiting on reporter details ([#3589](https://github.com/debpalash/VoiceStudio/issues/3589)). | [`SOURCE_ENV_INCOMPLETE`](troubleshooting.md#source_env_incomplete) | 12 |
| 32 | `INST-AMD-004` | P2 | Windows ROCm: remaining engine parity gaps | Partly fixed | [#3045](https://github.com/debpalash/VoiceStudio/pull/3045) | Close the remaining engine gaps. Open: [#3734](https://github.com/debpalash/VoiceStudio/pull/3734), and [#3486](https://github.com/debpalash/VoiceStudio/pull/3486) (waiting on a CLA). | [windows-rocm.md](windows-rocm.md) | 2 |
| 33 | `INST-WIN-004` | P2 | Windows Defender blocks state saves, so the latest crash record is lost | Open |  | Review open [#3708](https://github.com/debpalash/VoiceStudio/pull/3708). | [`ANTIVIRUS_BLOCKED`](troubleshooting.md#antivirus_blocked) | 1 |
| 34 | `INST-LNX-002` | P2 | The app will not open on NixOS | Open |  | Find why the app fails on NixOS. | [nix.md](nix.md) | 1 |
| 35 | `INST-AMD-005` | P2 | Windows RDNA 2 needs working native torch, readiness checks and a safe CPU fallback | Open |  | Review open [#3712](https://github.com/debpalash/VoiceStudio/pull/3712). | [windows-rocm.md](windows-rocm.md) | 1 |
| 36 | `INST-AMD-006` | P2 | Windows Vulkan detection times out on hidden probes | Open |  | Review open [#3648](https://github.com/debpalash/VoiceStudio/pull/3648). |  | 1 |
| 37 | `INST-UX-001` | P2 | First-run setup screens block or mislead (`All good` greyed out, repeated steps) | Fixed on main | [#1992](https://github.com/debpalash/VoiceStudio/pull/1992), [#3378](https://github.com/debpalash/VoiceStudio/pull/3378), [#3595](https://github.com/debpalash/VoiceStudio/pull/3595) | Ships in the next release. |  | 14 |
| 38 | `INST-WIN-005` | P2 | Windows installer claims VoiceStudio is still running or fails to finish | Fixed on main | [#3621](https://github.com/debpalash/VoiceStudio/pull/3621), [#3624](https://github.com/debpalash/VoiceStudio/pull/3624), [#3636](https://github.com/debpalash/VoiceStudio/pull/3636) | Ships in the next release. |  | 6 |
| 39 | `INST-ENG-002` | P2 | Other engine installs fail (Confucius4, dots.tts, Nemo, pyannote, translation) | Fixed in release | [#2173](https://github.com/debpalash/VoiceStudio/pull/2173), [#2181](https://github.com/debpalash/VoiceStudio/pull/2181), [#2484](https://github.com/debpalash/VoiceStudio/pull/2484) |  |  | 11 |
| 40 | `INST-LNX-003` | P2 | Linux packages: deb file conflict, libfuse, updates and microphones | Fixed in release | [#93](https://github.com/debpalash/VoiceStudio/pull/93), [#1681](https://github.com/debpalash/VoiceStudio/pull/1681), [#2329](https://github.com/debpalash/VoiceStudio/pull/2329) |  | [Section 7](troubleshooting.md#7-deb-ffprobe-path-conflict-on-upgrade) | 7 |
| 41 | `INST-DEP-001` | P2 | FFmpeg is missing or not detected during setup | Fixed in release | [#716](https://github.com/debpalash/VoiceStudio/pull/716), [#1311](https://github.com/debpalash/VoiceStudio/pull/1311), [#2353](https://github.com/debpalash/VoiceStudio/pull/2353) |  | [Section 7b](troubleshooting.md#7b-media-engine-unavailable--ffmpeg-questions) | 6 |
| 42 | `INST-DOCK-002` | P2 | Docker: remote browsers, loopback checks and the share PIN block access | Fixed in release | [#93](https://github.com/debpalash/VoiceStudio/pull/93), [#1673](https://github.com/debpalash/VoiceStudio/pull/1673), [#3401](https://github.com/debpalash/VoiceStudio/pull/3401) |  | [Section 8](troubleshooting.md#8-docker-lan-access--media-preview-404) | 4 |
| 43 | `INST-NATIVE-005` | P2 | Backend exits with a stack buffer overrun on Windows | Fixed in release | [#1401](https://github.com/debpalash/VoiceStudio/pull/1401), [#2907](https://github.com/debpalash/VoiceStudio/pull/2907) |  | [`NATIVE_CRASH`](troubleshooting.md#native_crash) | 4 |
| 44 | `INST-ENG-003` | P2 | IndexTTS: one-click install builds the wrong environment or weights config | Fixed in release | [#2205](https://github.com/debpalash/VoiceStudio/pull/2205), [#2207](https://github.com/debpalash/VoiceStudio/pull/2207), [#2240](https://github.com/debpalash/VoiceStudio/pull/2240) |  | [Section 11](troubleshooting.md#11-indextts--cosyvoice--chatterboxtts-clash) | 3 |
| 45 | `INST-MAC-003` | P2 | Backend hangs or fails on Apple Silicon during first model load | Fixed in release | [#645](https://github.com/debpalash/VoiceStudio/pull/645), [#1310](https://github.com/debpalash/VoiceStudio/pull/1310), [#1657](https://github.com/debpalash/VoiceStudio/pull/1657) |  |  | 3 |
| 46 | `INST-DOCK-003` | P2 | Docker: AMD GPU is not detected or has no image | Fixed in release | [#1166](https://github.com/debpalash/VoiceStudio/pull/1166), [#1673](https://github.com/debpalash/VoiceStudio/pull/1673) |  | [docker.md](docker.md) | 2 |
| 47 | `INST-DOCK-004` | P2 | Docker: no ARM64 image for the host | Fixed in release | [#1988](https://github.com/debpalash/VoiceStudio/pull/1988), [#3402](https://github.com/debpalash/VoiceStudio/pull/3402) | The standard image still has no native ARM64 build: #1988 documents emulation and #3402 adds an NVIDIA image. | [docker.md](docker.md) | 2 |
| 48 | `INST-ENG-004` | P2 | MOSS-TTS engines cannot install or have no audio backend | Fixed in release | [#2016](https://github.com/debpalash/VoiceStudio/pull/2016), [#2182](https://github.com/debpalash/VoiceStudio/pull/2182) |  |  | 2 |
| 49 | `INST-BOOT-008` | P2 | Older backend startup reports with no recorded cause (before failure codes) | Closed, cause unknown |  | Root causes are in [#602](https://github.com/debpalash/VoiceStudio/issues/602). | [`BACKEND_STARTUP_FAILED`](troubleshooting.md#backend_startup_failed) | 153 |
| 50 | `INST-UX-002` | P3 | Setup does not make clear which models allow commercial use | Open |  | Answer the question and decide whether setup should show this. |  | 1 |
| 51 | `INST-SRC-002` | P3 | Google Colab notebook install breaks | Fixed in release | [#1923](https://github.com/debpalash/VoiceStudio/pull/1923) |  |  | 4 |
| 52 | `INST-SRC-003` | P3 | Source setup commands fail (`bun run`, Python version, wheels) | Fixed in release | [#1444](https://github.com/debpalash/VoiceStudio/pull/1444), [#2080](https://github.com/debpalash/VoiceStudio/pull/2080) |  |  | 4 |
| 53 | `INST-DOCK-005` | P3 | Docker: image tags lag behind releases or confuse users | Fixed in release | [#252](https://github.com/debpalash/VoiceStudio/pull/252) |  | [docker.md](docker.md) | 2 |
| 54 | `INST-UPD-002` | P3 | Uninstall leaves users unsure how to remove the app | Fixed in release | [#1097](https://github.com/debpalash/VoiceStudio/pull/1097) |  | [uninstall.md](uninstall.md) | 1 |
| 55 | `INST-UX-003` | P3 | Very early install reports with no recorded cause | Closed, cause unknown |  |  |  | 3 |

## Reports for each issue

Open reports are linked. Find a closed report with a search for its number.

| ID | GitHub reports |
|---|---|
| `INST-WIN-001` | #3801, #3789, #3788, #3783, #3779, #3778, #3756, #3739, #3714, #3698, #3697, #3696, #3686, [#3683](https://github.com/debpalash/VoiceStudio/issues/3683), [#3682](https://github.com/debpalash/VoiceStudio/issues/3682), [#3681](https://github.com/debpalash/VoiceStudio/issues/3681), #3672, #3668, #3650, [#3635](https://github.com/debpalash/VoiceStudio/issues/3635), #3633, #3632, #3506, #3371, #3351, #3337, #3329, #3326, #3322, #3272, #3224, #3209, #3204, #3201, #3200, #3196, #3169, #3163, #3156, #3146, #3106, #3103, #3084, #3081, #3077, #3063, #3061, #2981, #2959, #2949, #2839, #2440, #1712 |
| `INST-NATIVE-001` | [#3772](https://github.com/debpalash/VoiceStudio/issues/3772), [#3767](https://github.com/debpalash/VoiceStudio/issues/3767), #3340, #3267, #3226, #3043, #2416, #2394, #2189, #1939, #1927, #1777, #1708, #1669, #1275, #1069 |
| `INST-NET-001` | [#3820](https://github.com/debpalash/VoiceStudio/issues/3820), [#3748](https://github.com/debpalash/VoiceStudio/issues/3748), [#3720](https://github.com/debpalash/VoiceStudio/issues/3720), #3475, #3444, #3411, #3259, #3202, #3060, #2562, #569, #130 |
| `INST-MAC-001` | #3264, #3240, #2951, #2829, #2763, #2681, #2360, #79, #54 |
| `INST-BOOT-001` | [#3704](https://github.com/debpalash/VoiceStudio/issues/3704), #3020, #2358, #1974, #1637, #1635, #1364 |
| `INST-AMD-001` | [#3780](https://github.com/debpalash/VoiceStudio/issues/3780), #3627, #3158, #1629, #1582, #972, #124 |
| `INST-BOOT-002` | [#3713](https://github.com/debpalash/VoiceStudio/issues/3713), #2985, #2445, #2276, #2184, #1909, #1229 |
| `INST-AMD-002` | [#3804](https://github.com/debpalash/VoiceStudio/issues/3804), #2372, #1665 |
| `INST-NET-002` | [#3741](https://github.com/debpalash/VoiceStudio/issues/3741), #2114, #33 |
| `INST-AMD-003` | [#3786](https://github.com/debpalash/VoiceStudio/issues/3786), #2373, #2371 |
| `INST-NATIVE-002` | [#3835](https://github.com/debpalash/VoiceStudio/issues/3835) |
| `INST-BOOT-003` | [#3680](https://github.com/debpalash/VoiceStudio/issues/3680) |
| `INST-NATIVE-003` | [#3684](https://github.com/debpalash/VoiceStudio/issues/3684) |
| `INST-BOOT-004` | #3558, #3488, #3460, #3364, #3342, #3306, #3245, #3229, #3228, #3222, #3214, #3184, #3182, #3173, #3139, #3108, #3085, #3072, #3059, #2970, #2876, #2840, #2794, #2756, #2610, #2601, #2594, #2491, #2490, #2430 |
| `INST-BOOT-005` | #3584, #3551, #3480, #3367, #3121, #2237, #2176, #2128, #1832, #1642, #1587, #1415, #1376, #1282, #1231, #1185, #1156, #314, #307, #129 |
| `INST-MODEL-001` | #3620, #2045, #1335, #1301, #976, #959, #874, #239, #118, #117 |
| `INST-NVDA-001` | #3067, #2135, #1931, #1812, #1529, #1349, #1343, #1342, #1285, #756 |
| `INST-NATIVE-004` | #3293, #3015, #2960, #2957, #2942, #2382, #2270, #2250 |
| `INST-ENG-001` | #3423, #2096, #1746, #55 |
| `INST-WIN-002` | #3409, #3050, #3010, #2669 |
| `INST-BOOT-006` | [#3818](https://github.com/debpalash/VoiceStudio/issues/3818), #3612 |
| `INST-MODEL-002` | #3369, #3176, #3122, #2929, #2678, #2579, #2442, #2322, #2319, #2132, #2116 |
| `INST-WIN-003` | #3070, #2971, #2683, #2436, #1783, #1713, #1223, #938, #766 |
| `INST-LNX-001` | #2146, #1258, #1244, #971, #961, #222, #144, #127, #56 |
| `INST-BOOT-007` | #3370, #3301, #3147, #3058, #3055, #3051 |
| `INST-MODEL-003` | #3112, #2634, #2386, #1273, #622 |
| `INST-SCRIPT-001` | #3274, #2973, #2754, #1813, #1626 |
| `INST-DOCK-001` | #2050, #43, #5 |
| `INST-UPD-001` | #1749 |
| `INST-MAC-002` | #3225, #3198, #2598, #2365, #889 |
| `INST-SRC-001` | [#3722](https://github.com/debpalash/VoiceStudio/issues/3722), [#3589](https://github.com/debpalash/VoiceStudio/issues/3589), #3261, #2988, #2948, #2922, #2787, #2783, #2621, #2555, #2465, #2303 |
| `INST-AMD-004` | #3123, [#2468](https://github.com/debpalash/VoiceStudio/issues/2468) |
| `INST-WIN-004` | [#3705](https://github.com/debpalash/VoiceStudio/issues/3705) |
| `INST-LNX-002` | [#3823](https://github.com/debpalash/VoiceStudio/issues/3823) |
| `INST-AMD-005` | [#3770](https://github.com/debpalash/VoiceStudio/issues/3770) |
| `INST-AMD-006` | [#3645](https://github.com/debpalash/VoiceStudio/issues/3645) |
| `INST-UX-001` | #3581, #2940, #1900, #1894, #1886, #1884, #1856, #1851, #1849, #1848, #1847, #1845, #1514, #1496 |
| `INST-WIN-005` | #3614, #3566, #3115, #1946, #1714, #1140 |
| `INST-ENG-002` | #3638, #2479, #2163, #2101, #2099, #2019, #1029, #974, #529, #527, #186 |
| `INST-LNX-003` | #2328, #2327, #2063, #1682, #1680, #1333, #76 |
| `INST-DEP-001` | #2345, #2215, #2167, #2124, #1309, #712 |
| `INST-DOCK-002` | #3628, #3003, #1651, #80 |
| `INST-NATIVE-005` | #2904, #1409, #1372, #1371 |
| `INST-ENG-003` | #2098, #2097, #1428 |
| `INST-MAC-003` | #1656, #1266, #632 |
| `INST-DOCK-003` | #1655, #1165 |
| `INST-DOCK-004` | #2831, #1921 |
| `INST-ENG-004` | #2100, #2015 |
| `INST-BOOT-008` | #3206, #2493, #2385, #2248, #2247, #2136, #2134, #2079, #2055, #2054, #1953, #1936, #1935, #1934, #1933, #1875, #1850, #1842, #1828, #1814, #1802, #1705, #1701, #1698, #1697, #1690, #1663, #1661, #1658, #1586, #1583, #1579, #1578, #1572, #1535, #1510, #1509, #1478, #1439, #1435, #1378, #1365, #1351, #1345, #1337, #1336, #1323, #1293, #1261, #1259, #1243, #1224, #1177, #1164, #1157, #1153, #1133, #1124, #1118, #1113, #1109, #1101, #1093, #1092, #1090, #1076, #1044, #1011, #1000, #979, #948, #941, #929, #926, #903, #900, #897, #885, #876, #875, #873, #863, #853, #850, #821, #809, #806, #804, #802, #796, #786, #785, #769, #755, #746, #743, #740, #738, #737, #736, #735, #729, #728, #727, #725, #724, #723, #721, #689, #682, #681, #680, #663, #662, #652, #638, #636, #628, #627, #624, #623, #620, #619, #618, #613, #610, #602, #597, #592, #591, #588, #585, #584, #583, #582, #580, #571, #570, #567, #555, #553, #548, #546, #541, #540, #535, #534, #522, #521, #520, #515, #514, #491 |
| `INST-UX-002` | [#3745](https://github.com/debpalash/VoiceStudio/issues/3745) |
| `INST-SRC-002` | #1922, #1357, #1341, #1294 |
| `INST-SRC-003` | #2113, #2052, #1438, #962 |
| `INST-DOCK-005` | #251, #249 |
| `INST-UPD-002` | #1089 |
| `INST-UX-003` | #57, #35, #4 |

## Keeping this list current

- **New report of a known problem:** add its number to that issue's row, update the count, and add the category label.
- **New problem:** add a row with the next number in its category, keeping the list in severity order.
- **Fix merged:** set the status to `Fixed on main` and link the PR.
- **Release cut:** change `Fixed on main` to `Fixed in release` and update the release named at the top of this page.

`tests/test_install_issue_tracker.py` checks the IDs, order, statuses, links and report counts.
