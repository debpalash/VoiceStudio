# VoiceStudio: CosyVoice Engine

> Native Windows ROCm changes on this branch are **DRAFT / not fully validated**.
> Earlier hardware checks do not certify this selective integration; full
> WhisperX remains unavailable. See the [draft scope and path limitations](../install/windows-rocm.md).

CosyVoice is an optional multilingual TTS backend for zero-shot voice cloning
and instructed speech. A one-click install runs it in its own
environment and process; an existing source installation keeps
running in-process.

## Downloaded weights and an available engine are different states

The Model Catalogue tracks model weights separately from engine runtime
availability. A downloaded
`FunAudioLLM/Fun-CosyVoice3-0.5B-2512` cache means the model files reached the
machine. It does not prove that the VoiceStudio backend can import and run
CosyVoice.

For an existing source installation, the same Python interpreter that runs
the VoiceStudio backend must import:

```python
from cosyvoice.cli.cosyvoice import AutoModel
```

It then loads the directory named by `OMNIVOICE_COSYVOICE_MODEL`, or defaults
to `pretrained_models/Fun-CosyVoice3-0.5B`. That path must resolve to the usable
model directory, not only the parent Hugging Face cache directory.

## One-click install

Click **Install** in **Model Catalogue → Engines → CosyVoice**. VoiceStudio
gives CosyVoice its own folder under the data directory and its own Python
3.10 environment (Python 3.12 on native Windows AMD ROCm), and runs it there
in a separate process. The install:

- clones a reviewed CosyVoice commit and the Matcha-TTS code it depends on;
- downloads the CosyVoice 3 weights (about 5.4 GB).

The completion marker is published atomically after the required checks,
including the speech probe on native Windows ROCm. An interrupted marker write
does not masquerade as a completed legacy CPU install. Retry Install after a
write failure; existing model weights remain reusable.

It differs from upstream's own setup:

- **PyTorch 2.7.0 on existing platforms.** The CUDA 12.8 build on an NVIDIA
  GPU, the CPU build on other Windows and Linux machines, the regular build
  on Apple Silicon. A fresh Windows AMD ROCm install instead pins AMD's
  PyTorch/torchaudio 2.9.1 + ROCm 7.2.1 and torchvision 0.24.1.
  Upstream's 2.3.1 exists only for CUDA 12.1 and cannot run on RTX 50-series
  GPUs.
- **Leaner dependencies.** No TensorRT, DeepSpeed or GPU onnxruntime. Native
  Windows ROCm uses the reviewed AMD wheel source. Upstream uses extra runtimes for speed on Linux;
  synthesis works without them. PyWORLD requires a C++ compiler: Xcode Command
  Line Tools on macOS, Visual Studio Build Tools with C++ on Windows, or the
  distribution’s C++ build tools on Linux. SoX is not needed.
- **Patched dependencies.** Where upstream pins a release with a published
  security advisory (diffusers, hydra-core, lightning, modelscope, onnx,
  protobuf, transformers), the install uses the fixed release. That set was
  installed on Windows and passes the install's import check.
- **No text normaliser.** Upstream's (wetext) downloads its data from
  ModelScope on every model load, and ModelScope rate-limits those
  downloads, so a normaliser could half-download and fail silently. The
  one-click install leaves it out, and CosyVoice reads text as written:
  spell out numbers, dates and symbols where the pronunciation matters.
- **Only the weights CosyVoice 3 loads.** Not the RL and TensorRT variants
  that share its repository.

Nothing it installs touches VoiceStudio itself or any other engine, and
**Uninstall** in the same row removes only that folder. An existing source
installation keeps working as it is. The button is not offered on Intel
Macs, where PyTorch 2.7.0 has no build.

### Native Windows AMD ROCm

On a detected native Windows ROCm host, a **fresh** one-click install uses a
separate Python 3.12 environment with AMD's ROCm 7.2.1 wheels for PyTorch,
torchaudio and torchvision, plus the pinned imageio-ffmpeg executable. The
CosyVoice 3 LLM, flow and HiFT weights and synthesis run on the Radeon; the
ONNX reference preprocessing remains on CPU, as on the upstream CUDA path.
Use a supported Radeon and AMD's ROCm 7.2.1-compatible Windows driver.

TorchCodec 0.8.1 from PyPI cannot load against AMD's Windows PyTorch build:
its native DLL imports a c10 symbol that the AMD DLL does not export
(WinError 127), even when FFmpeg 7.1 DLLs are present. The ROCm recipe does
not install or use TorchCodec. Instead, the worker reads formats supported
by libsndfile directly and decodes other references through its own pinned
static FFmpeg. The supported upload formats are WAV, MP3, M4A, FLAC, OGG,
OGA, Opus, AAC and WebM. All nine were checked with actual model-weighted
speech on an RX 9070 XT using transient reference transcodes. The existing
model weights were reused without copies; test transcodes were removed.
Invalid media still produces an error.

Before marking a new ROCm install ready, the installer checks its wheel
versions and a real GPU operation, encodes and decodes each of those nine
formats, then loads the downloaded model and synthesizes non-silent speech
with the LLM, flow and HiFT weights verified on the GPU. A failed check
leaves the install incomplete and does not advertise acceleration. This
work was also validated in a clean isolated Python 3.12 environment with the
same managed dependency pins: its own AMD wheels passed the format probe and
generated speech using the existing model without copying weights. The
NSIS-installed app then completed the real same-origin one-click API path:
source checkout, AMD dependencies, model download, on-GPU verification and
24-kHz generation, also with an uploaded MP3 reference. The literal Model
Catalogue Install button has not been clicked in a UI test.

An older complete CPU-managed install stays on CPU on AMD: **Install** does
not overwrite it or re-label it as GPU. To switch an existing managed
installation, explicitly uninstall CosyVoice in Model Catalogue and install
again (the model weights will be downloaded again). User-managed checkouts
are never rewritten. NVIDIA, macOS and Linux recipes remain unchanged.

CosyVoice 3 has no built-in voices. With no reference clip, it speaks in the
voice of upstream's own sample prompt.

## Source builds and existing installations

The upstream CosyVoice project recommends its own Python 3.10 Conda environment
and SoX. The one-click install above gives CosyVoice that separate
environment. Installing upstream dependency pins into VoiceStudio's
shared backend environment can also conflict with other engines.

Existing source installations remain usable when the VoiceStudio backend's
interpreter can already import `cosyvoice.cli.cosyvoice.AutoModel`. Keep a
working installation in place. Before starting VoiceStudio, set
`OMNIVOICE_COSYVOICE_MODEL` to its usable model directory through the source
checkout's environment or project `.env` file.

For upstream setup details, read the
[official CosyVoice installation guide](https://github.com/QwenAudio/CosyVoice#install).
Those steps create a standalone CosyVoice environment. Prefer the managed
one-click installer above for the Electron app on supported CUDA, CPU and
native Windows ROCm hosts.

## Diagnose an unavailable engine

Include these facts in a support question or bug report:

1. The exact VoiceStudio version.
2. The full reason under **Model Catalogue > Engines > CosyVoice** after
   selecting **Re-check**.
3. The CosyVoice lines from **Settings > Logs > Backend** immediately after
   that check.
4. On Windows, the output of `where.exe sox` in PowerShell.

These facts distinguish a downloaded-model state from a missing Python runtime,
missing SoX executable, or incorrect model directory. Do not delete a model
cache or reinstall dependencies until the log identifies which state failed.

The public report that exposed the misleading installed state is
[Discussion 1631](https://github.com/debpalash/VoiceStudio/discussions/1631).


On hosts without CUDA, the managed sidecar normalizes the LLM, flow and vocoder
weights to float32 to match upstream CPU inputs. CUDA keeps its selected
precision. This prevents the CPU Float/BFloat16 matrix mismatch; it does not
establish that every reported installation or speech-quality problem is fixed.

### Repairing missing runtime imports

The managed recipe includes gdown, wget and pyarrow, and builds pinned PyWORLD
source that no longer needs `pkg_resources`. Its final check imports the dataset
processor and Matcha utilities as well as AutoModel. Older completion markers
are treated as needing repair; retry Install to reuse the existing model weights.
User-managed environments are not rewritten. Do not downgrade setuptools merely
to restore `pkg_resources`.

### Speech context with newer Transformers

The managed sidecar loads Qwen in float32 before applying the trained checkpoint,
then preserves upstream's explicit precision choices. It also includes cached
prompt tokens in incremental attention masks: a one-token mask in newer
Transformers otherwise hides the text and voice context and produces unrelated
or garbled speech. Existing Transformers 4 environments and legacy cache tuples
remain supported; full masks are left intact.

A real CosyVoice 3 CPU check with Transformers 5.10.1 now reproduces the complete
89-word English input, verified with an independent local speech recognizer.
A tiny randomly initialized Qwen regression checks cached versus full-context
decoding without downloading any model in CI. This does not certify every
language, voice reference, or GPU configuration.
