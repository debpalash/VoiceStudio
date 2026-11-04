# VoiceStudio — Install on Windows

VoiceStudio's Windows desktop app is built with Electron. The archived Tauri
app (MSI installers) is no longer maintained; if you still run it, follow the
[migration guide](../electron-migration.md).

## Requirements

- **Windows 10 or 11**, x64 — or Windows on ARM (experimental, see below).
- **~10 GB free disk** for the app, its Python environment, and model weights.
- Optional: an **NVIDIA GPU + driver** for CUDA acceleration — see
  [GPU support on Windows](#gpu-support).

Python, FFmpeg, and model weights are bootstrapped by the app itself on first
launch; no toolchain is needed for the installer.

## Keep engine environments off `C:`

Engine environments (IndexTTS, CosyVoice, MOSS-TTS and other sidecars, plus the
audio.cpp runtime) install under `<app data>\engines` and can reach several GB
each. To place them on another drive, set `OMNIVOICE_ENGINES_DIR` before
launching VoiceStudio, for example in PowerShell:

```powershell
[Environment]::SetEnvironmentVariable('OMNIVOICE_ENGINES_DIR', 'D:\VoiceStudio\engines', 'User')
```

Restart VoiceStudio afterwards. Voices, projects and settings stay in the data
folder (`OMNIVOICE_DATA_DIR` moves that too). Engines already installed under the
old folder are not moved: reinstall them from Engines, or move the
`engines\<engine>` folder yourself and delete the old copy.

## Install

Download the installer matching your PC from the
[Releases page](https://github.com/debpalash/VoiceStudio/releases/latest):

| PC | Installer |
|---|---|
| Intel / AMD (x64) | `VoiceStudio-<version>-win-x64.exe` |
| Windows on ARM (Snapdragon X etc., experimental) | `VoiceStudio-<version>-win-arm64.exe` (releases without it: the x64 installer also works, under emulation) |

The setup wizard installs for the current user by default (no administrator
rights needed) and lets you choose the installation folder. Compare the download with the
release's `SHA256SUMS.txt` before running it.

During an update, the installer closes processes running inside the selected
installation folder. Processes in similarly named sibling folders and the
installer itself are excluded. If an older installer leaves a running copy
open, exit that copy before retrying. If it keeps saying VoiceStudio
cannot be closed when the app is stopped, run the installer from a different
folder (for example Downloads) and retry; keep your existing runtime, model
cache and data folders.

Or, from a regular (non-admin) PowerShell, let the
[install script](script.md) pick the right installer, verify it, and open the
wizard:

```powershell
irm https://voicestudio.sh/install.ps1 | iex
```

### PCs without an NVIDIA GPU

VoiceStudio is fully usable without a dedicated GPU: laptops with Intel or AMD
integrated graphics, Ryzen/Ryzen AI parts and older desktops all run the whole
app on the CPU, just slower (integrated graphics are not used for AI work).
On first run the runtime installer detects that no NVIDIA driver is present and
installs the small CPU build of PyTorch instead of the multi-GB CUDA one, so
the download is much smaller and needs about 5 GiB free instead of 9 GiB. For
the best experience on a CPU, pick a light voice engine (KittenTTS,
Supertonic-3, PocketTTS) and a small Whisper model in the Model Catalogue; the
setup screen lists them as its CPU preset. Choose **CPU only** or **NVIDIA CUDA**
under **Runtime packages** during desktop setup to override automatic detection.
An explicit `OMNIVOICE_TORCH_VARIANT=cuda` (or `cpu`) launch override also remains supported.
Source setup (`bun run setup:api`) also selects locked CPU wheels when NVIDIA
is absent. See [CPU setup and overrides](../../electron/README.md#running-without-a-gpu).

### Windows on ARM (Snapdragon X etc.)

> [!NOTE]
> **Experimental.** The ARM64 installer and the emulated runtime are built by
> CI but have not yet passed an end-to-end validation on real hardware. Expect
> rough edges and please report them.

The app shell is native ARM64. PyTorch has no ARM64 Windows wheels for
torchaudio/torchvision, so the AI backend runs as an **x64 Python under
Windows' built-in emulation, on the CPU only** (no GPU or NPU acceleration).
It is designed to work but will be slower than on an x64 PC; Windows 11 24H2
or newer gives the emulator the AVX2 support PyTorch benefits from. Setup shows
a "Windows on ARM" notice and applies the same light-engine advice as above.

### Everything on an external drive

The Electron app has no single-file portable build. To keep it all on another
drive (for example an external SSD):

1. Run the installer and choose the external drive on its **Installation
   Folder** page.
2. On the setup screen, press **Change…** beside **App environment** and pick
   the drive. That one folder then holds the Python environment, uv's download
   cache, managed Python, your data (`data`), the model cache (`models`) and
   scratch files (`tmp`), so nothing is staged on `C:`. The setup screen lists
   each effective location. Variables you set yourself (`OMNIVOICE_DATA_DIR`,
   `OMNIVOICE_CACHE_DIR`, `TEMP`) win over the folder.
3. An existing installation is never moved. To relocate its data or model cache
   later, use **Settings > Storage**.

The drive must be connected when VoiceStudio starts.

## GPU support on Windows

<a id="gpu-support"></a>

**NVIDIA GPUs** use CUDA automatically when a compatible driver is installed.
**AMD GPUs** use CPU PyTorch by default; supported Radeon and Ryzen AI hardware
can explicitly opt into the matched Windows 11 ROCm 7.2.1 installation for
Electron by choosing **AMD ROCm (supported cards)** under **Runtime packages**.
Existing managed installations can save a new target from Settings > Performance
for the next app start, then explicitly install. Source setup uses
`OMNIVOICE_TORCH_VARIANT=rocm`.

Follow the [Windows ROCm guide](windows-rocm.md) for AMD's exact driver/hardware
matrices, setup commands, offline verification, local speech smoke, repair and
CPU rollback. Python 3.12 and the pinned Windows HIP CTranslate2 build are
installed together. Main runtime readiness does not certify optional engines:
WhisperX/pyannote GPU execution and GPU forced alignment remain explicitly
gated pending MIOpen compatibility evidence; their CPU paths remain available.
Existing sidecar Python/Torch pins are preserved. Windows ARM64, Windows 10,
unlisted Radeon hardware and the Ryzen AI NPU are outside this recipe.

Engines with their own runtime remain separate: [audio.cpp](../engines/audio-cpp.md)
can use its Vulkan build, subject to its own installation/capability checks.
Linux ROCm setup remains documented in [linux.md](linux.md#amd-gpu-rocm).
Unsupported community alternative: a WSL2 distro with ROCm 7.2.4 and AMD's
`librocdxg` bridge, validated on an RX 7800 XT with the `v0.5.6` tag, is
recorded in [wsl-amd-rocm.md](wsl-amd-rocm.md).

**Intel Arc GPUs (XPU) — experimental.** The Windows install does not ship the
Intel XPU build of PyTorch, so out of the box an Arc card is not used. If you run
VoiceStudio with a PyTorch `+xpu` build, the main OmniVoice engine and PyTorch
Whisper ASR run on the card. Engines that install their own PyTorch (CosyVoice,
VoxCPM2, IndexTTS and similar) still run on the CPU. Two Windows driver quirks
are handled for you:

- The Arc driver cannot report free GPU memory. VoiceStudio estimates it as total
  memory minus what PyTorch has reserved, so model loads no longer fail with
  "doesn't support querying the available free memory".
- An out-of-memory error (`UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY` or
  `XPU out of memory`) is reported as one and its memory is released, so the next
  generation can run without restarting the app.

**Settings → Performance → GPU acceleration** shows exactly what applies to your
machine: the GPUs Windows reports, which PyTorch build is installed, and a
verdict for every engine (uses the GPU, runs on the CPU and why, or CPU by
design). Manually installed HIP sidecars are recognized in Windows virtual
environments too. If HIP cannot enumerate a GPU, diagnostics point to AMD's
supported GPU, driver and wheel combination; an SDK directory on PATH alone
does not prove a working PyTorch runtime. **Settings → About → Run self-check** names the card instead of just
saying "no GPU acceleration detected".

## Building from source

Use this for development or to run current `main`. The install script's
`main` mode (`$env:VOICESTUDIO_INSTALL_MODE='main'; irm https://voicestudio.sh/install.ps1 | iex`)
builds and installs a desktop package from `main` without a checkout; see
[the install script guide](script.md).

Prerequisites:

- **Git for Windows** — `winget install --id Git.Git -e`.
- **Bun** — `powershell -c "irm bun.sh/install.ps1 | iex"`.
- **uv** — `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` (it
  provides the managed Python 3.11 the backend uses).
- **Node.js 22+**.
- **Rust / Cargo** — `winget install Rustlang.Rustup`; close and reopen
  PowerShell afterwards.
- **Microsoft C++ Build Tools** — the
  [Visual Studio 2022 Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
  with the **"Desktop development with C++"** workload (plus the ARM64 build
  tools on Windows on ARM).

```powershell
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
bun run setup:api   # create the Python environment with uv
bun run dev         # Electron with hot reload; it starts and supervises the backend
```

Use `bun run desktop-prod` to build and launch the production bundle, or
`bun run dist` to create a local NSIS installer in `electron/release/` without
publishing. See [Electron setup](../../electron/README.md) for backend
configuration.

## HF_TOKEN persistence

The **recommended path** is the in-app **Settings → Credentials** panel: it
writes the token to VoiceStudio's encrypted SQLite store *and* to the canonical
`huggingface_hub` location, so every subprocess the app spawns picks it up.

If you prefer setting an environment variable directly (power-user / CLI runs
from source), use **PowerShell** with `[Environment]::SetEnvironmentVariable`:

```powershell
[Environment]::SetEnvironmentVariable("HF_TOKEN","hf_yourtokenhere","User")
```

That writes to the user-scope environment and is picked up by every **new**
shell — close and reopen PowerShell or your terminal to see it.

> **Don't use `setx`.** `setx HF_TOKEN "hf_..."` works in theory but has
> three real gotchas that produce "I set it but it's empty" bug reports:
> it doesn't propagate to the current shell, it silently truncates values
> longer than 1024 chars, and it doesn't escape `%` characters. Use the
> in-app panel or the PowerShell one-liner above.

Full HF token guide: [docs/setup/huggingface-token.md](../setup/huggingface-token.md).

## Triton / torch.compile OOM

<a id="torch-compile-oom"></a>

On Windows, certain TTS engines (notably IndexTTS 2.5 and some CosyVoice paths)
trigger `torch.compile` / Triton kernel compilation during the first
synthesise call. On machines with <16 GB VRAM, that compile step can OOM
*before* the audio render even begins — the error usually surfaces as
`OutOfMemoryError: CUDA out of memory` or `RuntimeError: Triton compilation
failed`.

For the built-in remedy, open **Settings → Performance** and turn on **Disable torch.compile**. In-process generation then follows the eager path, while every engine child launched by VoiceStudio receives `TORCH_COMPILE_DISABLE=1`. The tradeoff is a modest loss of maximum throughput.

**From the CLI / from source:** set the env var manually before launching:

```powershell
$env:TORCH_COMPILE_DISABLE = "1"
bun run dev
```

Although [#65](https://github.com/debpalash/VoiceStudio/issues/65) describes a Windows-only memory problem, the control is intentionally not restricted to Windows. Other hosts can need the same eager fallback for compiler failures, including the older-NVIDIA crash tracked in [#2135](https://github.com/debpalash/VoiceStudio/issues/2135).

## Hugging Face token (optional but recommended)

See [docs/setup/huggingface-token.md](../setup/huggingface-token.md).

## Troubleshooting

Hit a wall? See [docs/install/troubleshooting.md](troubleshooting.md).

Migration configuration is kept ASCII so Alembic can read it under Windows locale code pages as well as UTF-8. This applies to source installs and direct Alembic commands.
