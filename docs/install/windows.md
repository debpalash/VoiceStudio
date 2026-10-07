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

## Install

Download the installer matching your PC from the
[Releases page](https://github.com/debpalash/VoiceStudio/releases/latest):

| PC | Installer |
|---|---|
| Intel / AMD (x64) | `VoiceStudio-Electron-<version>-win-x64.exe` |
| Windows on ARM (Snapdragon X etc., experimental) | `VoiceStudio-Electron-<version>-win-arm64.exe` (releases without it: the x64 installer also works, under emulation) |

The setup wizard installs for the current user by default (no administrator
rights needed) and lets you choose the installation folder. Compare the download with the
release's `SHA256SUMS.txt` before running it.

Or, from a regular (non-admin) PowerShell, let the
[install script](script.md) pick the right installer, verify it, and open the
wizard:

```powershell
irm https://voicestudio.sh/install | iex
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
setup screen lists them as its CPU preset. Set `OMNIVOICE_TORCH_VARIANT=cuda`
(or `cpu`) before launching to override the detection.
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
2. On the setup screen, press **Change…** beside **App environment** to put the
   Python environment on the drive. uv's download cache and managed Python stay
   beside that environment, so multi-GB wheels are not staged on `C:`.
3. In **Settings > Storage**, move the application data folder (voices,
   projects, settings) and the model cache to the drive.

The drive must be connected when VoiceStudio starts.

## GPU support on Windows

<a id="gpu-support"></a>

**PyTorch GPU acceleration on Windows is NVIDIA/CUDA-only.** The Windows install
ships the CUDA build of PyTorch; with an NVIDIA GPU and a regular NVIDIA
driver it's picked up automatically (no CUDA Toolkit install needed).

**AMD GPUs — including Ryzen / Ryzen AI integrated Radeon graphics — do not
accelerate PyTorch engines on Windows.** The runtime has PyTorch 2.8 as either
the NVIDIA CUDA build or (with no NVIDIA driver present) the CPU-only build;
neither can drive a Radeon card, so those engines run on the CPU. pytorch.org publishes no Windows ROCm wheels, and VoiceStudio's ROCm
option (`OMNIVOICE_TORCH_VARIANT=rocm`) is Linux-only — it is ignored on Windows
rather than failing setup. (The Ryzen AI NPU is likewise not used.) Everything
still works on CPU, just slower. What you can do today:

- **Use an engine with its own GPU runtime.** [audio.cpp](../engines/audio-cpp.md)
  (Breeze-TTS-2) ships a Vulkan build that runs on Radeon GPUs: install the
  runtime from **Settings → Models**. VoiceStudio picks the discrete GPU
  automatically.
- **Run on Linux** (native, or the ROCm Docker image) for ROCm acceleration of
  the PyTorch engines — see [linux.md — AMD GPU (ROCm)](linux.md#amd-gpu-rocm).
- **Advanced / unsupported: AMD's own Windows ROCm wheels.** AMD publishes
  PyTorch ROCm wheels for Windows (`https://repo.amd.com/rocm/whl-multi-arch/`,
  Python 3.11–3.14, RDNA 3 / RDNA 4 cards such as the RX 7000 and RX 9000 series).
  They are PyTorch 2.9 or newer, not the 2.8 the engines here are validated
  against, and faster-whisper (CTranslate2) needs its own separate HIP build for
  the GPU, so expect parts of the app (WhisperX is a reported example) to break. VoiceStudio does not install them, and no engine
  parity is claimed. DirectML is not an option either: `torch-directml` needs
  PyTorch 2.4.

**Settings → Performance → GPU acceleration** shows exactly what applies to your
machine: the GPUs Windows reports, which PyTorch build is installed, and a
verdict for every engine (uses the GPU, runs on the CPU and why, or CPU by
design). **Settings → About → Run self-check** names the card instead of just
saying "no GPU acceleration detected".

## Building from source

Use this for development or to run current `main`. The install script's
`main` mode (`$env:VOICESTUDIO_INSTALL_MODE='main'; irm https://voicestudio.sh/install | iex`)
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

The **recommended path** is the in-app **Settings → API Keys** panel: it
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

**The one-click fix:** open **Settings → Performance** in the app and toggle
**"Disable torch.compile"** on. That sets the `TORCH_COMPILE_DISABLE=1` env var
on every engine subprocess VoiceStudio spawns and forces the in-process engine
to eager mode as well. You'll lose a few percent of peak throughput in exchange
for the engine actually loading.

**From the CLI / from source:** set the env var manually before launching:

```powershell
$env:TORCH_COMPILE_DISABLE = "1"
bun run dev
```

The OOM this section describes is Windows-specific, but the toggle itself works
on **every** platform — it used to be greyed out elsewhere, which left Linux and
macOS users with no way to switch off a `torch.compile` that was breaking their
engine. Tracking issues:
[#65](https://github.com/debpalash/VoiceStudio/issues/65) (this OOM) and
[#2135](https://github.com/debpalash/VoiceStudio/issues/2135) (the same toggle
on Linux/CUDA).

## Hugging Face token (optional but recommended)

See [docs/setup/huggingface-token.md](../setup/huggingface-token.md).

## Troubleshooting

Hit a wall? See [docs/install/troubleshooting.md](troubleshooting.md).

Migration configuration is kept ASCII so Alembic can read it under Windows locale code pages as well as UTF-8. This applies to source installs and direct Alembic commands.
