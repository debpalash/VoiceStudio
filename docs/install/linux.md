# VoiceStudio — Install on Linux

## Electron desktop (current)

From the repository root, install Bun and uv, then run:

```sh
bun install
bun run setup:api  # prepare Python dependencies before starting Electron
bun run dev
```

Use `bun run desktop-prod` to build and launch Electron, or `bun run dist`
to create local installers without publishing. The app manages its backend.
See [Electron setup](../../electron/README.md) and [migration notes](../electron-migration.md).

## Legacy Tauri installation and troubleshooting

The instructions below apply to the sunset Tauri app and existing Tauri installers.

This page is self-contained: follow it top to bottom and you'll end up with a
working VoiceStudio install on a Debian / Ubuntu / Fedora / Arch host.

## Prerequisites

### Using the AppImage

- **Linux x86_64 with glibc 2.39+** and a desktop session (X11 or Wayland)
  capable of running a Tauri / WebKitGTK app.
- **~10 GB free disk** for the app, its Python environment, and model weights.
- Optional: an **NVIDIA driver** for CUDA GPU acceleration — the app runs
  CPU-only without one. For AMD GPUs see [AMD GPU (ROCm)](#amd-gpu-rocm).
That's it — Python, FFmpeg/FFprobe, yt-dlp, and the model weights are bundled
or bootstrapped by the app itself on first launch. No toolchain needed. (If no
FFmpeg resolves anywhere, the app downloads its own checksummed static build
in the background during setup; **Settings → Audio tools** shows exactly which
binaries are in use and lets you override them or update yt-dlp.)

### Building from source

Everything above, plus the toolchain:

- **git** — `sudo apt install git` (Debian/Ubuntu), `sudo dnf install git` (Fedora), or `sudo pacman -S git` (Arch).
- **curl** — usually preinstalled; used by the Bun and rustup install one-liners below.
- **Python 3.11+** — typically `sudo apt install python3.11` on Debian/Ubuntu,
  `sudo dnf install python3.11` on Fedora, or already installed on Arch.
- **Bun** — `curl -fsSL https://bun.sh/install | bash`.
- **Rust / Cargo** — `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh` or via your package manager (e.g., `sudo apt install rustc cargo`).
  If you use rustup, reopen the shell or source `"$HOME/.cargo/env"` before running `bun run tauri:desktop-prod`.
- **GTK/WebKit deps** for the Tauri shell:

  ```bash
  # Debian / Ubuntu
  sudo apt-get update
  sudo apt-get install -y \
    libwebkit2gtk-4.1-dev libgtk-3-dev libpango1.0-dev libcairo2-dev \
    libsoup-3.0-dev libgdk-pixbuf-2.0-dev \
    libayatana-appindicator3-dev librsvg2-dev libssl-dev libxdo-dev \
    gstreamer1.0-plugins-good \
    libasound2-dev build-essential curl wget file

  # Fedora
  sudo dnf install webkit2gtk4.1-devel libappindicator-gtk3-devel librsvg2-devel openssl-devel libxdo-devel gstreamer1-plugins-good

  # Arch
  sudo pacman -S --needed base-devel webkit2gtk-4.1 libayatana-appindicator librsvg openssl xdotool gst-plugins-good
  ```

- Optional: a **Hugging Face token** for diarization + the larger TTS engines
  (see [docs/setup/huggingface-token.md](../setup/huggingface-token.md)).

## Install (from source)

One-liner (installs prerequisites, clones, and builds; WSL works too):

```bash
curl -fsSL https://voicestudio.sh/install | sh
```

Or manually:

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
source "$HOME/.cargo/env"  # only needed in a shell opened before rustup finished
bun tauri               # development build with hot reload
```

Use `bun run tauri:desktop-prod` instead when you need to build and launch the
production bundle. Both commands create the Python environment via `uv`, sync
dependencies, and start the backend automatically; do not start the backend in
a second terminal.

The first Rust build takes longer because Cargo compiles the Tauri shell. If it
fails with `Package gdk-3.0 was not found`, `pango.pc` missing,
`libsoup-3.0` missing, or `javascriptcoregtk-4.1` missing, install the complete
Debian/Ubuntu package block above. Those messages mean the development
libraries are absent, not that `PKG_CONFIG_PATH` needs changing. Verify them
with:

```bash
pkg-config --exists \
  gdk-3.0 pango cairo libsoup-3.0 javascriptcoregtk-4.1 gdk-pixbuf-2.0 \
  && echo "Tauri system libraries are ready"
```

`bun tauri` also checks the native `libxdo` linker input and GStreamer's
`autoaudiosink` before starting. The latter is required even if you do not plan
to record: WebKitGTK 2.52 aborts its renderer when a page creates an audio
element without that plugin, which otherwise turns a running app blank. The
launcher prints one distro-specific install command when either dependency is
missing.

The first app launch downloads model weights on demand. Subsequent launches
reuse the Rust build, Python environment, and installed models.

## Wayland dictation shortcut

System-wide dictation on Wayland uses the standard
`org.freedesktop.portal.GlobalShortcuts` interface. Your desktop must run
`xdg-desktop-portal` and a portal backend that implements that interface. The
desktop owns the consent dialog and may let you replace VoiceStudio's preferred
key combination.

VoiceStudio binds the replacement before saving a changed shortcut. If consent
is declined or the portal is unavailable, Settings keeps the previous shortcut
and reports the registration failure. The configured shortcut still works while
the VoiceStudio window is focused.

While a dictation is running, VoiceStudio shows a small always-on-top capsule.
It sits near the bottom of the screen your pointer is on — except on Wayland,
where the protocol gives applications no say in their own placement and the
compositor decides where it appears. The capsule works the same either way; only
its position is out of the app's hands there.

Wayland does not expose a portable identity for the app focused at shortcut
down, so VoiceStudio safely leaves the complete transcript on the clipboard and
the pill says **Copied** instead of risking insertion into a different app.

Advanced users can opt into current-focus insertion with
`VOICESTUDIO_WAYLAND_UNTARGETED_INSERT=1`. wlroots compositors such as Sway and
Hyprland use `wtype`; KDE Plasma and GNOME can use `dotool` or `ydotool` to
paste the Unicode clipboard payload. The
opt-in targets whichever client owns keyboard focus when transcription
finishes, not necessarily the app where dictation started. Tray-started
dictation remains copy-only. `dotool` needs direct write access to
`/dev/uinput` (normally through a distribution udev rule/group). `ydotool`
1.0+ needs the `ydotoold` daemon running with that access and its socket
available to the desktop user. VoiceStudio skips either helper when its
readiness check fails.

If the global shortcut stops working, restart your desktop's portal service,
then save the shortcut again in **Settings → Hotkey** to reopen consent. Portal
packages and support vary by desktop; use the backend recommended by your
distribution rather than running multiple portal backends in the same session.
See the portal project's [service integration checks](https://flatpak.github.io/xdg-desktop-portal/docs/system-integration.html)
and the Arch Linux [backend compatibility table](https://wiki.archlinux.org/title/XDG_Desktop_Portal#List_of_backends_and_interfaces)
for concrete service and desktop-backend checks.

## Install (AppImage)

Download the latest AppImage from the
[Releases page](https://github.com/debpalash/VoiceStudio/releases/latest),
make it executable, and run:

```bash
chmod +x VoiceStudio.Studio_*.AppImage
./VoiceStudio.Studio_*.AppImage
```

No FUSE? Use `--appimage-extract-and-run`:

```bash
./VoiceStudio.Studio_*.AppImage --appimage-extract-and-run
```

## .deb package

Not currently published: `.deb` bundling is disabled in the release pipeline
because of a `tauri-cli` bug (`Failed to create control scripts`) — see the
comment in `.github/workflows/release.yml` for the tracking note. The
AppImage above is the supported Linux install path until a `tauri-cli`
version resolves it. `apt install`-able `.deb`s shipped before v0.3 (see
[.deb ffprobe conflict](#deb-ffprobe-conflict) below) if you're upgrading
from one of those.

The desktop app uses these canonical paths (kept in sync with
`scripts/desktop-prod.sh` by the docs-drift CI gate):

<!-- validate -->
```bash
APP_ID="com.debpalash.omnivoice-studio"
APP_NAME="VoiceStudio"
```

## AppImage white screen / EGL errors (Fedora 44, Ubuntu 24.04+, 26.04)

<a id="appimage-white-screen-on-fedora-44--ubuntu-2404"></a>

Two separate WebKitGTK rendering issues land the Tauri window as a
fully-white frame with no UI. Which one you have depends on your WebKitGTK
version (`pkg-config --modversion webkit2gtk-4.1` prints it).

**Modern WebKitGTK (2.48+ — Ubuntu 24.04 and newer, incl. 26.04): try this
first.** WebKit's DMA-BUF renderer fails against some GPU drivers; the
terminal typically shows:

```
Could not create default EGL display: EGL_BAD_PARAMETER
```

Disable the DMA-BUF renderer before launching:

```bash
WEBKIT_DISABLE_DMABUF_RENDERER=1 ./VoiceStudio.Studio_*.AppImage
```

**WebKitGTK 2.44 / 2.46 (Fedora 44, Ubuntu 24.04 at release):** a
compositing-mode regression blanks the surface on first paint. Disable
compositing mode instead:

```bash
WEBKIT_DISABLE_COMPOSITING_MODE=1 ./VoiceStudio.Studio_*.AppImage
```

VoiceStudio's AppRun launcher autodetects the broken 2.44/2.46 range and sets
this second variable for you (shipped in v0.3+). The manual env-var path
remains the documented fallback when running from a checked-out source tree.

**Last resort** — if neither variable alone helps, force software rendering
(slower, but always paints):

```bash
WEBKIT_DISABLE_DMABUF_RENDERER=1 LIBGL_ALWAYS_SOFTWARE=1 ./VoiceStudio.Studio_*.AppImage
```

### If no environment variable helps at all (Mesa 26.1+)

On a host with **Mesa 26.1 or newer** — Arch/CachyOS, and rolling distros
generally — none of the variables above make any difference, including
`WEBKIT_DMABUF_RENDERER_FORCE_SHM`, `WEBKIT_SKIA_ENABLE_CPU_RENDERING`,
`EGL_PLATFORM=surfaceless` and `MESA_LOADER_DRIVER_OVERRIDE=swrast`. That is
expected: the failure is in EGL **display creation**, which happens before
WebKit consults any rendering-path flag, so there is nothing left for a flag
to change.

The cause is a version pairing, not a bug in either half. The AppImage bundles
a WebKitGTK built on Ubuntu but ships no `libEGL` of its own, so that bundled
WebKit runs against *your* Mesa. On Mesa ≥ 26.1 it calls
`eglGetPlatformDisplay()` in a way the newer driver rejects. Your distro's own
WebKitGTK is fine, because it was compiled against the Mesa you are running —
which is why building from source works on the same machine.

**From v0.4.1 the AppImage handles this itself:** when your system has a
WebKitGTK at least as new as the bundled one, the launcher lets your copy take
precedence, and the bundled libraries fill in only what your system lacks.

That check reads your WebKit version from `pkg-config`, which is only installed
alongside the **development** package. If you have the runtime but not the dev
package, the launcher can't compare versions and keeps the bundled copy — so
tell it explicitly:

```bash
OMNIVOICE_PREFER_SYSTEM_WEBKIT=1 ./VoiceStudio.Studio_*.AppImage
```

(Set it to `0` to force the bundled copy — useful if your distro's WebKitGTK is
older than ours and you'd rather keep the newer bundled one.)

If you are on v0.4.0 or older, either update or build from source:

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
bun run tauri:desktop-prod
```

Tracking issues: [#62](https://github.com/debpalash/VoiceStudio/issues/62),
[#961](https://github.com/debpalash/VoiceStudio/issues/961),
[#1258](https://github.com/debpalash/VoiceStudio/issues/1258).

## AppImage: "No microphone found" while the raw binary records fine

Recording from the AppImage fails with *"No microphone found. Connect or enable
a microphone and try again."* — but `pactl list short sources` shows your
microphones, `gst-launch-1.0 pulsesrc … ! fakesink` captures, and running
`frontend/src-tauri/target/debug/omnivoice-studio` directly records without
trouble. `GST_DEBUG=2` shows the real message:

```text
WARN GST_REGISTRY gst_registry_binary_check_magic:
  Binary registry magic version is different : 1.23.90 != 1.3.0
GStreamer element appsink not found. Please install it.
```

Same shape as the blank-window problem above, in a different library. The
AppImage bundles the GStreamer **core** (WebKitGTK links it) but not its
**plugins** — those are loaded dynamically at runtime, so the packaging step
cannot see them to copy. The bundled core then reads *your* plugin directory,
whose plugins were built against *your* core, the version check rejects them,
and the scan produces nothing. `appsink` is one of the elements that goes
missing, and it is the one WebKit needs to hand over a capture stream — so
`getUserMedia()` reports no device. Your raw binary works because it uses your
core with your plugins, which agree.

**From v0.4.3 the launcher prefers your system's GStreamer**, which is the only
core that can match the plugins that will actually load. It does that by
preloading that one library (`LD_PRELOAD`) rather than by putting your system
library directory ahead of the bundle — your GStreamer shares that directory
with most of the system, so hoisting it would quietly replace every *other*
bundled library too. If your distro's GStreamer is itself broken and you would
rather fall back to the bundled core:

```bash
OMNIVOICE_PREFER_SYSTEM_GSTREAMER=0 ./VoiceStudio.Studio_*.AppImage
```

The launcher checks first that your GStreamer can actually load alongside the
libraries the AppImage bundles — a system core built against newer GLib than we
ship would fail to load and take the whole app down with it, which is worse than
a missing microphone. If that check fails it prints a warning, keeps the bundled
core, and the app still starts (with capture still broken); building from source
avoids the mismatch entirely.

The AppImage also keeps its plugin-scan cache to itself, at
`~/.cache/VoiceStudio/gstreamer-registry.bin`, rather than in the shared
`~/.cache/gstreamer-1.0/`. GStreamer names that shared file by architecture
alone, so two cores of different versions overwrite each other's — which both
makes this failure depend on whichever application ran last, and lets the
AppImage corrupt the cache every other GStreamer app on your machine reads.

If you set `XDG_CACHE_HOME`, the path follows it
(`$XDG_CACHE_HOME/VoiceStudio/gstreamer-registry.bin`); `~/.cache` is the default
when it is unset.

Tracking issue: [#1333](https://github.com/debpalash/VoiceStudio/issues/1333).

## .deb ffprobe conflict

<a id="deb-ffprobe-conflict"></a>

Pre-v0.3 `.deb` packages installed `ffprobe` into `/usr/bin/ffprobe` and
clobbered the system copy on some distros. v0.3+ relocates the bundled
binary into `/usr/lib/omnivoice-studio/bin/ffprobe` and the `postrm` script
runs `dpkg --search` to undo the old conflict on upgrade. If you upgraded
from a pre-v0.3 .deb and `ffprobe -version` now reports the wrong binary,
re-install the system package:

```bash
sudo apt install --reinstall ffmpeg
```

## Restricted networks (China / Russia)

If `uv` times out fetching the python-build-standalone tarball or PyPI:

```bash
# Use a faster Python source mirror (China only — verify a current mirror)
export UV_PYTHON_INSTALL_MIRROR=https://ghproxy.com/https://github.com/astral-sh/python-build-standalone/releases/download

# Use a PyPI mirror
export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

# Or skip the download entirely if you have a compatible system Python
export UV_PYTHON_PREFERENCE=only-system

# Be tolerant of slow links
export UV_HTTP_TIMEOUT=120
export UV_HTTP_RETRIES=5
```

The Phase 3 install milestone (INST-07..11) ships an OS-level mirror cascade
that picks these defaults automatically; for v0.3 set them by hand.

## AMD GPU (ROCm)

<a id="amd-gpu-rocm"></a>

ROCm support is **Linux-only and opt-in**. The **default install ships the
CUDA build** of PyTorch (the `pytorch-cuda` index in `pyproject.toml`), so on
an AMD-only machine `torch.cuda.is_available()` is `False` and VoiceStudio runs
on CPU until you opt into the ROCm variant.

> **Running in Docker or Podman instead?** There's a prebuilt ROCm image —
> `ghcr.io/debpalash/omnivoice-studio:rocm` — with GPU acceleration out of the
> box; see [docker.md](docker.md#pull-and-run-amd-gpu--rocm). The rest of this
> section is about source/desktop installs. (On Windows there is no ROCm path
at all — PyTorch publishes no Windows ROCm wheels; see
[windows.md](windows.md#gpu-support).)

Three ways to opt in, in order of preference:

**1. First-run setup screen (recommended).** On Linux the setup screen's
**Compute** card offers **"AMD GPU (ROCm, Linux)"** next to the default
**Auto**. When VoiceStudio detects an AMD GPU *and* the ROCm userspace
(`/opt/rocm` present, or `rocminfo` on PATH), the ROCm option is pre-selected;
with an AMD GPU but no ROCm runtime it stays offered-but-unselected — install
ROCm first (or continue on CPU). Choosing ROCm makes the bootstrap reinstall
`torch`/`torchaudio` from the ROCm wheel index
(`https://download.pytorch.org/whl/rocm6.4` by default) right after the
dependency sync — matched to the app's pinned `torch==2.8.0` (the rocm6.2
index only ever published up to torch 2.5.1, so it silently failed the
reinstall and left the CPU-only CUDA build in place).

**2. Environment variable (existing installs / headless / source).** Set
`OMNIVOICE_TORCH_VARIANT=rocm` before launching — the next bootstrap performs
the same ROCm reinstall. Source installs honour it too:
`OMNIVOICE_TORCH_VARIANT=rocm bun run tauri` swaps torch right after
`uv sync` and launches the backend without re-syncing, so the wheel is not
reverted on the next start (#1665). Without the variable, `bun run tauri`
restores the lockfile's CUDA build — a hand-swapped ROCm wheel does not
survive it. `OMNIVOICE_TORCH_INDEX=<url>` overrides the wheel
index when you need a different ROCm version — e.g. AMD publishes newer
driver-matched builds (7.2.x) at `repo.radeon.com` as a `--find-links` page
rather than a PyPI-style index:
```bash
uv pip install --reinstall torch==2.8.0 torchaudio==2.8.0 \
  --find-links https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2.4/
```
run that manually if you want a specific ROCm point release; the
`OMNIVOICE_TORCH_INDEX` env var only accepts a PEP 503 index URL, not a
find-links page. If the reinstall fails (network, unsupported card), VoiceStudio
keeps the default torch build and warns instead of breaking the install.

**3. Manual wheel swap (fallback).** Replace torch with the ROCm wheel
**after** the first-run install populates the venv:

```bash
# From the project directory (source install), into VoiceStudio's uv venv.
# Matches the app's torch==2.8.0 pin — a different ROCm point release
# (e.g. rocm6.2, rocm7.x) may not carry that exact torch build.
uv pip install --reinstall torch torchaudio \
  --index-url https://download.pytorch.org/whl/rocm6.4
```

Once a ROCm build of PyTorch is in the venv, detection is automatic —
`get_best_device()` returns the GPU (ROCm-built PyTorch reports through
`torch.cuda.is_available()`), and VoiceStudio auto-sets
`HSA_OVERRIDE_GFX_VERSION` for consumer cards whose GFX ID isn't in the
official ROCm support matrix. Relaunch and the Settings → System panel should
report the GPU device instead of `cpu`. Verify the wheel sees your card:

```bash
uv run python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Notes:
- ROCm is exercised far less than the default CUDA/MPS/CPU paths — it works,
  but expect rough edges on consumer cards and report what you hit.
- Unsupported GFX (e.g. some consumer RDNA cards): if it still won't run, set
  `HSA_OVERRIDE_GFX_VERSION` yourself (e.g. `export HSA_OVERRIDE_GFX_VERSION=11.0.0`)
  to the nearest supported architecture before launching.
- ZLUDA (CUDA-on-ROCm translation) can work but is unsupported here — prefer a
  native ROCm wheel.

Tracking issue: [#124](https://github.com/debpalash/VoiceStudio/issues/124).

## Hugging Face token (optional but recommended)

See [docs/setup/huggingface-token.md](../setup/huggingface-token.md).

## Troubleshooting

Hit a wall? See [docs/install/troubleshooting.md](troubleshooting.md).
