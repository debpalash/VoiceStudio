# VoiceStudio: CosyVoice Engine

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
3.10 environment, and runs it there in a separate process. Explicit native
Windows ROCm setup uses the qualified Python 3.12 variant below. The install:

- clones a reviewed CosyVoice commit and the Matcha-TTS code it depends on;
- downloads the CosyVoice 3 weights (about 5.4 GB).

It differs from upstream's own setup:

- **PyTorch 2.7.0 in the default recipes.** The CUDA 12.8 build on an NVIDIA GPU, the CPU build on
  other Windows and Linux machines, the regular build on Apple Silicon.
  Upstream's 2.3.1 exists only for CUDA 12.1 and cannot run on RTX 50-series
  GPUs. On Linux ARM64 (for example DGX Spark) only torch takes that build
  and torchaudio installs the plain 2.7.0 wheel, because PyTorch publishes
  its ARM64 torchaudio 2.7.0 without a `+cu128` or `+cpu` label.
- **Leaner dependencies.** No TensorRT, DeepSpeed or GPU onnxruntime, and no
  third-party package index. Upstream uses them for extra speed on Linux;
  synthesis works without them. PyWORLD requires a C++ compiler: Xcode Command
  Line Tools on macOS, Visual Studio Build Tools with C++ on Windows, or the
  distribution’s C++ build tools on Linux. Without one, the install stops
  with "no C++ compiler found" and names the tools for your system; install
  them and click **Install** again to resume. SoX is not needed.
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

CosyVoice 3 has no built-in voices. With no reference clip, it speaks in the
voice of upstream's own sample prompt.

## Managed native Windows AMD variant

After explicitly selecting the supported Windows ROCm runtime, click **Install**
on the CosyVoice engine row. This uses its own Python 3.12 environment with the
hash-pinned Torch/audio 2.9.1 and SDK 7.2.1 recipe. Its remaining inference pins
retain the reviewed security fixes; PyWORLD still needs the existing C++ build
tools. CPU, CUDA, Linux, macOS and custom source installations retain their
existing recipes. An explicit CPU compute choice stays on CPU.

The managed setup verifies the actual private interpreter, package versions,
reviewed source revisions, a native GPU kernel and the bundled local prompt
before atomically publishing the HIP completion record. A legacy CPU completion
record does not establish HIP readiness. Changing source, packages or interpreter
invalidates that record. SDK selectors inherited from another installation are
removed only from this verified managed child's environment.

A healthy existing Python 3.10/Torch2.7 CPU sidecar remains installed and usable
as a CPU fallback after changing the main runtime choice. That choice alone
does not invalidate or convert it; an explicit reinstall is required to replace
its environment. Custom/source installations retain their own contract.

The pinned upstream reference and dataset loaders use a local soundfile adapter
inside their own module namespaces, preserving shared TorchAudio. This avoids
requiring TorchCodec's FFmpeg DLLs for WAV/FLAC/MP3 prompts. LLM, flow and HiFT
parameters must match the selected device; the ONNX speaker/tokenizer helpers
remain on CPU. Device changes allow the normal three-second graceful shutdown
before bounded forced cleanup; a retained live owner blocks replacement.
A model-free setup result is separate from real speech generation
and does not certify other cards, operating systems or gated pipelines.

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
one-click installer above for the Electron app.

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
