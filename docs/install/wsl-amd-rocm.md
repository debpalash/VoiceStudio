# VoiceStudio on WSL2 with an AMD GPU (ROCm, no Docker)

Community-tested path for running VoiceStudio's PyTorch engines on a **Radeon
GPU from Windows**, inside a WSL2 distro, with reusable disk images so the
setup can be wiped and restored in under a minute.

> **Status: unsupported, community-tested.** The supported way to use ROCm on
> Windows is now the opt-in native installation in the
> [Windows ROCm guide](windows-rocm.md); prefer it when your card is on its
> matrix. This WSL2 route predates it, and AMD officially validates
> ROCm-on-WSL only for some cards. This guide records one
> working build end to end, including every error hit along the way, so others
> can reproduce or adapt it. If you only need a Radeon-accelerated engine
> without any of this, use [audio.cpp](../engines/audio-cpp.md) (Vulkan) on
> Windows instead. The Docker variant of this setup is in
> [docker.md — AMD GPU on WSL2](docker.md#amd-gpu-on-wsl2).

## Tested configuration (2026-10-22)

| Component | Version |
|---|---|
| GPU | AMD Radeon RX 7800 XT (`gfx1101`, RDNA 3) |
| Windows | Windows 11 IoT Enterprise LTSC 2024 (10.0.26100) |
| Windows AMD driver | 32.0.31041.1004 |
| WSL | 2.7.12.0, kernel 6.18.33.2, `networkingMode=Mirrored` |
| Distro | Ubuntu 24.04 (`wsl --install Ubuntu-24.04`) |
| ROCm | 7.2.4 from `repo.radeon.com` (no DKMS) |
| WSL bridge | `librocdxg` 1.2.0 (`rocdxg-roct` .deb) |
| PyTorch | `torch 2.8.0+rocm7.2.4` / `torchaudio 2.8.0` / `torchvision 0.23.0` from `repo.radeon.com` |
| VoiceStudio | tag `v0.5.6` validated, run from source; steps target `v0.5.7` (Electron web UI) |

**Verified:**

- `rocminfo` lists `gfx1101` / `AMD Radeon RX 7800 XT`.
- `torch.cuda.is_available()` is `True`, and a 4096² fp16 matmul runs on the GPU at about 17.5 TFLOPS warm.
- The backend logs ``ROCm: amd radeon rx 7800 xt (gfx1101) is natively supported by this build; no HSA_OVERRIDE_GFX_VERSION needed``, followed by ``GPU pool sized to 2 worker(s) — 12.9 GB free``.
- The web UI loads from Windows.
- A reset from the saved image comes back with a working GPU.

**Also verified:** OmniVoice cloning runs on the GPU in eager mode at 2–5 s per
sentence once warm ([error 8](#8-first-generations-take-minutes-torchcompile-on-rocm)).

**Not yet verified:**

- ASR (WhisperX / faster-whisper use CTranslate2, which has no ROCm build here and stays on CPU).

## Why this route

AMD's current WSL2 route for ROCm 7.2.x is **ROCDXG**: the normal ROCm
userspace plus `librocdxg`, which bridges the ROCr runtime to the Windows driver
through `/dev/dxg`. It replaced the older `roc4wsl` packages
(`amdgpu-install --usecase=wsl,rocm`). The
[`librocdxg` README](https://github.com/ROCm/librocdxg) lists the RX 7800 XT
under its 1.2.0 / ROCm 7.2.x row, alongside the RX 7900 and RX 9000 series.
[AMD's WSL how-to](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/wsl/howto_wsl.html)
names only Ubuntu 22.04 and 24.04 as supported WSL distros.

PyTorch comes from AMD's `repo.radeon.com` wheels rather than pytorch.org,
for two reasons. AMD recommends them for Radeon cards, and they publish
torch builds for the same line as VoiceStudio's pin (this guide was validated with
`torch==2.8.0`, before the pin moved to 2.10.0). The pytorch.org `rocm7.1`
index that VoiceStudio's own `OMNIVOICE_TORCH_VARIANT=rocm` switch uses
predates ROCDXG.

## Layout: two reusable images

| Image | Contents | Rebuild time |
|---|---|---|
| `vs-rocm-base.vhdx` | Ubuntu 24.04, ROCm 7.2.4 libraries, librocdxg, user `vs` | ~15 min |
| `vs-0.5.7-ready.vhdx` | base + VoiceStudio `v0.5.7`, Python deps, ROCm torch (no models) | ~10 min on top of base |

The working distro (`VoiceStudio`) is always a **copy** of one of these
(`wsl --import --vhd` copies the file), so breaking it costs nothing: reset and
you are back. The examples use `G:\WSL`; change `$Root` in the scripts for
another drive. Budget **~60 GB** free: ROCm alone is ~14 GB, because rocBLAS,
MIOpen and hipBLASLt ship kernels for every GPU architecture.

```text
G:\WSL\
  images\vs-rocm-base.vhdx       # layer 1
  images\vs-0.5.7-ready.vhdx     # layer 2
  VoiceStudio\                   # the live distro's disk (a copy)
  scripts\provision-base.sh      # builds layer 1
  scripts\provision-voicestudio.sh  # builds layer 2
  scripts\verify-gpu.sh          # GPU check
  scripts\vs-wsl.ps1             # reset / start / save / check
```

## Prerequisites (Windows)

1. Install a current **AMD Adrenalin driver** for your card. AMD's WSL pages reference
   Adrenalin 26.1.1 or newer for WSL2. Reboot afterwards.
2. Install or update WSL to **2.5.6 or newer** (`wsl --update`). That version is
   needed for `wsl --install --name/--location`, `--format vhd` exports and
   `--set-sparse` with the `--allow-unsafe` flag this guide passes (see the
   [sparse VHDX warning](#sparse-vhd-warning)). Check with `wsl --version`.
3. Make sure `%UserProfile%\.wslconfig` gives WSL enough memory for the models
   (16 GB was used here).

## Step 1 — build the base image

Save the script below as `G:\WSL\scripts\provision-base.sh`:

```bash
#!/usr/bin/env bash
# Layer 1: Ubuntu 24.04 + ROCm 7.2.4 runtime + librocdxg 1.2.0 (AMD ROCDXG path for WSL2).
# Run as root inside a fresh Ubuntu-24.04 WSL distro.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

ROCM_VER=7.2.4
ROCM_DEB=amdgpu-install_7.2.4.70204-1_all.deb
# SHA-256 of the file used in the tested build (repo.radeon.com publishes no digest for it).
ROCM_DEB_SHA256=53c7ec5cace439f1ea97efebd59a3b32aa288c5c0bf76f57ab3cd7159dd98fad
ROCDXG_VER=1.2.0
# GitHub's published digest for the release asset; a mismatch stops before dpkg runs.
ROCDXG_SHA256=3ed9526719290cd8f590150dad8ea0f234fa779bea6a4c9a8449d7ae6b8cfb6e
USER_NAME=vs

apt-get update
apt-get -y upgrade
apt-get install -y --no-install-recommends \
  ca-certificates curl wget git git-lfs build-essential pkg-config unzip xz-utils \
  python3 python3-venv python3-dev python3-setuptools python3-wheel \
  ffmpeg libsndfile1 espeak-ng sox libsox-fmt-all portaudio19-dev libportaudio2 \
  sudo locales

locale-gen en_US.UTF-8

# Non-root user with passwordless sudo, default for the distro.
id "$USER_NAME" >/dev/null 2>&1 || useradd -m -s /bin/bash "$USER_NAME"
echo "$USER_NAME ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-$USER_NAME
chmod 0440 /etc/sudoers.d/90-$USER_NAME
cat > /etc/wsl.conf <<EOF
[user]
default=$USER_NAME
[boot]
systemd=false
[interop]
appendWindowsPath=false
EOF

# ROCm apt repo (no DKMS: WSL uses the Windows driver through /dev/dxg).
cd /tmp
wget -q "https://repo.radeon.com/amdgpu-install/${ROCM_VER}/ubuntu/noble/${ROCM_DEB}"
echo "${ROCM_DEB_SHA256}  ${ROCM_DEB}" | sha256sum -c -
apt-get install -y "./${ROCM_DEB}"
apt-get update
# Ubuntu's own old ROCm runtime packages shadow /opt/rocm if present.
apt-get remove -y libhsa-runtime64-1 libhsakmt1 2>/dev/null || true
apt-get install -y --no-install-recommends rocm-hip-runtime rocminfo
# AMD's repo.radeon.com torch wheels link these from /opt/rocm instead of bundling them.
apt-get install -y --no-install-recommends \
  rocblas rocsolver hipblas hipblaslt hipfft hiprand hipsolver hipsparse hipsparselt \
  miopen-hip rccl roctracer rocm-smi-lib libsox-dev
groupadd -f render; groupadd -f video
usermod -a -G render,video "$USER_NAME"

# librocdxg: the WSL bridge between ROCr and the Windows driver.
wget -q "https://github.com/ROCm/librocdxg/releases/download/v${ROCDXG_VER}/rocdxg-roct_${ROCDXG_VER}_amd64.deb"
echo "${ROCDXG_SHA256}  rocdxg-roct_${ROCDXG_VER}_amd64.deb" | sha256sum -c -
dpkg -i "rocdxg-roct_${ROCDXG_VER}_amd64.deb"

# Make the ROCm libraries (incl. librocdxg) visible to every process.
echo /opt/rocm/lib > /etc/ld.so.conf.d/rocm.conf
ldconfig
cat > /etc/profile.d/rocm-wsl.sh <<'EOF'
# ROCm 7.2.x on WSL2 needs DXG detection switched on (dropped from ROCm 7.13).
export HSA_ENABLE_DXG_DETECTION=1
export PATH=/opt/rocm/bin:$PATH
EOF

apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/*.deb
echo "BASE PROVISION OK"
```

Then, in PowerShell:

```powershell
New-Item -ItemType Directory -Force G:\WSL\images, G:\WSL\scripts, G:\WSL\build | Out-Null
wsl --install Ubuntu-24.04 --name VSBase --location G:\WSL\build\VSBase --no-launch
# Scripts saved from Windows have CRLF line endings; strip them on the way in.
wsl -d VSBase -u root -- bash -c "sed 's/\r$//' /mnt/g/WSL/scripts/provision-base.sh | bash"
wsl --terminate VSBase
wsl -d VSBase -- bash -lc "rocminfo | grep -E 'Marketing Name|Name: +gfx'"
```

The last command must list your Radeon card (here `gfx1101` /
`AMD Radeon RX 7800 XT`). **Do not continue until it does.**

<a id="sparse-vhd-warning"></a>

> **Sparse VHDX warning.** WSL only enables sparse mode with `--allow-unsafe`,
> because Microsoft flags it as a possible cause of virtual disk corruption.
> Use it only on distros you can rebuild, and keep a known-good exported image:
> anything changed since your last export can be lost if the disk corrupts.
> The layout here relies on that: the live distro is always a copy of an image.

Export the image. Run `wsl --shutdown` first (see
[error 3](#3-wsl---export-sharing-violation-even-with-the-distro-stopped)), and **only delete the build
distro after the export succeeds**:

```powershell
wsl --manage VSBase --set-sparse true --allow-unsafe
wsl -d VSBase -u root -- fstrim -av
wsl --shutdown
wsl --export VSBase G:\WSL\images\vs-rocm-base.vhdx --format vhd
if ($LASTEXITCODE -eq 0) { wsl --unregister VSBase; Remove-Item -Recurse G:\WSL\build }
```

## Step 2 — install VoiceStudio and build the ready image

Save as `G:\WSL\scripts\provision-voicestudio.sh`:

```bash
#!/usr/bin/env bash
# Layer 2: VoiceStudio v0.5.7 from source + PyTorch 2.8.0 for ROCm 7.2.4.
# Run as the default user (vs) inside a distro imported from the base image.
set -euxo pipefail
source /etc/profile.d/rocm-wsl.sh

VS_TAG=v0.5.7
VS_DIR="$HOME/VoiceStudio"
ROCM_WHEELS=https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2.4/

# Toolchain: uv (Python) and bun (JS), both per-user.
command -v uv  >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
command -v bun >/dev/null || curl -fsSL https://bun.sh/install | bash
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"

[ -d "$VS_DIR/.git" ] || git clone --branch "$VS_TAG" --depth 1 https://github.com/debpalash/VoiceStudio.git "$VS_DIR"
cd "$VS_DIR"
git -c advice.detachedHead=false checkout "$VS_TAG"

bun install --frozen-lockfile
bun run setup:api   # uv sync (locked CUDA torch) + scripts/setup.py

# Swap the locked CUDA torch for AMD's ROCm 7.2.4 build of the same versions.
uv pip install \
  --reinstall-package torch --reinstall-package torchaudio --reinstall-package torchvision \
  "torch==2.8.0+rocm7.2.4.lw.gitd618db82" "torchaudio==2.8.0+rocm7.2.4.git6e1c7fe9" \
  "torchvision==0.23.0+rocm7.2.4.git824e8c87" \
  --find-links "$ROCM_WHEELS"
# Drop the CUDA runtime wheels the locked build pulled in (several GB, unused on ROCm).
uv pip uninstall $(uv pip list --format freeze | grep -oE '^nvidia-[a-z0-9-]+-cu12' || true) 2>/dev/null || true
uv run --no-sync python -c "import torch; print('torch', torch.__version__, 'hip', torch.version.hip)"

# Launch helper: backend without re-sync (keeps the ROCm torch) + web UI.
cat > "$HOME/start-voicestudio.sh" <<'EOF'
#!/usr/bin/env bash
source /etc/profile.d/rocm-wsl.sh
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"
export OMNIVOICE_TORCH_VARIANT=rocm
cd "$HOME/VoiceStudio"
bun run dev:api &
API=$!
trap 'kill $API 2>/dev/null' EXIT
# With networkingMode=Mirrored, Windows reaches WSL on 127.0.0.1 only (::1 hangs).
bun run wait:api && echo "Backend ready: http://127.0.0.1:3900  —  UI: http://127.0.0.1:3901"
bun run dev:frontend
EOF
chmod +x "$HOME/start-voicestudio.sh"
echo "VOICESTUDIO PROVISION OK"
```

Notes on this script:

- **Why the launcher never re-syncs.** From `v0.5.7`, `scripts/dev-backend.mjs`
  always starts uvicorn with `uv run --no-sync`, so a launch keeps the ROCm
  torch installed above instead of restoring the CUDA build from `uv.lock`
  (in `v0.5.6` that needed `OMNIVOICE_TORCH_VARIANT=rocm`, which the launcher
  still sets for older checkouts). The torch swap itself is done by hand above, because the
  built-in swap only accepts a PEP 503 index (`OMNIVOICE_TORCH_INDEX`), and
  `repo.radeon.com` is a `--find-links` page.
- **Exact local versions are pinned.** `repo.radeon.com` carries two different
  `torch 2.8.0+rocm7.2.4` builds (`git6bea3e0b` and `gitd618db82`), and pinning
  keeps the result reproducible. Use `python3.11` wheels: `v0.5.7`'s
  `.python-version` is `3.11`, and uv downloads it.
- **`bun run dev:frontend` starts the Electron web UI** on port 3901. The
  community run validated this guide on `v0.5.6`, whose `dev:frontend` started
  the retired web UI; the steps here target `v0.5.7`, the first release whose
  `dev:frontend` is Electron's web entrypoint. Later tags use the same commands.

Save `G:\WSL\scripts\verify-gpu.sh`:

```bash
#!/usr/bin/env bash
# GPU sanity check for the VoiceStudio venv: device, arch list, and a real matmul.
source /etc/profile.d/rocm-wsl.sh
export PATH="$HOME/.local/bin:$PATH"
cd ~/VoiceStudio
uv pip show nvidia-ml-py >/dev/null 2>&1 || uv pip install -q nvidia-ml-py
uv run --no-sync python - <<'EOF'
import time, torch
print("torch", torch.__version__, "hip", torch.version.hip)
ok = torch.cuda.is_available()
print("gpu available:", ok)
if ok:
    print("device:", torch.cuda.get_device_name(0), "| archs:", torch.cuda.get_arch_list())
    a = torch.randn(4096, 4096, device="cuda", dtype=torch.float16)
    torch.cuda.synchronize(); t = time.time()
    for _ in range(20): a @ a
    torch.cuda.synchronize()
    print(f"matmul x20 fp16 4096: {time.time()-t:.3f}s  (~{20*2*4096**3/(time.time()-t)/1e12:.1f} TFLOPS)")
raise SystemExit(0 if ok else 1)  # a missing GPU fails the check
EOF
```

Create the working distro from the base, install, verify, and save the ready
image:

`--allow-unsafe` below carries the same
[sparse VHDX warning](#sparse-vhd-warning).

```powershell
wsl --import VoiceStudio G:\WSL\VoiceStudio G:\WSL\images\vs-rocm-base.vhdx --vhd
wsl --manage VoiceStudio --set-sparse true --allow-unsafe
wsl -d VoiceStudio -- bash -lc "sed 's/\r$//' /mnt/g/WSL/scripts/provision-voicestudio.sh | bash"
wsl -d VoiceStudio -- bash -lc "sed 's/\r$//' /mnt/g/WSL/scripts/verify-gpu.sh | bash"
```

Expected output:

```text
torch 2.8.0+rocm7.2.4.gitd618db82 hip 7.2.53211-97f5574fe2
gpu available: True
device: AMD Radeon RX 7800 XT | archs: [..., 'gfx1100', 'gfx1101', ...]
```

Then reclaim space and save. `uv cache clean` is safe: the venv keeps its
hard-linked files.

```powershell
wsl -d VoiceStudio -- bash -lc 'export PATH=$HOME/.local/bin:$PATH; uv cache clean; rm -rf ~/.bun/install/cache; sudo fstrim -av'
wsl --shutdown
wsl --export VoiceStudio G:\WSL\images\vs-0.5.7-ready.vhdx --format vhd
```

## Day to day: `vs-wsl.ps1`

Save as `G:\WSL\scripts\vs-wsl.ps1`:

```powershell
# VoiceStudio-on-WSL2 image manager (ROCm 7.2.4 + librocdxg).
#
#   .\vs-wsl.ps1 reset            # wipe "VoiceStudio" and re-import the ready image (fast)
#   .\vs-wsl.ps1 reset -From base # re-import the ROCm-only base and reinstall VoiceStudio v0.5.7
#   .\vs-wsl.ps1 start            # launch VoiceStudio, then open http://127.0.0.1:3901
#   .\vs-wsl.ps1 save             # snapshot the current "VoiceStudio" as the new ready image
#   .\vs-wsl.ps1 check            # GPU check (rocminfo + torch)
param(
    [Parameter(Mandatory)][ValidateSet('reset', 'start', 'save', 'check')][string]$Action,
    [ValidateSet('ready', 'base')][string]$From = 'ready'
)
$ErrorActionPreference = 'Stop'
$Root    = Split-Path $PSScriptRoot          # the folder above scripts\, e.g. G:\WSL
$Distro  = 'VoiceStudio'
$Images  = @{ base = "$Root\images\vs-rocm-base.vhdx"; ready = "$Root\images\vs-0.5.7-ready.vhdx" }
$Install = "$Root\$Distro"
# Windows path of scripts\ as seen from WSL, e.g. G:\WSL\scripts -> /mnt/g/WSL/scripts
$Scripts = '/mnt/' + $PSScriptRoot.Substring(0, 1).ToLower() + ($PSScriptRoot.Substring(2) -replace '\\', '/')

function Stop-Distro($name) {
    wsl --terminate $name | Out-Null
    # The VM keeps the VHDX open for a few seconds after terminate.
    for ($i = 0; $i -lt 30; $i++) {
        $row = (wsl -l -v) -replace "`0", '' | Where-Object { $_ -match "\s$name\s" }
        if ($row -match 'Stopped') { Start-Sleep 3; return }
        Start-Sleep 1
    }
    throw "$name did not stop"
}

function Export-Image($name, $dest) {
    Stop-Distro $name
    $tmp = $dest -replace '\.vhdx$', '.partial.vhdx'   # wsl insists on a .vhdx extension
    for ($try = 1; $try -le 3; $try++) {
        wsl --export $name $tmp --format vhd
        if ($LASTEXITCODE -eq 0 -and (Test-Path $tmp)) { Move-Item -Force $tmp $dest; return }
        # The utility VM can hold the disk even with the distro stopped; stop the VM.
        wsl --shutdown; Start-Sleep 5
    }
    throw "export of $name failed; nothing was removed"
}

switch ($Action) {
    'reset' {
        if (-not (Test-Path $Images[$From])) { throw "missing image $($Images[$From])" }
        if (((wsl -l -q) -replace "`0", '') -contains $Distro) { wsl --unregister $Distro }
        wsl --import $Distro $Install $Images[$From] --vhd   # copies; the image stays untouched
        if ($LASTEXITCODE -ne 0) { throw "import of $($Images[$From]) failed" }
        # Sparse VHDX gives freed space back to the host drive (run `sudo fstrim -av` inside after big deletes).
        # WSL calls sparse mode unsafe (possible disk corruption): the images stay the known-good copy.
        wsl --manage $Distro --set-sparse true --allow-unsafe
        if ($LASTEXITCODE -ne 0) { throw "enabling sparse mode on $Distro failed" }
        if ($From -eq 'base') {
            wsl -d $Distro -- bash -lc "sed 's/\r$//' '$Scripts/provision-voicestudio.sh' | bash"
        }
    }
    'start' { wsl -d $Distro -- bash -lc '~/start-voicestudio.sh' }
    'save'  { Export-Image $Distro $Images.ready }
    'check' { wsl -d $Distro -- bash -lc "sed 's/\r$//' '$Scripts/verify-gpu.sh' | bash" }
}
```

Usage:

```powershell
powershell -ExecutionPolicy Bypass -File G:\WSL\scripts\vs-wsl.ps1 start
```

Open **http://127.0.0.1:3901**. Use `127.0.0.1`, not `localhost`; see
[error 7](#7-ui-unreachable-from-windows-on-localhost).

- **`reset` wipes the live distro.** That includes downloaded models, cloned
  voices and projects. Run `save` once you have them, so the ready image
  includes them.
- **`save` and the export retry may run `wsl --shutdown`.** That stops every
  running WSL distro, Docker Desktop's included.

## Build log: every error hit, and the fix

This is the full record from the first build. All fixes are already in the
scripts above.

### 1. Docs say "no ROCm on Windows" and the Docker WSL path is unverified

When this guide was written, VoiceStudio's [windows.md](windows.md#gpu-support)
said PyTorch engines run on CPU on Radeon (the native
[Windows ROCm guide](windows-rocm.md) came later). [docker.md](docker.md#amd-gpu-on-wsl2) documents a Docker plus
ROCDXG route marked *Unverified*. Research for this guide found:

- **The current route is ROCDXG.** AMD documents it in the
  [librocdxg README](https://github.com/ROCm/librocdxg) and the
  [WSL how-to](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/wsl/howto_wsl.html).
  The legacy `amdgpu-install --usecase=wsl,rocm` path (roc4wsl) stops at ROCm 7.2.
- **The RX 7800 XT is listed** in librocdxg's 1.2.0 / ROCm 7.2.x row.
  Phoronix reports official validation targeting the RX 9000 series
  ([source](https://www.phoronix.com/news/AMD-WSL-ROCm-ROCDXG)).
- **A recent issue shows the same card working.**
  [ROCm/aiter#5957](https://github.com/ROCm/aiter/issues/5957) (moved to
  ROCm/rocm-systems#12495) has an RX 7800 XT on WSL2 Ubuntu 24.04 with the
  same 32.0.31041.1004 driver running PyTorch on TheRock wheels. Its profiler
  issue is that only `librocdxg.so.1` gets installed. The `.deb` used here
  installs `librocdxg.so`, `.so.1` and `.so.1.2.0`, so it does not apply.

### 2. Base export failed, and a chained command then deleted the distro

```text
The process cannot access the file because it is being used by another process.
Error code: Wsl/Service/ERROR_SHARING_VIOLATION
```

`wsl --export` ran right after `wsl --terminate`, while the WSL utility VM
still held the VHDX open. The same PowerShell line went on to run
`wsl --unregister VSBase` regardless, and the freshly built base was lost.
Rebuilding it cost about 10 minutes.

**Fix:** never chain `--unregister` after `--export` without checking
`$LASTEXITCODE`. `Export-Image` in `vs-wsl.ps1` exports to a temp file and
retries. It removes nothing on failure.

### 3. `wsl --export` sharing violation even with the distro stopped

Polling until `wsl -l -v` showed `Stopped` was not enough. The export kept
failing with `ERROR_SHARING_VIOLATION` until **`wsl --shutdown`** stopped the
utility VM. `Export-Image` now runs `wsl --shutdown` between retries.

### 4. Export to a `.partial` file name rejected

```text
The specified file must have the .vhdx file extension.
Error code: Wsl/Service/WSL_E_EXPORT_FAILED
```

`--format vhd` requires the destination to end in `.vhdx`. The temp file is
now `*.partial.vhdx`.

### 5. `import torch` fails: missing ROCm libraries

```text
ImportError: libroctx64.so.4: cannot open shared object file: No such file or directory
```

Unlike pytorch.org wheels, AMD's `repo.radeon.com` torch wheels **link against
the system ROCm in `/opt/rocm`**. `rocm-hip-runtime` alone is not enough. To
list what is missing:

```bash
cd ~/VoiceStudio/.venv/lib/python3.11/site-packages
for f in torch/lib/*.so torchaudio/lib/*.so torchvision/*.so; do ldd "$f" | grep 'not found'; done | sort -u
```

The `lib*.so` names that came up, against the packages that provide them:

| Missing libraries | Package |
|---|---|
| MIOpen | `miopen-hip` |
| hipblas, hipblaslt, hipfft, hiprand, hipsolver, hipsparse, hipsparselt | the package of the same name |
| rccl | `rccl` |
| rocblas, rocsolver | `rocblas`, `rocsolver` |
| roctracer64, roctx64 | `roctracer` |
| rocm_smi64 | `rocm-smi-lib` |
| sox | `libsox-dev` |

These are now part of `provision-base.sh`. `libc10*`/`libtorch*` also show as
"not found" under plain `ldd`, but torch resolves them through its own RPATH.
Ignore them.

### 6. Disk full on the image drive

```text
There is not enough space on the disk.  Error code: Wsl/Service/0x80070070
```

VHDX files only grow. The ready image reached **34 GB** of which ~12 GB was
uv's cache (the CUDA torch and `nvidia-*` wheels from `uv sync` are cached even
after being uninstalled), and the build distro another 21 GB. Fix:

- Turn on **sparse VHDX** for each distro:
  `wsl --manage <distro> --set-sparse true --allow-unsafe` (read the
  [sparse VHDX warning](#sparse-vhd-warning) first).
- Inside, run `uv cache clean`, remove the bun install cache, and run
  `sudo fstrim -av`.
- Then `wsl --shutdown`.

That reclaimed ~25 GB. Exports of sparse disks stay sparse.
Explorer and `Get-Item` still show the **logical** size, so check real usage with
the drive's free space instead.

### 7. UI unreachable from Windows on `localhost`

Inside WSL both ports answered `200`, but from Windows `http://localhost:3900`
and `:3901` timed out. With `networkingMode=Mirrored`, IPv6 `::1` hangs while
**`127.0.0.1` works**. Use `http://127.0.0.1:3901`. The UI's backend websocket
connects normally that way.

### 8. First generations take minutes: `torch.compile` on ROCm

The first voice-clone generation took **101 s** and the second **150 s** for one short
sentence, with uvicorn at 400 % CPU. Triton is importable in the ROCm torch
build, so the backend applied `torch.compile(mode="reduce-overhead")`, and
Inductor recompiled for every new text length. The fix is to disable it.
Either turn on **Settings → Performance → Disable torch.compile**, or call:

```bash
curl -X PUT http://127.0.0.1:3900/api/settings/perf/torch-compile-disabled \
  -H 'Content-Type: application/json' -d '{"enabled":true}'
```

Then restart the backend; the model loads at start. Measured on the RX 7800 XT
in eager mode, for a ~7 s Spanish sentence:

| Run | Time |
|---|---|
| First few runs | 18–37 s (MIOpen finds and caches kernels in `~/.cache/miopen`) |
| Warm runs | **2–5 s** |

The many `MIOpen(HIP): Warning [IsEnoughWorkspace] ... provided ptr: 0 size: 0`
lines during those first runs are harmless; MIOpen falls back to a solver
that needs no workspace. Run `save` after a few generations so the warm MIOpen
cache is kept in the ready image.

### 9. Smaller slips worth avoiding

- **The first VoiceStudio install stopped partway** because of error 5
  (`set -e`), so `~/start-voicestudio.sh` was never written. Re-running the
  whole script is safe and fast: the clone is skipped and uv reuses its cache.
- **Do not remove every `nvidia-*` package.** An early `grep '^nvidia-'` also
  removed `nvidia-ml-py`, which is pure Python and expected by the backend's GPU
  stats. Only `nvidia-*-cu12` runtime wheels are dead weight on ROCm.
  `verify-gpu.sh` reinstalls `nvidia-ml-py` if it is missing.
- **Quoting `$PATH` or `(...)` inside `wsl -- bash -lc "..."` from PowerShell
  breaks easily**, because PowerShell expands `$PATH` itself. Put the commands in
  a `.sh` file and run that, as the scripts here do.
- **Strip CRLF line endings.** Files edited on Windows get them, so pipe
  scripts through `sed 's/\r$//'` before running them with `bash`.

## Troubleshooting

| Symptom | Check |
|---|---|
| `rocminfo` shows only the CPU agent | Is `HSA_ENABLE_DXG_DETECTION=1` set (`/etc/profile.d/rocm-wsl.sh`, login shell `bash -l`)? Is `/dev/dxg` present? Is the Adrenalin driver current, and did you reboot after installing it? |
| `gpu available: False` but `rocminfo` is fine | Run `ldd` on torch's libs to find missing system libraries (error 5). Check that the venv still has the ROCm torch (`torch.version.hip` not `None`). A plain `uv run` without `--no-sync` restores the CUDA build. |
| Backend log says `Falling back to CPU:` | Read the reason on that line. For an architecture mismatch, try `HSA_OVERRIDE_GFX_VERSION=11.0.0` only on an RDNA 3 (gfx11xx) card whose `gfx` ID is missing from `torch.cuda.get_arch_list()`; never on an RX 9000 (RDNA 4, gfx12xx) card, which needs a ROCm build that lists it. |
| Generation takes minutes, high CPU | `torch.compile` is recompiling: disable it (error 8). The first runs after that still warm up MIOpen. |
| Breaks after a Windows driver update | Test with `vs-wsl.ps1 check`. If `rocminfo` lost the GPU, roll back the driver or move to a newer librocdxg/ROCm pair from the librocdxg compatibility table. |

## Sources

- [ROCm/librocdxg](https://github.com/ROCm/librocdxg): README, supported GPUs, releases
- [AMD: Use ROCm on Radeon and Ryzen — WSL how-to](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/wsl/howto_wsl.html)
- [AMD: Install Radeon software for WSL with ROCm (7.2)](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2/docs/install/installrad/wsl/install-radeon.html), the legacy roc4wsl path
- [repo.radeon.com ROCm 7.2.4 wheels](https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2.4/)
- [Phoronix: AMD ROCDXG for WSL](https://www.phoronix.com/news/AMD-WSL-ROCm-ROCDXG)
- [ROCm/aiter#5957](https://github.com/ROCm/aiter/issues/5957): RX 7800 XT on WSL2 report
