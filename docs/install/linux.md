# VoiceStudio — Install on Linux

VoiceStudio's Linux desktop app is built with Electron. The archived Tauri app
is no longer maintained; if you still run it, follow the
[migration guide](../electron-migration.md).

## Requirements

- **Linux x86_64** with a graphical desktop session (X11 or Wayland). There are
  no ARM64 Linux packages.
- **~10 GB free disk** for the app, its Python environment, and model weights.
- Optional: an **NVIDIA driver** for CUDA acceleration. Without one, setup
  installs the CPU build of PyTorch (see below). For AMD GPUs see
  [AMD GPU (ROCm)](#amd-gpu-rocm).

Python, FFmpeg/FFprobe, yt-dlp, and model weights are bootstrapped by the app
on first launch; no toolchain is needed for the packaged app. If no FFmpeg
resolves anywhere, the app downloads its own checksummed static build;
**Settings → Audio tools** shows which binaries are in use and lets you
override them or update yt-dlp.

## Install (AppImage or .deb)

<a id="install-appimage"></a>

Download a package from the
[Releases page](https://github.com/debpalash/VoiceStudio/releases/latest):

| Package | File |
|---|---|
| AppImage (any distribution) | `VoiceStudio-Electron-<version>-linux-x64.AppImage` |
| Debian / Ubuntu package | `VoiceStudio-Electron-<version>-linux-x64.deb` |

**AppImage:**

```bash
chmod +x VoiceStudio-Electron-*-linux-x64.AppImage
./VoiceStudio-Electron-*-linux-x64.AppImage
```

The AppImage uses the static AppImage runtime, so it does not need `libfuse2`.
The [shell installer](script.md) (`curl -fsSL https://voicestudio.sh/install | sh`)
downloads the latest AppImage, verifies it against the release's
`SHA256SUMS.txt`, and installs it as `~/.local/bin/VoiceStudio`.

**.deb:**

```bash
sudo apt install ./VoiceStudio-Electron-<version>-linux-x64.deb
```

The in-app updater's Linux feed carries the AppImage. Update a `.deb`
installation by installing the newer `.deb`; remove it with your package
manager.

Compare any download with the release's `SHA256SUMS.txt` before installing.

### Machines without an NVIDIA GPU

Laptops and desktops with Intel/AMD integrated graphics (or any GPU without
an NVIDIA driver) run the whole app on the CPU, just slower. When no NVIDIA
driver is found, the packaged app's runtime setup installs the small CPU build
of PyTorch rather than the CUDA build and its ~3 GB of `nvidia-*` packages,
and needs about 5 GiB of free disk instead of 9 GiB. Pick a light voice engine
(KittenTTS, Supertonic-3, PocketTTS) and a small Whisper model for the best
speed. `OMNIVOICE_TORCH_VARIANT=cuda|cpu|rocm` overrides the detection, and an
existing install keeps working untouched. Source setup (`bun run setup:api`)
also selects the locked CPU wheels on x86-64 hosts without NVIDIA. Linux ARM
source installs keep their native PyPI wheels. See
[CPU setup and overrides](../../electron/README.md#running-without-a-gpu).

## Building from source

Use this for development or to run current `main`. To build and install a
desktop package from `main` without a checkout, use the shell installer's
[`--main` mode](script.md#building-main).

Prerequisites:

- **git** and **curl**
- **Bun** — `curl -fsSL https://bun.sh/install | bash`
- **uv** — `curl -LsSf https://astral.sh/uv/install.sh | sh` (it provides the
  managed Python 3.11 the backend uses)
- **Node.js 22+**, **Rust / Cargo**
  (`curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`), and the
  native development libraries used by the Electron native helper. If you use
  rustup, reopen the shell or run `source "$HOME/.cargo/env"` first.

  ```bash
  # Debian / Ubuntu
  sudo apt-get update
  sudo apt-get install -y build-essential pkg-config libasound2-dev libxdo-dev \
    libxtst-dev libx11-dev libxkbcommon-dev libwayland-dev libssl-dev \
    binutils zsync

  # Fedora
  sudo dnf install gcc gcc-c++ make pkgconf-pkg-config alsa-lib-devel libxdo-devel \
    libXtst-devel libX11-devel libxkbcommon-devel wayland-devel openssl-devel

  # Arch
  sudo pacman -S --needed base-devel alsa-lib xdotool libxtst libx11 \
    libxkbcommon wayland openssl
  ```

  `bun run dist` also needs `readelf` (binutils) and `zsyncmake` (zsync) to
  embed AppImage update information.

Then, from a clone of the repository:

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
bun run setup:api   # create the Python environment with uv
bun run dev         # Electron with hot reload; it starts and supervises the backend
```

Do not start the backend in a second terminal; Electron attaches to a backend
already listening on port 3900, otherwise it starts its own. Other commands:

| Command | What it does |
|---|---|
| `bun run desktop-prod` | Build Electron and launch the production bundle |
| `bun run dist` | Build local installers (AppImage and `.deb`) in `electron/release/` without publishing |
| `bun run smoke-test` | Build and launch an isolated packaged app |

See [Electron setup](../../electron/README.md) for backend configuration and
quality gates. The first launch downloads model weights only when you install
a model.

### Arch Linux (AUR)

```bash
yay -S voicestudio-bin   # or paru -S voicestudio-bin
```

`voicestudio-bin` is a community-maintained AUR package that installs the
release `.deb`; report packaging problems on its AUR page. Package-managed
installs should set `VOICESTUDIO_DISABLE_UPDATER=1` so `pacman` handles
updates instead of the in-app updater.

## ChromeOS, iPad and other devices

There is no native ChromeOS or iPadOS app, and VoiceStudio does not run
inside the browser alone: the models need a real backend.

- **Chromebook with Linux development environment on x86-64:** install the
  AppImage or `.deb` ([Install](#install-appimage-or-deb)). Expect CPU-only
  generation (the container has no GPU access on most devices) and enough RAM
  for the model you pick. ARM Chromebooks are not supported: the Linux builds
  are x86-64 only.
- **Any browser, including ChromeOS, iPad and phones:** run VoiceStudio on a
  PC, Mac or server and open its web interface from the device. On the same
  network turn on **Network** sharing and scan the QR code; from anywhere, use
  Tailscale. See [Sharing & Remote Access](../sharing.md); for a headless host
  see [Docker](docker.md).

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

## Blank window or GPU errors

<a id="appimage-white-screen-on-fedora-44--ubuntu-2404"></a>

The Electron app renders with Chromium, not WebKitGTK, so the `WEBKIT_*`
variables and the bundled-WebKit/GStreamer workarounds from the archived Tauri
AppImage no longer apply. If the window stays blank or the terminal shows GPU
process errors, start the app once with Chromium's GPU acceleration disabled:

```bash
./VoiceStudio-Electron-*-linux-x64.AppImage --disable-gpu
```

On Wayland sessions you can also try forcing X11 (XWayland) with
`--ozone-platform=x11`. If either flag helps, report your distribution, desktop
and GPU driver in a GitHub issue.

## .deb ffprobe conflict

<a id="deb-ffprobe-conflict"></a>

This applies only to upgrades from **pre-v0.3** `.deb` packages, which
installed `ffprobe` into `/usr/bin/ffprobe` and could clobber the system copy.
If `ffprobe -version` reports the wrong binary after such an upgrade,
reinstall the system package:

```bash
sudo apt install --reinstall ffmpeg
```

## Restricted networks (China / Russia)

If `uv` times out fetching the python-build-standalone tarball or PyPI:

```bash
# Use a faster Python source mirror (China only — verify a current mirror)
export UV_PYTHON_INSTALL_MIRROR=https://gh-proxy.com/https://github.com/astral-sh/python-build-standalone/releases/download

# Use a PyPI mirror
export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

# Or skip the download entirely if you have a compatible system Python
export UV_PYTHON_PREFERENCE=only-system

# Be tolerant of slow links
export UV_HTTP_TIMEOUT=120
export UV_HTTP_RETRIES=5
```

Packaged setup already tries a Python download mirror and raises the uv
network budget automatically; set these to force a specific mirror, or before
`bun run setup:api` in a source checkout. See
[troubleshooting](troubleshooting.md#first-run-setup-fails-on-a-restricted-network-githubpypi-blocked).

## AMD GPU (ROCm)

<a id="amd-gpu-rocm"></a>

ROCm support is **Linux-only and opt-in**. The **default install ships the
CUDA build** of PyTorch (the `pytorch-cuda` index in `pyproject.toml`), so on
an AMD-only machine `torch.cuda.is_available()` is `False` and VoiceStudio runs
on CPU until you opt into the ROCm variant.

> **Running in Docker or Podman instead?** There's a prebuilt ROCm image —
> `ghcr.io/debpalash/voicestudio:stable-rocm` — with GPU acceleration out of the
> box; see [docker.md](docker.md#pull-and-run-amd-gpu--rocm). The rest of this
> section is about source/desktop installs. (VoiceStudio has no ROCm path on
Windows; see [windows.md](windows.md#gpu-support) for what a Radeon card can
do there.)

Ways to opt in, in order of preference:

**1. Packaged app (environment variable).** Install the ROCm userspace
(`/opt/rocm` or `rocminfo` on PATH), then launch VoiceStudio once with
`OMNIVOICE_TORCH_VARIANT=rocm` set, for example
`OMNIVOICE_TORCH_VARIANT=rocm ./VoiceStudio-Electron-*-linux-x64.AppImage`.
Runtime setup then reinstalls `torch`/`torchaudio` from the ROCm wheel index
(`https://download.pytorch.org/whl/rocm6.4` by default) right after the
dependency sync, matched to the app's pinned `torch==2.8.0` (the rocm6.2 index
only ever published up to torch 2.5.1). **Settings → Performance → GPU
acceleration** reports which PyTorch build is installed. The option is ignored
on macOS and Windows.

**2. Source checkout.** `OMNIVOICE_TORCH_VARIANT=rocm bun run setup:api` swaps
torch right after `uv sync`; `bun run dev` then starts the backend from that
environment, and `bun run dev:api` launches it without re-syncing, so the
wheel is not reverted (#1665). Running `bun run setup:api` without the
variable restores the lockfile's CUDA build — a hand-swapped ROCm wheel does
not survive it. `OMNIVOICE_TORCH_INDEX=<url>` overrides the wheel
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
official ROCm support matrix. Relaunch and **Settings → Performance → GPU acceleration** should
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
