# VoiceStudio — Install on Windows

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

### Windows installers

Download the installer matching your PC from the
[Releases page](https://github.com/debpalash/VoiceStudio/releases/latest):

| PC | Installer |
|---|---|
| Intel / AMD (x64) | `VoiceStudio-Electron-<version>-win-x64.exe` |
| Windows on ARM (Snapdragon X etc., experimental) | `VoiceStudio-Electron-<version>-win-arm64.exe` (releases without it: the x64 installer also works, under emulation) |

`irm https://voicestudio.sh/install | iex` picks the right one automatically.

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

### Everything on an external drive (portable-style use)

The Electron app has no single-file portable build. To keep it all on another
drive (for example an external SSD):

1. Run the installer and choose the external drive on its **Installation
   Folder** page.
2. On the setup screen, press **Change…** beside **App environment** to put the
   Python environment on the drive.
3. In **Settings > Storage**, move the application data folder (voices,
   projects, settings) and the model cache to the drive.

The drive must be connected when VoiceStudio starts.

## Legacy Tauri installation and troubleshooting

The instructions below apply to the sunset Tauri app and existing Tauri installers.

This page is self-contained: follow it top to bottom and you'll end up with a
working VoiceStudio install on Windows 10 / 11 (x64).

## Prerequisites

### Using the MSI installer

- **Windows 10 (21H2 or newer) or Windows 11**, x64.
- **~10 GB free disk** for the app, its Python environment, and model weights.
- Optional: an **NVIDIA GPU + driver** for CUDA acceleration — see
  [GPU support on Windows](#gpu-support). AMD GPUs do not accelerate PyTorch
  engines on Windows (audio.cpp can use them via Vulkan).

That's it — Python, FFmpeg, and the model weights are bundled or bootstrapped
by the app itself on first launch. No toolchain needed.

### Building from source

Everything above, plus the toolchain:

- **Git for Windows** — `winget install --id Git.Git -e`. Needed for
  `git clone`, and it includes **Git Bash**, which `bun run tauri:desktop-prod`
  uses to run its build-and-launch script. Without it, `desktop-prod` stops
  with an error telling you to install it.
- **Python 3.11+** — `winget install Python.Python.3.11` (or download from
  [python.org](https://www.python.org/downloads/windows/)).
- **Microsoft C++ Build Tools** — required by some PyPI source distributions
  (`pyannote.audio`, occasional torch wheel rebuild). Install via the
  [Visual Studio 2022 Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
  with the **"Desktop development with C++"** workload checked.
- **Bun** — `powershell -c "irm bun.sh/install.ps1 | iex"`.
- **FFmpeg** — `winget install Gyan.FFmpeg`.
- **Rust / Cargo** — `winget install Rustlang.Rustup` or download `rustup-init.exe` from [rustup.rs](https://rustup.rs/).
  After installing Rustup, close and reopen PowerShell before running `bun run tauri:desktop-prod`.

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

## Install (from source)

One-liner from a regular (non-admin) PowerShell — installs Git, FFmpeg, uv,
and bun via winget, then clones and builds:

```powershell
irm https://voicestudio.sh/install | iex
```

Or manually:

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
bun run tauri:desktop-prod
```

The first launch creates the Python venv via `uv`, syncs deps, and downloads
model weights. The splash screen shows progress.

> **Note:** `bun run tauri:desktop-prod` runs a bash script under the hood. You can
> launch it from PowerShell or cmd as shown — it finds Git Bash automatically
> (installed with Git for Windows, see Prerequisites). If no Git Bash is
> found, it prints instructions instead of failing silently. Alternatives
> that don't need bash: `bun run tauri` (dev mode) or the pre-built MSI
> below.

## Install (pre-built MSI)

Download the latest MSI from the
[Releases page](https://github.com/debpalash/VoiceStudio/releases/latest).

| Artifact name | Scope | Administrator required | Default location |
|---|---|---|---|
| `VoiceStudio_<version>_x64_en-US.msi` | All users (per-machine) | Yes | `%ProgramFiles%\VoiceStudio` |
| `VoiceStudio_Current_User_<version>_x64_en-US.msi` | Current user only | No | `%LOCALAPPDATA%\VoiceStudio (Current User)` |

Run the artifact matching the required scope and follow the wizard. The
per-user artifact can be installed, updated, and removed by a standard Windows
account. It has a separate Windows Installer upgrade identity, shortcut name,
and signed updater manifest, so it cannot upgrade or uninstall the per-machine
copy (or vice versa). Both copies use the same VoiceStudio data directory; do
not run them simultaneously against the same projects.

Automatic updates preserve the installed scope. Managed deployments should
continue to use the per-machine MSI. Users without elevation should choose the
artifact containing `Current_User`.

### Installing to a different drive

<a id="install-other-drive"></a>

The wizard's **directory picker** lets you install the app to any **local**
drive (D:, E:, …). Two caveats:

- **Mapped network drives (Z: → a share) are not supported** — this is a
  Windows Installer limitation, not a VoiceStudio bug: MSI custom actions run
  as a service account that doesn't see per-user drive mappings, so the
  install fails or rolls back. Install to a local drive instead.
- The install location only moves the ~200 MB app itself. The big data
  (models, voices, projects — tens of GB) lives in the **data directory**,
  which you move independently: **Settings → Storage → Models directory**
  in-app, or `OMNIVOICE_DATA_DIR` / [Portable mode](#portable-install) for
  the whole data tree.

When the Python **environment folder** (first-run setup → Advanced, or
portable mode) is on a different drive than Windows, the installer keeps
uv's package cache and managed Python **inside the environment folder**
(`uv-cache/`, `uv-python/`) instead of `%LOCALAPPDATA%\uv`. Without that,
every wheel (PyTorch alone is several GB) would be staged on `C:` and then
copied across drives — filling the system drive you were trying to spare.
The same applies to engine sidecar installs when the data directory is on
another drive (`engines\.uv-cache`). An explicit `UV_CACHE_DIR` /
`UV_PYTHON_INSTALL_DIR` you set yourself always wins.

If an install to a local non-C: drive fails anyway, capture a log with
`msiexec /i VoiceStudio*.msi /L*V install.log` and
[open an issue](https://github.com/debpalash/VoiceStudio/issues) with it
— that log shows exactly which step rolled back.

## Managed WebView2 installation

The MSI checks the `pv` version value in both the machine-wide and current-user
Edge Update registry keys for the WebView2 Runtime product
`{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}`. When no `pv` version is detected and
the bootstrapper was not explicitly allowed (or was explicitly disabled), the
install fails without making a network request and directs the operator to the
offline runtime. `ALLOWWEBVIEW2BOOTSTRAP=1` instead selects the opt-in path
documented below.

Managed or offline deployments can prohibit that network action:

```powershell
msiexec /i VoiceStudio_0.5.2_x64_en-US.msi DISABLEWEBVIEW2BOOTSTRAP=1 AUTOLAUNCHAPP=0 /qn /L*V "%TEMP%\VoiceStudio-install.log"
```

The per-user artifact never contains a WebView2 download or installer action.
It does not require an elevated terminal, and fails closed when the runtime is
absent:

```powershell
msiexec /i VoiceStudio_Current_User_0.5.2_x64_en-US.msi DISABLEWEBVIEW2BOOTSTRAP=1 AUTOLAUNCHAPP=0 /qn /L*V "%TEMP%\VoiceStudio-user-install.log"
```

For the per-machine artifact, `DISABLEWEBVIEW2BOOTSTRAP=1` leaves detection
enabled but prevents every WebView2 PowerShell, download, and installer action.
For the per-user artifact that property is redundant because those actions are
omitted from the MSI. If the runtime is absent, either MSI fails before copying
VoiceStudio and tells the operator to deploy the
[Microsoft Evergreen Runtime](https://developer.microsoft.com/microsoft-edge/webview2/#download-section)
first. `/L*V` records detection and action selection in the named Windows
Installer log.

An interactive administrator may explicitly permit Microsoft's network
bootstrapper for the per-machine artifact by setting
`ALLOWWEBVIEW2BOOTSTRAP=1`. Only then does that MSI download
`https://go.microsoft.com/fwlink/p/?LinkId=2124703` with PowerShell and invoke
it silently with `/install`; `DISABLEWEBVIEW2BOOTSTRAP=1` always wins if both
properties are supplied. `ALLOWWEBVIEW2BOOTSTRAP` has no effect on the
per-user artifact.

`AUTOLAUNCHAPP=0` is independent: it prevents VoiceStudio from launching after
a successful silent install. It does not change WebView2 detection; on the
per-machine artifact it also does not disable an otherwise permitted setup.

## Portable install (Windows)

<a id="portable-install"></a>

VoiceStudio has a **Portable** mode: instead of scattering data across
`%APPDATA%` and `%LOCALAPPDATA%`, the whole install — Python env, model
weights, voices, projects, settings — lives in a single folder. By **default**
that is `OmniVoiceStudio-Data` next to the executable; you can put it anywhere
writable from the setup screen (see below). Moving or copying the app folder
(exe + that data folder together) relocates the entire install, USB-stick
style.

The first-run setup screen offers Portable, and the folder is **yours to
choose** — press **Change…** on the Portable folder row and point it at any
writable location (an external SSD, a second drive, a USB stick).

Where the folder lives decides how far it travels, and the setup screen tells
you which of these you're getting:

- **Inside the app's own folder** (e.g. `D:\Apps\VoiceStudio\MyData`, with the
  exe in `D:\Apps\VoiceStudio`) — the `portable.path` marker records it as a
  **relative** path. Move the app folder as a unit, to another machine or a
  different drive letter, and the install still finds itself. This is the
  USB-stick case Portable exists for.
- **Somewhere else, app folder writable** (e.g. exe in `D:\Apps`, data on
  `E:\VoiceStudio`) — the marker can only record an **absolute** path, so the
  install is tied to that exact path. Change the drive letter or the mount
  point and you'll have to point the app at it again.
- **App folder read-only** (a default `C:\Program Files` MSI install) — no
  marker can be written at all, so the location is remembered for **your user
  account only** on this machine.

That second case used to be a hard block: Portable was greyed out entirely
after a default install
([#766](https://github.com/debpalash/VoiceStudio/issues/766)). It no longer
is — you just get the machine-bound variant unless the app itself sits
somewhere writable.

If you want the fully-portable version, install to a user-writable folder:

- Re-run the MSI and choose a custom destination folder in the setup wizard
  (e.g. `D:\Apps\VoiceStudio`), or
- From a terminal:
  `msiexec /i VoiceStudio.Studio_<version>_x64_en-US.msi INSTALLDIR="D:\Apps\VoiceStudio"`

On the next launch, pick **Portable** on the first-run setup screen. With the
default folder, what lives next to the exe afterwards:

<!-- validate: skip -->
```
D:\Apps\VoiceStudio\
├── VoiceStudio.exe        ← the app
└── OmniVoiceStudio-Data\       ← the whole install, self-contained
    ├── config.json             ← install-mode + app settings
    ├── env\                    ← Python venv + backend code
    └── data\                   ← voices, projects, settings DB
        └── models\             ← model weights (HF cache)
```

Prefer the default Program Files install? **Installed** mode is the same app —
data just lives in `%APPDATA%\OmniVoice` and the model cache in
`%LOCALAPPDATA%\OmniVoice\hf_cache`.

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
bun run tauri:desktop-prod
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

### Building the current-user MSI

Build the system MSI first. `scripts/render-per-user-wix.py` requires
`--system-wxs` pointing to its fully rendered `release/wix/x64/main.wxs`,
in addition to the canonical `--source` template and `--output` destination.
The renderer preserves Tauri resource destinations while assigning distinct,
stable per-user component identities, HKCU registry keypaths, and uninstall
cleanup for nested resource folders. Missing or unrendered resources fail the
build. The canonical system installer retains its per-machine authoring.

The per-user build reuses the system build's frontend output: its config clears
`beforeBuildCommand` so a second Vite build cannot replace the hashed files
referenced by the rendered WiX template. Keep using `tauri build` for the
per-user stage; it still recompiles the shell with its own product configuration.

CI builds both scopes with a tiny executable, an external helper, and nested
resources using the CLI version locked in `bun.lock`. Its frontend-like build
hook rotates resource filenames, verifying the per-user build preserves the
system build's resource snapshot. The Windows MSI authoring
job runs after the test suite and preserves verbose WiX logs and rendered XML.
For focused diagnosis, dispatch CI with `windows_wix_diagnostic=true`; it skips
the other jobs and never signs, publishes, or installs the fixture bundles.

The release smoke installs and removes the current-user MSI using a standard
Windows account. On disposable GitHub-hosted Windows Server runners, it explicitly
allows unmanaged MSI installations for the duration of this test, then restores
the previous Installer policy value and type (or its absence), even on failure.
This test-host preparation does not change installer privileges or user machines.
Verbose MSI logs are printed if installation or removal fails. The Windows CI
job also rejects an invalid MSI and verifies policy absence, value types, account
cleanup, and verbose failure logs using Windows PowerShell 5.1.


Migration configuration is kept ASCII so Alembic can read it under Windows locale code pages as well as UTF-8. This applies to source installs and direct Alembic commands.
