# VoiceStudio — Install on Linux

VoiceStudio's Linux desktop app is built with Electron. The archived Tauri app
is no longer maintained; if you still run it, follow the
[migration guide](../electron-migration.md).

## Requirements

- **Linux x86_64** with a graphical desktop session (X11 or Wayland). There are
  no ARM64 Linux packages.
- **~10 GB free disk** for the app, its Python environment, and model weights.
- Optional: an **NVIDIA driver** for CUDA acceleration. In **Automatic** mode,
  without one, setup
  installs the CPU build of PyTorch (see below). For AMD GPUs see
  [AMD GPU (ROCm)](#amd-gpu-rocm).

Python, FFmpeg/FFprobe, yt-dlp, and model weights are bootstrapped by the app
on first launch; no toolchain is needed for the packaged app. If no FFmpeg
resolves anywhere, the app downloads its own checksummed static build;
**Settings → Audio tools** shows which binaries are in use and lets you
override them or update yt-dlp.

## Install (Snap, AppImage or .deb)

<a id="install-snap"></a>

**Snap Store** (Ubuntu and other distributions with snapd):

<a href="https://snapcraft.io/voicestudio"><img src="https://snapcraft.io/en/dark/install.svg" alt="Get it from the Snap Store" height="40" /></a>

or from a terminal:

```bash
sudo snap install voicestudio
```

The snap updates itself through snapd. Recording and dictation need the
microphone interface; if VoiceStudio cannot hear you, connect it once:

```bash
sudo snap connect voicestudio:audio-record
```

<a id="install-appimage"></a>

Download a package from the
[Releases page](https://github.com/debpalash/VoiceStudio/releases/latest):

| Package                     | File                                       |
| --------------------------- | ------------------------------------------ |
| AppImage (any distribution) | `VoiceStudio-<version>-linux-x64.AppImage` |
| Debian / Ubuntu package     | `VoiceStudio-<version>-linux-x64.deb`      |

**AppImage:**

```bash
chmod +x VoiceStudio-*-linux-x64.AppImage
./VoiceStudio-*-linux-x64.AppImage
```

The AppImage uses the static AppImage runtime, so it does not need `libfuse2`.
The [shell installer](script.md) (`curl -fsSL https://voicestudio.sh/install | sh`)
downloads the latest AppImage, verifies it against the release's
`SHA256SUMS.txt`, and installs it as `~/.local/bin/VoiceStudio`.

**.deb:**

```bash
sudo apt install ./VoiceStudio-<version>-linux-x64.deb
```

The in-app updater's Linux feed carries the AppImage. Update a `.deb`
installation by installing the newer `.deb`; remove it with your package
manager.

Compare any download with the release's `SHA256SUMS.txt` before installing.

### Machines without an NVIDIA GPU

With **Automatic** runtime packages selected, laptops and desktops with
Intel/AMD integrated graphics (or any GPU without an NVIDIA driver) run the
whole app on the CPU, just slower. When no NVIDIA
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
  sudo pacman -S --needed base-devel alsa-lib xdotool libxtst libx11 libxkbcommon wayland openssl
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

| Command                | What it does                                                                           |
| ---------------------- | -------------------------------------------------------------------------------------- |
| `bun run desktop-prod` | Build Electron and launch the production bundle                                        |
| `bun run dist`         | Build local installers (AppImage and `.deb`) in `electron/release/` without publishing |
| `bun run smoke-test`   | Build and launch an isolated packaged app                                              |

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

### Nix / NixOS

A community-maintained Nix flake provides development shells and an FHS environment for running from source (not tested in CI). See the [Nix / NixOS guide](nix.md):

```bash
nix develop .#fhs   # recommended for NixOS
bun run dev
```

## ChromeOS, iPad and other devices

There is no native ChromeOS or iPadOS app, and VoiceStudio does not run
inside the browser alone: the models need a real backend.

- **Chromebook with Linux development environment on x86-64:** install the
  AppImage or `.deb` ([Install](#install-snap-appimage-or-deb)). Expect CPU-only
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
./VoiceStudio-*-linux-x64.AppImage --disable-gpu
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

# Use a PyPI mirror (source checkouts that resolve packages themselves; the
# locked dependency install downloads the wheel URLs pinned in uv.lock)
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
If the dependency download is slow or stalls, see
[First-run setup is slow or stalls on a download](troubleshooting.md#first-run-setup-is-slow-or-stalls-on-a-download).

## AMD GPU (ROCm)

<a id="amd-gpu-rocm"></a>

ROCm acceleration is **opt-in** on supported Linux x86_64 AMD GPUs. This section
covers Linux; native Windows has a separate [ROCm setup recipe](windows-rocm.md).
The packaged app's **Automatic** runtime choice selects CUDA when an NVIDIA
driver is detected, otherwise CPU. Choose ROCm explicitly for a supported AMD card.

> **Running in Docker or Podman instead?** There's a prebuilt ROCm image —
> `ghcr.io/debpalash/voicestudio:stable-rocm` — with GPU acceleration out of the
> box; see [docker.md](docker.md#pull-and-run-amd-gpu--rocm). The rest of this
> section is about source/desktop installs. Windows has its own opt-in ROCm
> setup; see [windows-rocm.md](windows-rocm.md).

**Radeon RX 9000 series (RDNA4: RX 9060 / 9070 / 9070 XT, `gfx1200`/`gfx1201`)**
is supported natively by the ROCm build below (torch 2.8.0 from the `rocm6.4`
index includes both architectures), so it needs no `HSA_OVERRIDE_GFX_VERSION`.
The host needs an `amdgpu` kernel driver new enough for RDNA4 (AMD's ROCm
6.4.1+ driver, or a recent distribution kernel); `rocminfo` listing your card
as `gfx1200`/`gfx1201` confirms it. Never remap an RDNA4 card onto `11.0.0`:
RDNA3 kernels cannot run on it.

Ways to opt in, in order of preference:

**1. Packaged app (GUI, recommended).** Install the ROCm userspace
(`/opt/rocm` or `rocminfo` on PATH). Linux x64 AppImage and `.deb` builds offer
**Automatic**, **CPU only**, **NVIDIA CUDA** and **AMD ROCm (supported cards)**
under **Runtime packages** during first setup and in **Settings > Performance**.
Choose **AMD ROCm (supported cards)**, then explicitly choose **Install** (or
**Resume** / **Retry**) in setup. Saving a choice alone starts no download.

For an already working app-managed local runtime, save the choice for the next
app start, finish tasks, fully quit VoiceStudio (including its tray process),
and reopen it. Setup then asks for an explicit install action; saving does not
restart the current backend. User data, model caches and storage locations are
retained; existing CPU sidecars are not automatically converted. Remote,
attached and user-managed backends cannot be converted through this control.
The package selector is not offered on macOS or ARM targets because these wheel
variants are unavailable there.

A valid inherited `OMNIVOICE_TORCH_VARIANT` takes precedence over the saved GUI
choice and pins the selector. To use that optional environment-variable route,
fully quit the app and launch it with `OMNIVOICE_TORCH_VARIANT=rocm`, for example
`OMNIVOICE_TORCH_VARIANT=rocm ./VoiceStudio-*-linux-x64.AppImage`.
Keep using that launch method while the override is wanted; removing the
override makes the saved GUI choice available again.
Runtime setup then reinstalls `torch`/`torchaudio`/`torchvision` from the ROCm
wheel index (`https://download.pytorch.org/whl/rocm6.4` by default) right after
the dependency sync, leaving every other package on the regular index, matched to the app's pinned `torch==2.8.0` (the rocm6.2 index
only ever published up to torch 2.5.1). **Settings → Performance → GPU
acceleration** reports which PyTorch build is installed. The option is ignored
on macOS; on Windows it selects the opt-in stack in
[windows-rocm.md](windows-rocm.md).

**2. Source checkout.** `OMNIVOICE_TORCH_VARIANT=rocm bun run setup:api` swaps
torch right after `uv sync`; `bun run dev` then starts the backend from that
environment, and `bun run dev:api` launches it without re-syncing, so the
wheel is not reverted (#1665).
Running `bun run setup:api` again without `OMNIVOICE_TORCH_VARIANT` restores
the lockfile's CUDA torch build, replacing any ROCm wheel you installed by
hand. To choose another ROCm release, set `OMNIVOICE_TORCH_INDEX=<url>`; AMD
publishes newer driver-matched 7.2.x wheels on `repo.radeon.com` as a
`--find-links` page rather than a PyPI-compatible index, for example:
```bash
uv pip install --reinstall-package torch --reinstall-package torchaudio \
  --reinstall-package torchvision torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 \
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
# Swap all three torch packages together: a CUDA torchvision left beside a
# ROCm torch fails at generation time ("operator torchvision::nms does not
# exist"). Keep PyPI as the main index: PyTorch's index has no Pillow 13, so
# `--index-url` with `--reinstall` cannot resolve (#3158).
uv pip install \
  --reinstall-package torch --reinstall-package torchaudio --reinstall-package torchvision \
  torch==2.8.0+rocm6.4 torchaudio==2.8.0+rocm6.4 torchvision==0.23.0+rocm6.4 \
  --extra-index-url https://download.pytorch.org/whl/rocm6.4 \
  --index-strategy unsafe-best-match
```

Once a ROCm build of PyTorch is in the venv, detection is automatic —
`get_best_device()` returns the GPU (ROCm-built PyTorch reports through
`torch.cuda.is_available()`), and VoiceStudio auto-sets
`HSA_OVERRIDE_GFX_VERSION` when your card's GFX ID is missing from the
installed wheel's architecture list. The target comes from that list, not from
a fixed table: the closest architecture of the same generation and the same or
an older revision (gfx1152 → gfx1100, gfx1035 → gfx1030, gfx1201 → gfx1200 on
a wheel that only ships gfx1200). It never crosses a generation, nor RDNA1/RDNA2
(gfx101x/gfx103x) or Vega/CDNA within gfx9, so an RDNA4
card on a wheel without any gfx12 target gets no override and a clear message. Relaunch and **Settings → Performance → GPU acceleration** should
report the GPU device instead of `cpu`. Verify the wheel sees your card:

```bash
uv run python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Notes:

- ROCm is exercised far less than the default CUDA/MPS/CPU paths — it works,
  but expect rough edges on consumer cards and report what you hit.
- Unsupported GFX (e.g. some consumer RDNA cards): if it still won't run, set
  `HSA_OVERRIDE_GFX_VERSION` yourself to a supported architecture **of the same
  generation** before launching (e.g. `export HSA_OVERRIDE_GFX_VERSION=11.0.0`
  for a gfx11xx card, `10.3.0` for a gfx103x card; RDNA1 gfx101x cannot use
  `10.3.0`). A remap across generations cannot
  work, so RDNA4 (gfx12xx) cards need a ROCm build that lists them instead.
  When the build lacks your card, VoiceStudio's GPU architecture notification
  names the exact value to try, or says that no remap exists.
- ZLUDA (CUDA-on-ROCm translation) can work but is unsupported here — prefer a
  native ROCm wheel.

Tracking issue: [#124](https://github.com/debpalash/VoiceStudio/issues/124).

## Hugging Face token (optional but recommended)

See [docs/setup/huggingface-token.md](../setup/huggingface-token.md).

## Troubleshooting

Hit a wall? See [docs/install/troubleshooting.md](troubleshooting.md).
