# Opt-in Windows ROCm

VoiceStudio can install AMD's matched native Windows stack when you explicitly
choose **AMD ROCm (supported cards)** in desktop runtime setup. Source setup
uses `OMNIVOICE_TORCH_VARIANT=rocm`. Both paths use the same
checked-in [recipe](../../scripts/windows-rocm-recipe.json) and installer.
The default Windows setup remains CUDA on NVIDIA, otherwise CPU, so a Radeon
(including the RX 9070 / 9070 XT and RX 9060, `gfx1201`/`gfx1200`) runs PyTorch
engines on the CPU until you opt in here; the setup screen's GPU check and
**Settings → Performance → GPU acceleration** say so and name this opt-in. This
option does not enable the Ryzen AI NPU, install a graphics driver, or download
models.

Linux x64 AppImage and `.deb` builds offer the same **Runtime packages** selector
with a separate Linux package path. See [Linux ROCm setup](linux.md#amd-gpu-rocm);
the Windows recipe described here is specific to native Windows x64.

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

On first run, choose **AMD ROCm (supported cards)** under **Runtime packages**,
then choose **Install** (or **Resume** / **Retry**). Saving the choice does not
download packages. Setup persists it alongside the download-region preference
and uses it for Python selection, package installation, verification and the
managed backend's environment. No terminal or permanent environment change is
required. **Automatic** retains the existing NVIDIA-or-CPU policy.

For an already working app-managed local runtime, open **Settings > Performance >
Runtime packages**, choose the target and confirm saving it for the next start.
Current work and the running backend continue unchanged. Finish tasks, fully
quit VoiceStudio (including its tray process), and reopen it. The setup screen
then asks for an explicit install action. Choosing a different package set alone
never starts repair, downloads or deletion. Remote, attached and user-managed
backends cannot be converted through this control.

Voices, projects, model caches and storage locations are retained. Existing CPU
sidecars are not automatically converted. Installed HIP support also does not
override an explicit CPU compute-device choice; change that separately in
Performance if GPU execution is desired. Replacing packages is not a
transactional rollback: after an unsuccessful conversion, use setup retry or
deliberately choose CPU and install its package set again.

A valid explicit inherited `OMNIVOICE_TORCH_VARIANT` pins the choice and disables
the selector. Advanced launches can still use PowerShell: fully quit the app,
set the variable, then launch the executable at the location shown by its shortcut:

```powershell
$env:OMNIVOICE_TORCH_VARIANT = 'rocm'
& 'C:\path\to\VoiceStudio.exe'
```

With this advanced override, keep using the launch method while the override is
wanted; it affects the current shell and its children only. The saved GUI choice
remains available after removing the override. Changing the recipe or Python ABI invalidates
the runtime readiness stamp and requires verification/repair.

This feature must be present in the installed build; older releases do not gain
it from an environment variable. See the
[Releases page](https://github.com/debpalash/VoiceStudio/releases) for available builds.

For Windows ROCm runtimes owned by the installed app, Electron selects the
interpreter's bundled SDK by removing inherited `HIP_PATH`, `ROCM_HOME` and
`ROCM_PATH` from the child environment before backend startup, setup verification
and readiness/import checks. Windows environment settings and saved preferences
are unchanged. Source checkouts, custom backend commands and externally managed
runtimes retain these variables, as do CPU/CUDA runtimes and other platforms.
Architecture, device-visibility and MIOpen overrides still undergo the strict
profile checks below; incompatible SDK/TEMP/cache paths still reject WhisperX.

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

| Path                                                   | Windows ROCm behavior                                                                                                                                                         |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Main Torch TTS and voice-cloning audio input           | Uses the matched main environment; local soundfile compatibility preserves explicit soundfile input on torchaudio 2.9. Model/engine kernels still need real hardware testing. |
| Faster-whisper, including the crash-isolated worker    | GPU is admitted only for the pinned HIP binary plus bounded local verification; CPU overrides remain effective. Auto selection can prefer this verified basic-ASR path.       |
| WhisperX ASR and wav2vec2 GPU alignment                | Only the registered profile below is admitted, before imports or warm-cache reuse. Runtime GPU reporting uses the same qualifier.                                             |
| Pyannote speaker detection and unknown heavy pipelines | GPU execution remains gated before token resolution and cached pipeline reuse. Choose CPU in Settings → Performance to use these pipelines.                                   |
| Unavailable forced alignment                           | Keeps the transcript and native word timestamps; alignment precision can be lower. It does not discard requested speech or translation content.                               |
| Optional engine sidecars                               | Separate environments and compatibility. Main readiness never certifies them.                                                                                                 |

### Qualified WhisperX and alignment

Choose **WhisperX** in **Model Catalogue > ASR > Use** on an already configured
AMD runtime. Qualification is automatic; there is no additional terminal switch.
Auto selection continues to prefer verified faster-whisper on Windows HIP.
Both ASR and alignment use one current qualifier, also used by runtime compute
reporting. A qualified host reports ROCm acceleration instead of CPU fallback.

The registered profile requires all of the following:

- Windows x64 Python 3.12 and the unchanged checked-in 7.2.1 recipe: exact
  Torch/audio 2.9.1+rocm7.2.1, vision 0.24.1+rocm7.2.1, ROCm and SDK
  core/devel/libraries 7.2.1, and the pinned HIP CT2 4.8.2 DLL. WhisperX must be
  3.4.5 and pyannote.audio 3.4.0. A recipe change requires renewed registration.
- An available native HIP device 0 with architecture **gfx1201**, plus the
  existing bounded, model-free CT2 readiness check. Other recipe-supported GPUs
  are not thereby qualified for WhisperX.
- Physical ASCII SDK core/devel directories belonging to this interpreter.
  Expanded HIP runtime, MIOpen, rocRAND (including `rocrand_xorwow.h`) and
  rocPRIM headers must already exist. An explicit `ROCM_PATH` must match that
  managed development root. Catalogue checks never unpack or download them.
- Existing physical ASCII `TEMP` and `TMP`, compatible `TMPDIR` and Python's
  cached temporary directory, and physical ASCII effective cache roots.
  Unicode, redirected, relative and network paths do not qualify. This includes
  custom MIOpen/XDG cache roots and the home/profile roots used for default caches.
- No SDK-home, architecture-remap or visible-device overrides, and no unqualified
  MIOpen overrides. The existing application `FAST` search mode is permitted.
  Disabling MIOpen's disk cache does not qualify an unsupported SDK or TEMP path.

Versions, paths, headers, device metadata and CT2 integrity/readiness are checked
before every ASR/alignment cache reuse. Rejections remain retryable compatibility
errors, not broken-import entries. CPU and ordinary native CUDA/non-Windows
behavior are unchanged. No ASR model or default selection is changed.

The production backend passed a natural-path RX 9070 XT test with the default
`large-v3` and no model-selection override or diagnostic gate exception. WhisperX
constructed its default pyannote VAD, real ffmpeg decoded 24-to-16 kHz audio,
CT2 used HIP/float16, and wav2vec2 alignment ran on GPU. The 4.8-second and
repeated 39.6-second inputs had 12/12 and 48/48 recognized words timed; the long
input detected French automatically. The installed default batch was 1.
Class and instance runtime reporting both identified ROCm acceleration.

Supported warm models were reused. Unsupported SDK/TEMP/cache overrides were
rejected before additional model execution and recovered after restoration.
Speaker diarization remained gated with zero token-resolver calls. Both owned
process trees exited 0 without forced termination; selected protected inputs
remained unchanged and the Python audit recorded no external network, token-file
or out-of-scope write attempts. Earlier cached-small library/backend controls
remain separate diagnostic evidence. These tests establish recognized-word
timing, not every spoken word, all models/languages or packaged/fresh-Windows
acceptance; pyannote speaker diarization remains closed.

See also the [Polish guide](windows-rocm.pl.md).

IndexTTS keeps its Python 3.10/3.11 requirement. Default CosyVoice installs keep
Torch 2.7; the explicitly selected Windows HIP variant uses Torch 2.9.1.
Default VoxCPM2 installs keep Torch 2.11. A new explicitly opted-in Windows
ROCm VoxCPM2 install has its own Python 3.12/HIP 7.2.1 recipe and verified
completion receipt; see [the engine guide](../engines/voxcpm2.md). The main
runtime probe alone does not certify this sidecar. Its installer also checks
every AMD artifact against the recipe's SHA-256 and ignores ambient options
that disable hash verification. Existing healthy CPU and
user-managed environments are preserved. Other managed Windows sidecars
retain their CPU availability and upstream requirements; the cp312/Torch 2.9
overlay is not applied to them. Known non-HIP IndexTTS metadata keeps CPU fallback on
all platforms. Only explicit Windows ROCm opt-in treats unknown sidecar metadata
conservatively; default unknown-metadata behavior stays the same across platforms.
Existing manually installed HIP sidecars remain detectable;
that detection alone is not an inference certification. No engine is certified
GPU-ready just because the main environment passed. Native/Vulkan engines have
their own installation and capability checks.

## Repair and rollback

For source installs, repeat the opted-in `bun run setup:api`. For Electron,
keep the saved AMD choice, then resume/retry setup. Strict
verification distinguishes an interrupted or incompatible install from a
working one; repair does not overlay the project's locked CUDA packages onto a
healthy HIP environment. Failed installs remain retryable and preserve user data.

Readiness rejects malformed capability types, including boolean device counts,
not just failed GPU checks. Invalid diagnostics cannot certify acceleration and
do not authorize a package reinstall.

Sidecar completion markers are published only after their entire contents have
been written. An interrupted write or failed replacement preserves any previous
marker and cannot turn a new incomplete installation into a ready engine.

Either `HF_HUB_OFFLINE` or `TRANSFORMERS_OFFLINE` also prevents optional alignment
from downloading missing TorchAudio checkpoints, Hugging Face models or Punkt
data on allowed CPU/CUDA/MPS routes. An unavailable optional aligner preserves
native word timings; retry after installing assets or leaving offline mode does
not require restarting. Windows HIP alignment additionally requires the registered
profile above; speaker diarization retains its separate closed gate.

To return to CPU in Electron, choose **CPU only** in the same setup control or
save it from Performance and reopen the app, then explicitly install. For a
source checkout or an advanced environment override, choose CPU explicitly:

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
OpenNMT/CTranslate2 GitHub release. Installed packages outside the overlay stay
at their locked versions; package indexes resolve only new dependencies.
Verification and local smoke are offline. This installation
keeps regional PyPI wheel mirrors separate from the pinned AMD artifact URLs.
Mirrored frozen sync restores the bundled lock before readiness is published;
retrying from the origin preserves the selected runtime family and wheel hashes.
This installation
work incorporates focused contributions from
[#2600](https://github.com/debpalash/VoiceStudio/pull/2600) and
[#2607](https://github.com/debpalash/VoiceStudio/pull/2607); it does not absorb
their whole draft or mix the separate recipe proposed in
[#2908](https://github.com/debpalash/VoiceStudio/pull/2908).

On an RX 9070 XT (gfx1201), the production Electron runtime installer completed
in an isolated Windows test copy with the complete pinned SDK, including
`rocm-sdk-devel`. Reuse reported both runtime and dependencies ready. Actual
faster-whisper tiny inference used HIP/float16 and produced 19 timed words from
the bundled 5.15-second speech sample under each offline flag independently.
This used an existing Windows host with drivers and prerequisites; it is not
a clean-Windows or packaged GUI certification by itself.

The additional integration in [#3486](https://github.com/debpalash/VoiceStudio/pull/3486)
was also built as an unsigned Windows package. Its actual packaged interface
saved the English keyboard-selected AMD preference, quit, reopened and required
an explicit setup action before reusing the already installed HIP runtime. A
separate configured-profile run verified the real main/preload/native helper,
managed backend, version and ROCm GPU report, then transcribed 19 timed words
and generated 3.456 seconds of nonzero 48 kHz VoxCPM2 audio through the production
local API. Sidecar logs identify CUDA-device execution with bfloat16 under the
verified HIP build; the name `cuda` is PyTorch's API on Radeon too. All 82 owned
processes closed normally. No terminal action was needed for the saved choice.
This used an explicitly prepared pointer to the existing test runtime, not a
fresh NSIS installation. Full Polish keyboard, physical NVDA and listening
acceptance remain open. Ordinary audio preprocessing remains CPU work as in the
CUDA path.

A subsequent genuine beta.3 package at commit `07e8c02da` verified the saved
AMD runtime, staged backend, real native helper and GPU report in the same
configured test profile. The production API generated 3.065 seconds of nonzero
24 kHz OmniVoice audio. Normal shutdown left zero owned processes and no repair
IPC handler errors; the earlier package exposed those errors before the shutdown
fix. This does not certify a fresh install or the later setup-timeout change.

After the test owner accepted the upstream model terms in that isolated profile,
the production default OmniVoice adapter also generated ordinary speech and a
transcript-free reference clone at 24 kHz. All model parameters were on Radeon
with FP16; hooks observed 12 language-model forwards and 25 forwards of the
already cached model-owned PyTorch Whisper reference recognizer, all with GPU
inputs. The generated clips were 2.28 and 2.52 seconds, with nonzero finite audio;
peak allocated GPU memory was 4.04 GB. This reference test used the cached
PyTorch Whisper fallback, not CTranslate2. No external socket/DNS attempt was
observed; a loopback connection was Windows asyncio's own local socketpair.
Only the tokenizer already bundled with the pinned OmniVoice checkpoint was
used. The separate tokenizer registry entry still has unresolved licence
metadata; local test acknowledgement does not reclassify or resolve that audit.
The production licence prompts apply to ordinary users on CUDA and ROCm alike.

For the pinned Torch `2.9.1+rocm7.2.1` Windows build, a non-ASCII
`MIOPEN_CUSTOM_CACHE_DIR` can make MIOpen's SQLite kernel cache fail with
`miopenStatusInternalError`. Native initialization now disables only that disk
cache when this custom path contains Unicode and `MIOPEN_DISABLE_CACHE` was not
explicitly set. ASCII paths, other Torch builds/platforms and explicit overrides
are unchanged. Actual GPU LSTM inference passed with a fresh Polish/space path
after automatic initialization. Skipping the disk cache can require recompiling
kernels in later processes; it does not move model inference to CPU.

Default MIOpen cache locations are not certified by this workaround. A separate
complete SDK installation under a Polish Unicode path passed model-free GPU
readiness but failed actual VAD kernel compilation: HIPRTC could not find the
rocRAND header. The same interpreter completed GPU VAD when pointed at the
existing ASCII SDK header root. Unicode SDK paths therefore remain a known
limitation, distinct from the disk-cache workaround. These roots remain outside
the registered WhisperX profile; speaker diarization remains gated:
direct cached wav2vec2 library alignment completed with 18 timed words and an
observed GPU forward, but does not certify the gated application pipeline.
Clean Windows, packaged GUI execution and upstream CI remain separate gates.

The managed CosyVoice install also has a separate CP312/Torch2.9.1/SDK7.2.1
variant after explicit ROCm selection. Start it from **Model Catalogue → Engines →
CosyVoice**; its completion record requires the actual private environment,
reviewed sources, a native HIP kernel and local prompt decoding. Old CPU markers
cannot advertise HIP readiness. Its upstream I/O namespaces use soundfile for
local WAV/FLAC/MP3 references, without changing shared TorchAudio or requiring
TorchCodec DLLs. LLM, flow and HiFT parameters are checked against the selected
device; ONNX helpers remain on CPU. Source/custom installs and CPU/CUDA/other
platform recipes are unchanged. Device changes allow the normal three-second
graceful shutdown before bounded forced cleanup; a live owner blocks replacement.
Private environment checks read each distribution's name once per check, while
still rejecting duplicate package names and mismatched versions. They do not
cache admission across calls. A changed CosyVoice qualification fingerprint
requires running Install again to produce a fresh native verification; existing
model weights are reused.
Real speech and packaged hardware acceptance
remain separate from this model-free setup check. See the
[CosyVoice guide](../engines/cosyvoice.md#managed-native-windows-amd-variant).

A healthy existing managed CosyVoice CPU installation stays available as a CPU
fallback when the main runtime switches to Auto or ROCm. It is not invalidated or
reinstalled by that change; only an explicit reinstall replaces its environment.

Explicit main-runtime setup and the managed VoxCPM2 and CosyVoice install probes also initialize
the pinned SDK development archive through AMD's own API. The explicit Windows
ROCm VoxCPM2 setup probe has a ten-minute limit for first-time SDK expansion,
native imports and a verified GPU kernel; ordinary import probes retain their
two-minute limit. A timeout still fails setup without publishing readiness.
Native initialization
then discovers the expanded rocRAND headers and sets the process-local
`ROCM_PATH` before MIOpen imports, preserving an explicit override. Read-only
verification does not unpack SDK files. Both GPU VAD parameters and actual
forward inputs were verified on the RX 9070 XT with fresh ASCII and Polish/space
cache paths after this automatic preparation. The SDK installation root was
ASCII. Only the registered WhisperX ASR/alignment profile above now qualifies;
unsupported roots and pyannote speaker diarization remain gated.

Setup repairs only confirmed missing, mismatched or damaged packages, including
native audio, vision and CTranslate2 imports with missing or corrupted wheel files.
An import exception alone can indicate a missing external prerequisite (such as
the VC runtime) and does not authorize package repair. GPU support, driver/kernel failures,
timeouts, crashes and unclassified failures stop verification without downloading
or reinstalling the overlay. CLI diagnostics include a bounded failure category;
correct the reported host problem before retrying. A successful package repair
must still pass the complete model-free GPU verification.

## Offline watermark checkpoints

With `HF_HUB_OFFLINE=1` or `TRANSFORMERS_OFFLINE=1`, AudioSeal loading first
checks the local checkpoint selected by the installed model card. Prefetch uses
the loader's actual cache filename; a cached detector does not make the generator
ready. A missing checkpoint skips the downloading constructor and follows the
existing logged fallback, so generated audio may lack an invisible watermark.
AudioSeal uses its original CPU constructor defaults. These cache rules also
apply to the other supported platforms.

## Maintainer integration

The integration branch preserves the newer native executable compute profiles,
including whisper.cpp, alongside dynamically verified HIP faster-whisper and
managed VoxCPM2. Missing GPU attestation still cannot advertise acceleration.
Setup download notices derive Windows Torch/SDK hosts from the checked-in
recipe; a Linux ROCm index override does not change them. The uv cache observer
does not track the separate CTranslate2 archive transfer.

Review the native recipe/runtime, sidecar receipt/device contract and ordinary
media fixes as separate units. Keep current security floors and existing CPU or
user-managed sidecars. Run the SDK, ASR compute-profile, VoxCPM2, sidecar and
runtime/setup regression groups plus the maintained CI matrix before landing.
The additional change notes are `changelog.d/3486.added.md` and
`changelog.d/3486-b.fixed.md`, following the fragment workflow.

The accepted base implementation is upstream PR #3045. Review the additional
changes in these units; do not replace the upstream hash checks, dependency
constraints, trusted package discovery or failed-probe memoization.

| Additional unit                 | Source boundary                                                                                                 | Focused validation                                                                                                                        |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Desktop runtime choice          | Electron runtime, supervisor, IPC and renderer; explicit installation and next-start persistence                | `runtime-*.test.ts`, `backend*.test.ts`, `ipc*.test.ts`, runtime selector and Performance component tests; all locale checks              |
| Verified HIP VoxCPM2            | `windows_rocm/voxcpm2.py`, managed sidecar installation and VoxCPM2 adapter                                     | `test_voxcpm2_windows_rocm.py`, `test_windows_rocm_voxcpm2_hashes.py`, sidecar installation and marker tests; cached real GPU synthesis   |
| SDK/header preparation          | `windows_rocm/runtime.py` and explicit setup/reuse                                                              | SDK/header, MIOpen-cache and strict readiness tests; actual VAD on supported hardware                                                     |
| Qualified WhisperX GPU path     | Named capabilities in `windows_rocm/pipelines.py`, read-only path checks and the ASR runtime compatibility hook | `test_windows_rocm_whisperx_profile.py`, routing/offline controls and natural default-large VAD/CT2/alignment with warm-profile rejection |
| Optional alignment offline      | ASR backend asset checks and failure/retry cache                                                                | Offline alignment tests plus registered Windows HIP capability checks before warm-cache reuse                                             |
| Portable execution and recovery | Media helpers, atomic markers, backup handling and native fixture isolation                                     | The corresponding media, sidecar, backup and fixture regression groups, then normal backend/frontend CI                                   |

Keep source tests with the code and repeat the normal CI matrix after integration.
Include `test_windows_rocm_sidecar_metadata.py` for per-call package indexing
without admission caching, and `test_watermark_offline_checkpoints.py` plus the
prefetch lifecycle tests for exact cached-card selection and offline misses.
Hardware evidence above names the tested GPU and its limits; it does not replace
Linux/macOS/NVIDIA checks or prove a fresh-Windows installation. A saved GUI
preference alone also does not establish that the intended runtime was installed.

When changing native recipe bytes, package versions or the qualified GPU matrix,
renew the WhisperX profile registration only after the corresponding hardware
checks. Keep availability, execution before warm reuse and compute reporting on
the same qualifier; do not admit speaker diarization through the ASR capability.

## Maintainer integration checks

When reconciling this path with newer runtime code, retain both the Windows HIP
checks and the default runtime's native-failure diagnostics. Initialize DLL search
and import Torch before dependent HIP application modules. Keep the Python 3.12
ABI check, bounded fail-closed HIP import probe and complete `--verify-only`
validation before publishing runtime readiness. A missing system DLL must remain
a prerequisite failure rather than trigger another multi-gigabyte wheel install.

Backend startup prepares audio compatibility inside the guarded audio import
phase. Preparation or TorchAudio import failures retain degraded startup and
sanitized logging; they do not certify HIP readiness or enable gated pipelines.
The runtime integration, broken-audio startup, Windows ROCm heavy-gate and
offline-alignment regressions cover these separate boundaries. Run the actual
current-main backend/frontend CI as well as these focused tests before landing.
