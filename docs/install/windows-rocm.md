# Opt-in Windows ROCm

VoiceStudio can install AMD's matched native Windows stack when you explicitly
set `OMNIVOICE_TORCH_VARIANT=rocm`. Source setup and Electron use the same
checked-in [recipe](../../scripts/windows-rocm-recipe.json) and installer.
The default Windows setup remains CUDA on NVIDIA, otherwise CPU, so a Radeon
(including the RX 9070 / 9070 XT and RX 9060, `gfx1201`/`gfx1200`) runs PyTorch
engines on the CPU until you opt in here; the setup screen's GPU check and
**Settings → Performance → GPU acceleration** say so and name this opt-in. This
option does not enable the Ryzen AI NPU, install a graphics driver, or download
models.

## Prerequisites and exact scope

- Windows 11 x64 and a GPU listed in AMD's **7.2.1**
  [Radeon matrix](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2.1/docs/compatibility/compatibilityrad/windows/windows_compatibility.html)
  or [Ryzen matrix](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2.1/docs/compatibility/compatibilityryz/windows/windows_compatibility.html).
  A matching `gfx` architecture alone does not establish support for an unlisted product.
- AMD's required [26.2.2 graphics driver](https://www.amd.com/en/resources/support-articles/release-notes/RN-RAD-WIN-26-2-2.html),
  installed by you, following the
  [AMD installation guide](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2.1/docs/install/installryz/windows/install-pytorch.html).
- Microsoft Visual C++ 2015–2022 x64 Redistributable. Source setup checks this
  prerequisite locally and stops with manual installation guidance when missing.
  Source and installed Electron users must install or verify the [official Microsoft x64 Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)
  themselves before starting ROCm runtime setup.
- Space for the SDK, Python environment and temporary package downloads;
  optional engine environments and model weights require additional space.

The recipe selects x64 Python **3.12**, ROCm **7.2.1**, Torch/audio
**2.9.1+rocm7.2.1**, vision **0.24.1+rocm7.2.1**, and the exact HIP
CTranslate2 **4.8.2** archive/wheel/DLL hashes. Windows ARM64, Windows 10 and
unlisted GPUs, including RX 6700 XT, are outside this recipe's supported scope.
Do not use Linux ROCm wheel indexes or an `HSA_OVERRIDE_GFX_VERSION` remap to
claim support for those systems. A standalone HIP SDK on PATH is insufficient.

### What setup downloads, and how it is checked

Setup downloads only from AMD's `repo.radeon.com` (the SDK and PyTorch wheels)
and the OpenNMT/CTranslate2 GitHub release (the HIP CTranslate2 archive). Every
one of those files has a SHA-256 in the recipe, and setup refuses a file whose
bytes differ. Every other package keeps the version your environment already
has, so the overlay never quietly upgrades dependencies.

Before this release, `OMNIVOICE_TORCH_VARIANT=rocm` was ignored on Windows. It
now selects this stack. If it is already set on a machine with a working CUDA
or CPU runtime, the app shows the setup screen instead of switching on its
own; nothing downloads until you start setup there.

## Source setup

From the repository root, after the normal
[Windows source prerequisites](windows.md#building-from-source):

```powershell
$env:OMNIVOICE_TORCH_VARIANT = 'rocm'
bun run setup:api
bun run dev
```

The setup command selects cp312, excludes the locked Torch/CTranslate2/CUDA
packages before syncing other dependencies, then installs and verifies the
matched Windows overlay. Direct `scripts/setup.py` also invokes that shared
installer when opted in, but must already run under Windows x64 Python 3.12;
use `bun run setup:api` to select the interpreter automatically.

## Installed Electron app

Fully quit VoiceStudio, including its tray process. Open PowerShell, set the
variable, then launch your installed executable; replace the example path with
the executable location shown by your VoiceStudio shortcut:

```powershell
$env:OMNIVOICE_TORCH_VARIANT = 'rocm'
& 'C:\path\to\VoiceStudio.exe'
```

Start or resume runtime setup in the app. Keep using this launch method while
you want ROCm selected: these commands affect the current shell and its child
processes only. They do not change the system environment. Existing models,
voices and projects stay in place. Changing the recipe or Python ABI invalidates
the runtime readiness stamp and requires verification/repair.

This feature must be present in the installed build; older releases do not gain
it from an environment variable. See the
[Releases page](https://github.com/debpalash/VoiceStudio/releases) for available builds.

## Verify locally

Run these from the source root, or from the managed runtime directory named in
Electron's setup log, using **that environment's interpreter**:

```powershell
& .\.venv\Scripts\python.exe -m backend.core.windows_rocm.install --verify-only
& .\.venv\Scripts\python.exe scripts\smoke_windows_rocm.py
```

Both commands without speech inputs perform **model-free** checks: exact ABI and
package versions, native audio/vision imports, the pinned HIP CT2 DLL, supported
GPU architecture, synchronized Torch arithmetic with checked results, and CT2
GPU/float16 discovery. A timeout, crash or CPU fallback fails readiness. This
proves neither speech inference nor WhisperX/MIOpen compatibility.

For a separate **local CT2 speech** check, supply an already-installed local CT2
model folder and an existing PCM speech WAV:

```powershell
& .\.venv\Scripts\python.exe scripts\smoke_windows_rocm.py --model 'C:\models\local-ct2' --wav 'C:\audio\speech.wav'
```

The smoke test never resolves a remote model ID or downloads assets. It consumes
GPU/float16 inference and requires nonempty speech segments with finite times;
the report omits transcripts and user paths. Model-free readiness and local CT2
inference are reported as different scopes.

## Engine boundaries

| Path | Windows ROCm behavior |
| --- | --- |
| Main Torch TTS and voice-cloning audio input | Uses the matched main environment; local soundfile compatibility preserves explicit soundfile input on torchaudio 2.9. Model/engine kernels still need real hardware testing. |
| Faster-whisper, including the crash-isolated worker | GPU is admitted only for the pinned HIP binary plus bounded local verification; CPU overrides remain effective. Auto selection can prefer this verified basic-ASR path. |
| WhisperX, pyannote speaker detection, wav2vec2 GPU alignment | GPU execution is gated before imports/cached pipelines: MIOpen Unicode-path and SDK-header compatibility is unverified. Choose CPU in Settings → Performance (`OMNIVOICE_DEVICE=cpu`) to use these pipelines. |
| Unavailable forced alignment | Keeps the transcript and native word timestamps; alignment precision can be lower. It does not discard requested speech or translation content. |
| Optional engine sidecars | Separate environments and compatibility. Main readiness never certifies them. |

IndexTTS keeps its Python 3.10/3.11 requirement; CosyVoice keeps Torch 2.7;
VoxCPM2 keeps Torch 2.11. The cp312/Torch 2.9 recipe is **not** applied to these
environments. Managed Windows sidecars retain their own CPU availability and
upstream requirements. Known non-HIP IndexTTS metadata keeps CPU fallback on
all platforms. Only explicit Windows ROCm opt-in treats unknown sidecar metadata
conservatively; default unknown-metadata behavior stays the same across platforms.
Existing manually installed HIP sidecars remain detectable;
that detection alone is not an inference certification. No engine is certified
GPU-ready just because the main environment passed. Native/Vulkan engines have
their own installation and capability checks.

## Repair and rollback

For source installs, repeat the opted-in `bun run setup:api`. For Electron,
fully quit and relaunch with the same opt-in, then resume/retry setup. Strict
verification distinguishes an interrupted or incompatible install from a
working one; repair does not overlay the project's locked CUDA packages onto a
healthy HIP environment. Failed installs remain retryable and preserve user data.

Once setup finishes in the installed app, the choice is saved in
`%USERPROFILE%\.config\omnivoice\env`, so later launches keep ROCm without the
variable. Finishing setup with another choice removes it.

To return to the supported CPU path, fully quit and choose CPU explicitly:

```powershell
$env:OMNIVOICE_TORCH_VARIANT = 'cpu'
# Source checkout:
bun run setup:api
# Or launch the installed Electron executable and resume runtime setup:
& 'C:\path\to\VoiceStudio.exe'
```

Use the command for your installation type, not both. `OMNIVOICE_DEVICE=cpu`
alone selects CPU **execution** in the existing HIP environment; it does not
replace packages. Remove a manually set HSA override when troubleshooting an
otherwise supported GPU. Do not delete voices, projects, model caches or
optional-engine environments to repair the main runtime.

Only explicitly started installation downloads AMD packages and the pinned
OpenNMT/CTranslate2 GitHub release, with normal dependency resolution through
package indexes. Verification and local smoke are offline. This installation
work incorporates focused contributions from
[#2600](https://github.com/debpalash/VoiceStudio/pull/2600) and
[#2607](https://github.com/debpalash/VoiceStudio/pull/2607); it does not absorb
their whole draft or mix the separate recipe proposed in
[#2908](https://github.com/debpalash/VoiceStudio/pull/2908).

Fresh supported AMD hardware inference, packaged Windows execution and upstream
CI remain separate validation gates; a passing mocked test suite is not that evidence.

Setup repairs only confirmed missing, mismatched or damaged packages, including
native audio, vision and CTranslate2 imports with missing or corrupted wheel files.
An import exception alone can indicate a missing external prerequisite (such as
the VC runtime) and does not authorize package repair. GPU support, driver/kernel failures,
timeouts, crashes and unclassified failures stop verification without downloading
or reinstalling the overlay. CLI diagnostics include a bounded failure category;
correct the reported host problem before retrying. A successful package repair
must still pass the complete model-free GPU verification.
