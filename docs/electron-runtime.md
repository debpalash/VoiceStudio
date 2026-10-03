# Electron runtime setup

> **Historical context:** this page was written while the Electron and Tauri apps
> coexisted. Mentions of Tauri helpers, pages, tests and regression results describe
> that migration period; the Tauri shell has since been removed and the shared code
> now lives in `electron/src/shared/`. Existing Tauri installs: see the
> [migration guide](electron-migration.md).

The top bar places a GitHub Star button immediately right of Get Pro, showing the exact star count from GitHub's small public endpoint. It refreshes when shown and every 20 minutes while visible; browser caching keeps repeat requests small. A bundled count remains visible if GitHub is unavailable. Requests send no credentials or referrer, and clicking the shortcut opens GitHub in the external browser. The footer shows the project website icon, then Join Discord immediately left of X. Discord opens the community invite in the external browser. The footer includes one X button with a hover popup of follow cards for @idebpalash and @voicestudiosh, plus a heart shortcut to donation options, immediately left of the footer collapse control. The profile popup also opens by click or keyboard; follow links open in the external browser. Profile cards span the popup and bundle their images locally for offline display. The heart opens compact cards with locally bundled brand icons for Ko-fi, PayPal, and GitHub stars, plus the full Support page. Both popups support click and keyboard access. Icons have localized accessible labels. The footer chevron collapses the bar; a slim expand control restores it. Get Pro remains in the top bar.

Low-disk warnings in the notification menu open Settings > Storage, where users can inspect disk use and clear temporary files.

The development launcher watches main-process and preload source changes as well as renderer changes, rebuilding and restarting or reloading Electron to keep IPC contracts synchronized. After changing the launcher itself, stop and restart `bun run dev` once; a renderer refresh alone cannot update the running main process.

The development shell keeps DevTools closed by default. Open it with the normal
Electron shortcut, or set `VOICESTUDIO_OPEN_DEVTOOLS=1` before `bun run dev`.
This prevents Chromium's detached performance monitor from injecting failing
timers into the app execution context during route transitions.

Source launches verify required imports offline, then use the repository’s prepared `.venv` interpreter directly; startup and Retry never run dependency synchronization. Run `bun run setup:api` once before `bun run dev`, and again after Python dependencies change. Let setup finish before launching the app so a large PyTorch download is not interrupted by the backend health-check deadline. An explicit `OMNIVOICE_BACKEND_CMD` remains responsible for its own environment.

Packaged startup first checks for a running VoiceStudio backend. If one answers, the shell attaches without creating or modifying a Python environment.

Otherwise, an installation matching the bundled `pyproject.toml` and `uv.lock` is required. The setup view explains the dependency download and waits for **Install local runtime**. Startup and Retry do not install automatically. Model downloads remain separate engine actions.

The setup view shows the runtime destination before downloading. The user can keep the default location, choose a folder with the native directory picker, or restore the default. The install action first checks that target filesystem for 9 GiB of free space (5 GiB for the CPU-only install described below) and verifies write access. It then copies bundled backend/package sources into the selected `VoiceStudio/project` directory, preserving the interpreter and unrelated files. Release packages include the same pinned official Astral uv 0.12.13 executable as Tauri, so first launch skips the uv network bootstrap; development builds fall back to an installed uv or an app-private download. `uv sync --frozen --no-dev --managed-python --python 3.11` installs dependencies. Fresh installs and explicit Clean & Retry use an app-private uv-managed interpreter rather than borrowing another application's Python from PATH. Existing interpreters are reused during repair when the Python 3.11 probe succeeds. Setup separately probes SentencePiece and the PyTorch/torchaudio/torchvision stack, evicting and reinstalling only the failing group from the app-private uv cache even when its package metadata still looks installed. Missing non-native dependencies use ordinary frozen sync without evicting healthy native wheels. A failed interpreter probe selects managed Python; normal startup does not silently replace environments. Setup verifies all required imports before marking the runtime ready. On a CUDA host, setup also installs the pinned cuDNN 8 compatibility wheel required by CTranslate2; Linux setup clears the obsolete executable-stack request from the installed CTranslate2 library for hardened kernels. The runtime is marked ready only after the compatibility files and imports validate. No environment is created under the read-only application bundle.

**PyTorch flavour.** The lock pins the CUDA build of PyTorch on Linux and Windows x64, which is several GB of wheels (plus about 3 GB of `nvidia-*` packages on Linux) that a machine without an NVIDIA driver can never use. Setup therefore chooses the flavour from the host: `OMNIVOICE_TORCH_VARIANT=auto` (the default) installs the CPU build on Linux/Windows x64 hosts where no NVIDIA driver is found (`nvcuda.dll`/`nvidia-smi` on Windows; `/proc/driver/nvidia`, `/dev/nvidiactl` or `libcuda` on Linux, including WSL) and keeps the lock's CUDA wheels everywhere else. It runs the frozen sync with `--no-install-package` for torch, torchaudio, torchvision and every `nvidia-*` package in the lock, then `uv pip install torch==2.8.0+cpu torchaudio==2.8.0+cpu torchvision==0.23.0+cpu` from the PyTorch CPU index. `cuda`, `cpu` and `rocm` force a flavour. An environment installed before this behaviour existed stays valid on an auto-detected CPU host; a CPU environment is reinstalled when an NVIDIA driver later appears. macOS and Linux arm64 already receive CPU/MPS wheels from PyPI and are unchanged.

**Windows on ARM (experimental).** The Electron shell and native helper are built for ARM64, but PyTorch publishes no `win_arm64` torchaudio or torchvision wheels, so the Python runtime is the x64 interpreter (`uv sync --managed-python --python cpython-3.11-windows-x86_64`) running under Windows' emulation layer, always with the CPU build of PyTorch. A native ARM64 interpreter left behind by an earlier attempt is rejected and replaced. Setup shows a "Windows on ARM" notice; nothing is GPU/NPU accelerated.

Setup content is anchored below the fixed brand header with fluid top padding. Its primary action therefore stays stationary while native status and font metrics settle, including Wayland and short-window launches.

The branded setup view reports environment checks, uv download, dependency installation and final verification from real runtime phases. During dependency installation it shows the resolved package count, announced transfer bytes, remaining data, current rate, ETA and the largest unfinished package; stale rate and ETA values disappear when the transfer pauses. Once the last announced artifact arrives, the view switches to package installation instead of leaving that artifact displayed as if its download stalled. It also streams the latest setup activity while preserving the full log disclosure. Retry and Clean & Retry preserve uv's verified download cache outside the replaceable Python project, so rebuilding a broken environment does not repeat the multi-gigabyte transfer.

Cancel stops the active process tree or download and returns to setup. Failure preserves logs and offers Retry plus a confirmed Clean & Retry. Clean recovery removes only the dedicated Python project in the default runtime or a custom runtime Electron has claimed after beginning installation; compatible Tauri environments and unowned custom locations are refused. Incomplete environments or a changed dependency graph require setup again. Source updates replace old bundled modules while preserving the venv.

Verification: runtime regression tests cover consent gating, dependency changes, incomplete environments, failed repair, cancellation before download, and source replacement. `node electron/tests/packaged-smoke.mjs --setup` verifies the first-run view without initiating an installation. `--install` performs the explicit isolated runtime installation and verifies the packaged renderer's same-origin connection to its managed backend.

First run has four steps: System check, Model packs, Privacy, and Enter studio. Model packs reuse the existing performance tiers and install only missing supported models after an explicit click. Installed state, remaining download size, disk checks and installation progress appear in one place. Advanced mode reveals individual models, engine tuning, privacy settings and recovery tools; failed system checks still expose recovery without enabling Advanced. The final Enter Studio step opens Advanced expanded, showing optional dictation permissions and shortcuts; either toggle can collapse them. Dictation and its permissions remain optional and available in Settings. Navigation requires passing preflight, required models and the privacy choice; entering the studio opens the first-voice demo. On narrow windows the step navigation moves above the content and the footer stays visible.

A subsequent packaged launch also reused that installed runtime and started a managed backend. Electron discovers compatible Tauri default, custom and portable runtime locations and can reuse them without a download when the interpreter and both frozen dependency manifests match. Its location record distinguishes reused environments from custom runtimes Electron creates. Uninstall includes only an Electron-owned custom runtime; it never claims or removes a reused Tauri runtime.

While the native supervisor is attaching, starting, restarting or reporting a crash, Electron pauses
renderer queries through TanStack Query's online state. Active queries resume when the supervisor
publishes `ready`; this avoids a burst of predictable 502/503 requests while retaining the backend
failure screen, restart action and logs.

Source-mode shutdown closes active renderer-proxy streams as well as its listening socket. A live
backend response or keep-alive connection therefore cannot strand Quit, Restart, or an isolated
native lifecycle check.

The long-running supervisor probes the canonical compact `/health` contract. Full `/system/info`
hardware, storage and settings data is fetched only by views that use it, avoiding repeated payload
work while Dubbing or another model-heavy workflow is active.

`/model/status` binds its checkpoint and UTC load timestamp to the model instance that actually
became resident. Engine Ready and diagnostics therefore retain the loaded model identity across
renderer refreshes, even if the configured checkpoint changes before the resident model unloads.
Idle status clears both fields, and failures resolving preferences cannot break the recovery surface.

If another VoiceStudio shell replaces Electron's managed backend, Electron pauses renderer requests
for a bounded handoff window and attaches to the healthy replacement. The intentional ownership
transfer does not create a crash record or strand the app on its recovery screen.

If no replacement appears during that ten-second window, the owned exit becomes a persisted crash
with its exit code, timestamp, app version, uptime and bounded log tail. The recovery screen exposes
those details, and the next Electron launch restores them while an intentional later quit stays out
of the journal. Opening the details marks that crash seen and clears its repair-attention indicator,
while retaining the evidence for bug reports; a later crash starts unacknowledged. `node electron/tests/native-crash-journal-smoke.mjs` exercises that complete native
process/relaunch path with an isolated profile.

A fresh Windows environment has also completed a real frozen dependency installation and import check. The current packaged app repaired the isolated runtime after its dependency manifest changed, started backend 0.5.2, then relaunched against the same runtime without setup; renderer, preload bridge and same-origin API passed, and deliberate shutdown removed the run sentinel. A separate empty-runtime, empty-uv-cache and empty-Hugging-Face-cache run completed dependency setup, required-model download, onboarding and a paused 341804-byte first-sound WAV before shutting down cleanly. The segmented model downloader keeps both the canonical cache blob and snapshot pointer on Windows, using a hard link when symlinks are unavailable, so model loading does not download the same multi-gigabyte weight again.

Ubuntu 26.04 under WSL completed the current frozen dependency graph from an empty uv cache: 227 packages and a 7.9 GiB runtime. The current unpacked Linux package used its bundled uv 0.12.13, completed that explicit runtime installation from empty dependency and model caches, downloaded the 3,267,470,260-byte required OmniVoice model to a host-backed cache after preflight correctly rejected the space-constrained WSL disk, loaded it on the RTX 4090, and produced a paused 341,804-byte first-sound WAV before shutting down cleanly. A clean AppImage profile also completed the visible explicit setup action from the dependency cache and connected its packaged renderer to backend 0.5.2 under WSLg Wayland. The same profile relaunched without installation, reused its managed runtime, and cleared the backend run sentinel on both deliberate exits. These runs exposed and fixed Electron's missing POSIX nested-operation ownership descriptor.

Remaining verification: fully uncached packaged installation through first generated sound on macOS, plus native directory-picker interaction. Use `--install` only for an explicit integration run: it installs real dependencies into an isolated profile. `--first-sound` continues through required-model setup and verifies a valid paused WAV. Set `VOICESTUDIO_TEST_PROFILE` to that profile to verify a subsequent launch.

Deliberate shutdown remains available during deferred native and ML imports. Electron can therefore retire the backend run sentinel before Windows performs its bounded process-tree termination, so closing during “Preparing GPU libraries” does not become a false crash warning on the next launch. A packaged Windows acceptance quits inside that gated startup phase and verifies the sentinel is gone.

The backend's default desktop origins include `app://voicestudio` alongside the Tauri origins. HTTP CORS and WebSocket checks share this list. An explicit `OMNIVOICE_ALLOWED_ORIGINS` overrides the defaults; deployments using it must include the Electron origin to allow native live dictation. Non-loopback WebSocket connections still require remote authorization.

Clean recovery serializes cleanup against install, region and location actions. Filesystem failures return to setup with current logs and access guidance. Closing or restarting the app during cleanup prevents that stale action from starting a new installation.

An installation-in-progress marker persists through interruption or verification failure. Both readiness and legacy-environment compatibility reject marked projects until a successful import check completes, so Retry cannot bypass a partial installation merely because its copied dependency manifests match. Startup-path (`.pth`) repair is best-effort during setup: a locked file does not skip the Python import check, and a failed import check still prevents the runtime from being marked ready.

macOS packaging includes the microphone purpose description shared with Tauri and the audio-input entitlement for the app and helper processes, alongside Electron's runtime entitlements. This is checked by the packaging contract; an actual signed macOS microphone run remains required.

Setup reserves its active operation before asynchronous compatibility checks. Repeated install clicks cannot race those checks, cancellation invalidates preflight, and each explicit attempt starts with a fresh elapsed timer and activity log.

If the renderer remains empty after three bounded reloads, Electron paints an asset-independent localized recovery page. Its retry clears only Chromium's display cache, schedules a relaunch, and still shuts the managed backend down cleanly; voices, projects, models and preferences remain untouched.

Branding remains in a fixed native title row while onboarding status loads, installation runs or recovery needs retry. Runtime details scroll independently below it; installer phases wrap into two columns on narrow windows, so a growing progress/log surface cannot clip the wordmark or window controls.

On Windows and Linux, the collapsed workspace sidebar shows the VoiceStudio icon at the top. Use the toggle beside the page title to expand the sidebar. macOS retains its existing sidebar control.

Every main workspace header exposes the same sidebar toggle, including pages that automatically collapse the voice library at narrow widths. The control reflects the visible sidebar state and explicitly expands it for the current workspace.

The shared video player renders Vidstack's poster before playback, including the Dub source thumbnail, and hides it once playback starts. Play requests made while the video is loading wait for the provider to become ready, including timeline preview requests.

On Linux Wayland systems where Chromium logs `eglCreateImage failed` / `OzoneImageBacking` and video or window contents flicker, launch Electron with `--disable-gpu-compositing`. For source development, run `bun run dev:software-compositing` from `electron/`. This opt-in uses software window compositing while leaving backend CUDA inference available; it does not disable acceleration for other installations. It requires a full Electron restart, not a renderer reload. A refused connection to port 3903 instead means the development proxy is stopped; restart the Electron development process to restore it.

Secondary workspace sidebars resize from their right edge up to 40% wider than the previous limits (515 / 616 / 750 px by size), while reserving space for the main workspace. Widths are saved per size in the app profile’s local storage and restored on navigation and restart. Double-click the divider to reset the width; focus it and use arrow keys for keyboard resizing. Sidebar sections fill the resized width, and video controls adapt to the player width.

Runtime download byte progress matches complete package identifiers, so concurrent downloads such as `torch` and `torchvision` retain separate received bytes and totals. Package names use the same case-insensitive, hyphen/underscore/dot normalization for planned, active and finished downloads. Unknown package progress does not change another package’s planned size.

### Windows proxy bootstrap

Electron translates enabled WinINET `ProxyServer` maps (`http=…`, `https=…`,
`socks=…`) into proxy URLs for both the HTTPS installer download and the dependency-install subprocess. Installer redirects retain proxy and bypass rules; downloads have a 60-second deadline and a 2 MiB limit. SOCKS maps use
`socks5h://` so the proxy resolves download hostnames. Explicit `HTTP_PROXY`,
`HTTPS_PROXY`, or `ALL_PROXY` settings, including lowercase forms, take priority.
Loopback hosts remain excluded, and existing `NO_PROXY` entries are retained.
Proxy credentials are never logged by this normalization.

Simple WinINET bypass hosts and `*.domain` suffix rules are retained. Windows-only
bypass patterns such as `<local>` stop setup with localized proxy configuration
guidance before uv runs; dropping them would silently change routing semantics.
Fully quit Electron, set an explicit proxy URL and the intended `NO_PROXY` exclusions in a terminal, then launch Electron from that same terminal. Retrying or relaunching from the existing process cannot pick up new environment variables. PAC remains handled by the existing system networking behavior; use the actual proxy protocol,
not the `socks=` registry syntax. This repair applies to packaged runtime setup;
it does not change browser networking or global Windows proxy settings.

## Home and sidebar

Home opens with the “VoiceStudio.sh Open Source” title and a right-aligned cluster of round avatars in up to three right-aligned, tapered rows filled from right to left (currently 5, 3, 2 icons) for human GitHub contributors with more than 10 repository commits. All qualifying avatars are shown; the final “All” icon links to the repository contributor page. The cluster scales down and the heading wraps as the window narrows. Contributor avatars link to their GitHub profiles; hovering or activating Palash's avatar opens both GitHub and X links. Keyboard and touch users can open that card with the avatar button. The contributor list and images are bundled offline in `features/home/home-contributors-data.ts` and `features/home/contributors/`, using the GitHub contributors API snapshot dated 2026-09-28; refresh both from that API when updating credits, excluding bots and counts of 10 or fewer. Home emphasizes Voice cloning, Design, and Dubbing, with compact horizontal cards for Stories, Audiobook, Gallery, Transcribe, Workflows, and Tools. Primary cards use restrained color accents and local vector illustrations; all cards have visible navigation arrows, keyboard focus states, and reduced-motion support. Each of the nine Home icons uses a local SVG illustrating its feature. CSS animates only transforms and opacity on hover or keyboard focus, for two 1.6-second cycles; idle icons and reduced-motion mode remain static. No timers, animation library, or continuous background loop is used. Descriptions explain each workflow; Stories creates multi-voice scenes, while Audiobook organizes chapters. Saved voices and projects remain on Home; Recent takes appears in the sidebar. Sidebar filters wrap at narrow widths and Clear remains an accessible icon button with confirmation. Engine controls show family labels, and the speed/quality control shows its current tier, with Auto as a separate toggle.

The VoiceStudio title links to `https://voicestudio.sh`. Hover or keyboard focus shows a preview hint; clicking opens a modal below the clicked item, constrained to the right content area of the main window with an address bar, back/forward, reload/stop, and an **Open in browser** list. The list includes the system default and common browsers detected in standard installation locations (Windows and macOS) or executable PATH entries (Linux). Browser/web deployments open the title link in a new browser tab instead.

Website requests begin only after clicking. Remote pages use a sandboxed Electron WebContentsView with no preload, Node access, app bridge, or device permissions. Links and redirects stay restricted to HTTP(S); popup links reuse the same view. The browsing session is temporary and separate from app storage. The sidebar and workspace remain visible behind the modal. Its native page follows the modal bounds on resize and zoom, leaving the toolbar and surrounding workspace uncovered. **Back to Studio**, Escape, or clicking outside the modal disposes the embedded web contents, clears its session data, and restores the existing workspace and keyboard focus without navigating or remounting it. The browser can be reused for future cloud signup and other web flows; authentication callbacks are not implemented yet. Sites requiring device permissions or external authentication may need **Open in browser**. The browser list passes URLs as arguments to detected executables, never to a command shell.

The Dubbing workspace keeps its existing `/dub` route and saved project format; the name change affects visible feature labels only.
