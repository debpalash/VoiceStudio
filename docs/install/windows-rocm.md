# Native Windows ROCm — DRAFT desktop integration

[Polska instrukcja](windows-rocm.pl.md) · [Windows installation](windows.md)

**DRAFT / not fully validated.** This desktop/engine integration depends on
PR #2600's [source-only recipe](windows-rocm-source.md). It is not an official
release, a new validated installer, or a claim of full CUDA/ROCm parity.
The official v0.5.6 installer does not contain these desktop changes.

## Conditional desktop setup

For a future build of this branch on Windows 11 x64, use a Radeon and driver
listed in the [AMD ROCm 7.2.1 Windows compatibility matrix](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2.1/docs/compatibility/compatibilityrad/windows/windows_compatibility.html).
Leave the compute backend on **Automatic**, or select **AMD ROCm**, then start
**Install local runtime** explicitly. Manual selection cannot make an unsupported
card compatible. NVIDIA and other unsupported hosts keep their existing paths.
Do not use the official updater expecting it to preserve an unpublished patch.

Setup checks HIP execution and CTranslate2 before writing readiness. It does
not install drivers, WSL, Visual Studio Build Tools or the development SDK.
Interrupted setup and repair remain explicit and must preserve user data.
Older experimental readiness markers require explicit repair after the complete
recipe stamp changes; checking readiness does not rewrite a runtime.
Desktop setup and repair exclude the lockfile's CUDA Torch and NVIDIA runtime
packages before installing the reviewed Windows HIP wheels. They do not need to
download a temporary CUDA stack first; the source-sync commands below are separate.

## Shared recipe and source setup

`scripts/windows-rocm-recipe.json` is shared by source setup and Electron:
Python 3.12, torch/torchaudio 2.9.1+rocm7.2.1, torchvision 0.24.1,
`rocm[libraries]==7.2.1`, and hash-checked CTranslate2 4.8.2 Windows HIP.
Stock PyPI CTranslate2 does not gain HIP support by installing AMD PyTorch.
The [maintainer contract](../maintainers/windows-rocm.md) documents update gates.
Sidecar engines still have separate pins and verification markers.

Source setup changes only that checkout's environment:

```powershell
$env:OMNIVOICE_TORCH_VARIANT = 'rocm'
uv sync --frozen --no-dev --python 3.12
if ($LASTEXITCODE -ne 0) { throw 'Base dependency sync failed' }
uv run --no-sync --python 3.12 python scripts/setup.py
if ($LASTEXITCODE -ne 0) { throw 'Windows ROCm bootstrap failed' }
```

Do not repair a working AMD runtime with ordinary `uv sync`: the common lock
restores default wheels, so reviewed setup must run again after such a sync.
No development SDK dependency or production environment workaround is added.

## Engine scope

- Faster-whisper transcribes through a verified CT2 HIP wheel. Word-timestamp
  transcription requests can use the independent WhisperX aligner; fast
  dictation and translation do not. Unsupported languages or unavailable
  optional offline assets retain native word timestamps. Alignment computation
  errors still fail explicitly. Sentence splitting is accepted only when all
  transcript text is preserved in order and returned words have timestamps.
  This is not the full WhisperX engine.
  Either `HF_HUB_OFFLINE` or `TRANSFORMERS_OFFLINE` set to `1`, `on`, `yes` or
  `true` (case-insensitive) prevents optional alignment downloads. Cached
  aligners still use the selected GPU; this does not introduce CPU inference.
- The source pyannote 3.x adapter restores metadata and explicit soundfile reads
  removed by torchaudio 2.9 without modifying installed dependencies or tensor
  execution. An audio-adapter pass does not prove GPU kernels or diarization.
  Sequential-only codecs such as GSM WAV decode from the beginning without an
  unsupported seek; pyannote can crop file paths through its full-decode fallback.
  Nonzero crops from file-like streams retain pyannote's existing limitation.
- Fresh VoxCPM2 and CosyVoice installs have separate ROCm recipes and GPU checks.
  Complete CPU installations are not silently converted or labelled accelerated.
  Completion markers are written atomically; a partial write cannot report a
  successful install or prevent an explicit retry.
  See the engine guides for migration and remaining limitations.
- Sortformer through audio.cpp uses Vulkan, not HIP. Its readiness badge follows
  the selected backend, not an unrelated catalogue model.
- CPU preprocessing and documented CPU fallback remain possible, as on CUDA.
  Inspect Settings → Performance; non-silent audio alone does not prove GPU use.

## Known native path blocker

**Full WhisperX remains unavailable in production.** Developer-reported offline
checks initialized the official development SDK in a Unicode/spaced environment.
The same staged dependencies through ASCII aliases, including one **with spaces**,
passed default-MIOpen VAD + tiny ASR + English alignment on GPU: 17/17 timed words,
normal exit, kernel cache disabled, only `ROCM_PATH`, no HIPRTC append option and
no global SDK modules. The spaced ASCII alias completed in 11.45 seconds.

Non-ASCII paths, including Polish-only names, remain unsupported by these
native diagnostics. An all-Unicode setup failed at `instance_norm` with a native
MIOpen temporary-path character-conversion error. A Unicode `ROCM_PATH` with
ASCII interpreter/temp paths passed `instance_norm` but failed at LSTM with a
missing rocRAND-header error despite the header existing. Failed runs could
hang on shutdown and required their own process timeout. Python UTF-8 mode and
`locale.LC_ALL=.UTF8` did not fix the failure. ASCII paths with spaces passed;
this is not a generic whitespace failure or a production-ready workaround.
Full gated speaker diarization remains unverified.

For a model-free diagnostic, run the evaluated Python with
`scripts/smoke_windows_rocm.py --miopen`. This explicitly checks VAD-shaped
InstanceNorm/LSTM kernels, not a full engine. It reproduces the failing Polish
SDK path as a bounded worker timeout; ASCII and ASCII-with-spaces paths passed.
It does not install headers or enable WhisperX. See the
[maintainer diagnostic](../maintainers/windows-rocm.md#model-free-miopen-diagnostic).

## Evidence and review limits

Earlier RX 9070 XT checks exercised HIP transcription/synthesis and basic
installed-app dubbing with Vulkan Sortformer. They describe a broader historical
local build, not this exact selective draft. Independent capture-language,
worker and cached-WAV fixes are not included here. No final installer was built
for this transfer.

Clean-Windows provisioning, robust non-ASCII paths, a new installed artifact,
actual macOS/Linux/NVIDIA regressions, long workloads, translation quality and
NVDA speech still need validation. NLLB omitted content in an earlier sample on
both CPU and HIP; a completed WAV does not establish translation fidelity.

Adoption also requires the CLA, fork CI/Security approval and maintainer approval
of download endpoints. Alignment can acquire HF/TorchAudio checkpoints and NLTK
assets on a transcription request; extra endpoint/consent policy remains for
review without disabling the working timestamp implementation. The compute
selector now uses the shared keyboard-capable control; its compatibility warning
remains visible. This has automated keyboard coverage, not a real NVDA pass.
Existing upstream profile
storage is preserved; this draft does not put all data beside the executable.
