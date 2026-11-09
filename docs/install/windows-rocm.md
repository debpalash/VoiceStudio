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

- Windows 11 x64 and a GPU. The existing ROCm 7.2.1 recipe targets AMD's
  [Radeon matrix](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2.1/docs/compatibility/compatibilityrad/windows/windows_compatibility.html)
  and [Ryzen matrix](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2.1/docs/compatibility/compatibilityryz/windows/windows_compatibility.html).
  The ROCm 10 recipe targets RDNA 1 `gfx1010` (Navi 10, e.g. RX 5700 / 5700 XT)
  and RDNA 2 `gfx1030`, `gfx1031` and `gfx1032`; it has been tested on an
  RX 5700 (`gfx1010`) and an RX 6700 XT (`gfx1031`). The presence of a native
  wheel does not imply AMD certification of every product or driver.
- An AMD graphics driver compatible with the selected GPU and ROCm runtime.
  For the 7.2.1 recipe, follow AMD's
  [driver and installation guide](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2.1/docs/install/installryz/windows/install-pytorch.html).
- Microsoft Visual C++ 2015–2022 x64 Redistributable. Source setup checks this
  prerequisite locally and stops with manual installation guidance when missing.
  Source and installed Electron users must install or verify the [official Microsoft x64 Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)
  themselves before starting ROCm runtime setup.
- Space for the SDK, Python environment and temporary package downloads;
  optional engine environments and model weights require additional space.

Both recipes select x64 Python **3.12**. RDNA 3, 3.5 and 4 retain ROCm **7.2.1**,
Torch/audio **2.9.1+rocm7.2.1**, vision **0.24.1+rocm7.2.1** and the exact HIP
CTranslate2 **4.8.2** archive/wheel/DLL hashes. RDNA 1 and 2 use ROCm **10.0.0**,
Torch **2.13.0+rocm10.0.0**, audio **2.11.0.2+rocm10.0.0** and vision
**0.28.0+rocm10.0.0**; RDNA 2 adds the expanded-target HIP CTranslate2 **4.8.4**
described below. Setup identifies the GPU by PCI ID and installs only that
architecture's Torch, vision and SDK device packages, with native kernels and no
`HSA_OVERRIDE_GFX_VERSION`. Mixed AMD architectures are refused because selecting
the wrong device package would be unsafe. Windows ARM64, Windows 10 and other
architectures remain outside this recipe's scope. A standalone HIP SDK on PATH
is insufficient.

Repairs require a complete GPU inventory and use that same snapshot for AMD
architecture selection; partial registry reads fail before package changes.
Malformed SDK metadata and incompatible ROCm major/minor families are rejected,
including replacement of an installed ROCm 7 family with ROCm 10 or vice versa.
Use a fresh runtime for an intentional family change; a failed detection must
never downgrade a working install.

#### CTranslate2 on RDNA 1 (`gfx1010`) stays on the CPU

The pinned HIP CTranslate2 **4.8.2** is built against the ROCm 7.2.1 SDK. On ROCm 10
it loads and reports a device with `float16` support, but every inference fails at
kernel launch (`hipErrorInvalidImage: device kernel image is invalid`, measured on an
RX 5700 / `gfx1010`), and the RDNA 2 expanded-target build carries no `gfx1010`
kernels. The recipe therefore pins no HIP CTranslate2 for `gfx1010` and reports
`ct2_supported: false`. Consequences:

- PyTorch TTS engines run on the GPU and are the reason to opt in here.
- Everything backed by CTranslate2 — faster-whisper ASR, diarization — stays on the
  CPU. Its `hip_ct2_available()` returns `False` without probing, so no path
  advertises GPU ASR that would then fail.
- Setup still installs the project's **locked CPU** `ctranslate2` (hash-checked
  against `uv.lock`), because the frozen sync skips that package along with its
  HIP replacement. Leaving it out would break `faster-whisper` entirely — and with
  it the CPU fallback users are meant to keep. `availability_status()` therefore
  reports "no opinion" rather than "unavailable", so the CPU backend stays
  selectable in Settings → Performance.
- Installing a HIP CTranslate2 wheel yourself does not make ASR use the GPU; the
  recipe pins none for `gfx1010`, and the refusal is by recipe, not by what
  happens to be importable.

### What setup downloads, and how it is checked

Setup downloads from AMD's `repo.radeon.com` for the existing recipe or
`stable.repo.amd.com` for RDNA 1 and 2. The existing recipe uses the
OpenNMT/CTranslate2 release archive. RDNA 2 uses an expanded-target HIP
CTranslate2 4.8.4 that this repository builds from pinned upstream
OpenNMT/CTranslate2 source (`.github/workflows/ct2-rocm-windows.yml`: commit
`78b1e17` plus upstream #2142, which adds the `gfx1031`/`gfx1032` targets) and
publishes as the prerelease
[`ct2-rocm-4.8.4-78b1e17`](https://github.com/debpalash/VoiceStudio/releases/tag/ct2-rocm-4.8.4-78b1e17);
the recipe pins that archive's size and SHA-256, the wheel's and the native
DLL's, and setup on RDNA 2 downloads only that archive for CTranslate2. RDNA 1
(`gfx1010`) downloads no CTranslate2 archive: setup installs the project's
locked CPU build through the normal package index. Every file has a SHA-256 in the recipe, and setup
refuses a file whose bytes differ. Every other package keeps the version your
environment already has, so the overlay never quietly upgrades dependencies.

Official CTranslate2 4.8.2 and 4.8.4 Windows wheels omit `gfx1031`; both can
detect an RX 6700 XT and load a model yet fail at inference with
`hipErrorInvalidImage`. The expanded build contains the missing kernels.
Changing the version number alone or bypassing binary checks is insufficient.
See the [hardware report](https://github.com/OpenNMT/CTranslate2/issues/2137#issuecomment-6572396042)
for the validated build of the same source and the limits of the short-clip tests.

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
package versions, native audio/vision imports, supported GPU architecture,
synchronized Torch arithmetic with checked results, and a grouped-convolution
kernel from the same wheel (a matmul-only check passes on GPUs whose convolution
kernels cannot run at all). For a recipe that pins a HIP CTranslate2 it also
checks the pinned DLL, CT2 GPU/float16 discovery and a checked native GPU
FP32 → FP16 → FP32 conversion, which executes CT2's own kernels and rejects
discovery-only successes. A timeout, crash, wrong result or CPU fallback fails
readiness. This
proves neither speech inference nor WhisperX/MIOpen compatibility.

For a separate **local CT2 speech** check, supply an already-installed local CT2
model folder and an existing PCM speech WAV:

```powershell
& .\.venv\Scripts\python.exe scripts\smoke_windows_rocm.py --model 'C:\models\local-ct2' --wav 'C:\audio\speech.wav'
```

The smoke test never resolves a remote model ID or downloads assets. It consumes
GPU/float16 inference and requires nonempty speech segments with finite times;
the report omits transcripts and user paths. Model-free readiness and local CT2
inference are reported as different scopes. On RDNA 1 (`gfx1010`) there is no
verified HIP CTranslate2, so
the speech-scope smoke is unavailable and the model-free report says so with
`ct2_supported: false`.
## Engine boundaries

| Path | Windows ROCm behavior |
| --- | --- |
| Main Torch TTS and voice-cloning audio input | Uses the matched main environment. Historical RX 6700 XT results reported on 2026-11-02 (illustrative, not certification of the current revision or other Radeon hardware): native OmniVoice TTS and synthetic-reference cloning on GPU/fp16 at 16 steps, plus CPU/fp32 TTS at four steps with four Torch threads. Reference audio retains explicit soundfile compatibility on torchaudio 2.9 and later. Speaker similarity and other engines need their own tests. |
| PyTorch Whisper | The matched opt-in Windows Torch recipe advertises ROCm independently of CT2. Historical RX 6700 XT results reported on 2026-11-02 (illustrative, not certification of the current revision or other Radeon hardware): both transcription APIs passed on GPU/fp16 and CPU/fp32 using tiny.en; runtime evidence reports the actual pipeline device and dtype. English-only checkpoints accept English transcription without pretending to support translation. |
| Faster-whisper, including the crash-isolated worker | GPU is admitted only for the pinned HIP binary plus bounded local verification; on RDNA 1 (`gfx1010`) it runs on the CPU. ROCm 10 runs normal faster-whisper in a separate inference process for both GPU and CPU, preserving CPU selection and memory fallback without mixing Torch's LLVM OpenMP with CT2's Intel OpenMP. Model, VAD, timestamps and decode/request options are preserved. |
| WhisperX transcription | ROCm 10 refuses this in-process CT2 path before imports or cached pipeline use because its CPU execution/fallback mixes incompatible OpenMP runtimes. Use faster-whisper or PyTorch Whisper. The ROCm 7 recipe retains its existing CPU path; GPU execution remains unverified. |
| Argos offline translation | ROCm 10 refuses Argos before `argostranslate.translate` is imported, both when listing translation engines and when translating, because it runs CTranslate2 inside the backend process beside Torch. Choose NLLB (local) or an online provider. The ROCm 7 recipe is unchanged. |
| pyannote speaker detection, wav2vec2 alignment | GPU execution is gated before imports/cached pipelines: MIOpen Unicode-path and SDK-header compatibility is unverified. CPU selection retains the existing Torch pipeline; it is separate from WhisperX's CT2 transcription and is not certified by the basic-ASR tests. |
| Unavailable forced alignment | Keeps the transcript and native word timestamps; alignment precision can be lower. It does not discard requested speech or translation content. |
| PyTorch engines in the main environment (PyTorch Whisper, and in-process VoxCPM2, MOSS-TTS-Nano and CosyVoice) | A HIP Torch build exposes AMD devices through `torch.cuda`. Actual support depends on the engine and GPU. |
| FunASR and NeMo Parakeet | Windows ROCm support is unverified; these engines do not claim ROCm routing on Windows. Choose CPU in Settings → Performance when needed. |
| Optional engine sidecars | Separate environments and compatibility. Main readiness never certifies them. A CUDA or CPU wheel routes to CPU on an AMD host. Routing stays unknown when GPU execution has not been verified, since a HIP build alone does not certify a particular GPU. |

These are short offline API tests, not a full hardware/quality certification.
The 16-step CPU-only OmniVoice test exceeded a five-minute test deadline;
four-step CPU speech completed and transcribed correctly, but was much slower
than GPU execution. Model loading and AudioSeal startup are separate from warm
generation. Startup performance inventory checks package/model metadata rather
than importing Transformers or every installed model runtime into the API.
Before saving changed engine selections, presets validate runtime readiness in
a bounded offline child process. Failed checks preserve the existing selection;
automatic ASR selection uses the same isolated validation and retains fallback.
These model-free checks do not replace normal model load-time validation.
ROCm 10 capture selection also verifies faster-whisper's native import in an
offline child before choosing it, including CPU mode. A package whose DLLs
cannot load therefore leaves PyTorch Whisper available as the capture fallback;
CPU checks do not run GPU verification or load CT2 into the API process.
Both normal and crash-isolated faster-whisper catalogue entries use this same
child-only CPU availability proof, including failed-import fallback.
Automatic and explicit preset checks also keep that CT2 import in a separate
child, away from the readiness worker that inspected Torch hardware.
Readiness results, including failures and timeouts, are reused for at most
30 seconds to avoid repeated native imports; concurrent checks for the same
identity share one child, including failed results. PyTorch Whisper runtime
availability also verifies the actual lazy Transformers pipeline import in a
child; inventory alone still reads installed-package metadata. Package, device, interpreter or
parent import-failure changes invalidate that result immediately.

IndexTTS keeps its Python 3.10/3.11 requirement; CosyVoice keeps Torch 2.7;
VoxCPM2 keeps Torch 2.11. Neither cp312 main-environment recipe is applied to these
environments. Managed Windows sidecars retain their own CPU availability and
upstream requirements. Known non-HIP IndexTTS metadata keeps CPU fallback on
all platforms. Only explicit Windows ROCm opt-in treats unknown sidecar metadata
conservatively; default unknown-metadata behavior stays the same across platforms.
On the ROCm 10 overlay, IndexTTS, dots.tts and MOSS-TTS-v1.5 still avoid bf16 GEMM
on `gfx103*` (RDNA 2) because it can crash rocBLAS there. The guard is keyed to those
architectures, not to the overlay, because bf16 GEMM was measured working on
`gfx1010` (matmul at 1024–4096 plus 25 repeated 2048×2048 iterations, all finite).
Existing manually installed HIP sidecars remain detectable; that detection alone
is not an inference certification. No engine is certified GPU-ready just because
the main environment passed. Native/Vulkan engines have their own installation
and capability checks.

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
replace packages. Remove a manually set HSA override before running the ROCm 10 recipe; it uses
native kernels and verification rejects an override. Do not delete voices, projects, model caches or
optional-engine environments to repair the main runtime.

Only explicitly started installation downloads AMD packages and the pinned
OpenNMT/CTranslate2 GitHub release (7.2.1 recipe) or, once pinned, this
repository's expanded-target CTranslate2 release (RDNA 2), with normal dependency
resolution through package indexes. Verification and local smoke are offline. This installation
work incorporates focused contributions from
[#2600](https://github.com/debpalash/VoiceStudio/pull/2600) and
[#2607](https://github.com/debpalash/VoiceStudio/pull/2607); it does not absorb
their whole draft or mix the separate recipe proposed in
[#2908](https://github.com/debpalash/VoiceStudio/pull/2908).

Packaged Windows execution and speech inference remain separate validation gates;
a passing model-free check does not certify an engine.

Setup repairs only confirmed missing, mismatched or damaged packages, including
native audio, vision and CTranslate2 imports with missing or corrupted wheel files.
An import exception alone can indicate a missing external prerequisite (such as
the VC runtime) and does not authorize package repair. GPU support, driver/kernel failures,
timeouts, crashes and unclassified failures stop verification without downloading
or reinstalling the overlay. CLI diagnostics include a bounded failure category;
correct the reported host problem before retrying. A successful package repair
must still pass the complete model-free GPU verification.
