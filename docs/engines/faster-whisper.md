# VoiceStudio — Faster-Whisper Engine

Faster-Whisper runs Whisper on CTranslate2 — the same transcription core
WhisperX uses, **without** the wav2vec2 forced-alignment pass. It's the safe
cross-platform fallback when whisperx isn't installed, and the capture/dictation
fallback on non-Apple machines.

## Selecting it

- **Model Catalogue**, ASR tab → **Use** on the Faster-Whisper row, or
- pin it with `OMNIVOICE_ASR_BACKEND=faster-whisper`.

Auto-detect only picks it when [whisperx](whisperx.md) is unavailable.

## Best at

- **Subtitles, dictation buffers, and batch transcription** where Whisper's
  native word timing (±100–300 ms) is good enough.
- For dubbing lip-sync, prefer [whisperx](whisperx.md) (or
  [mlx-whisper](mlx-whisper.md) on Apple Silicon) — their forced alignment is
  an order of magnitude tighter on word boundaries.

## Platform support

- **CUDA** — float16, with automatic degradation (below).
- **CPU** — int8 on macOS, Windows, and Linux.
- **Apple Silicon GPU / ROCm** — not supported: CTranslate2 has no Metal or
  HIP build, so those hosts run on CPU
  ([#1529](https://github.com/debpalash/VoiceStudio/issues/1529)); auto-detect
  routes them to mlx-whisper / pytorch-whisper instead.

## Model selection

`ASR_MODEL_FASTER` — default `Systran/faster-whisper-large-v3`. Accepts the
size aliases (`tiny` … `large-v3`, `distil-large-v3`) or any CTranslate2
Whisper repo on HF. Weights download on first load — see
[downloading-models](../downloading-models.md).

Segments are cleaned up by faster-whisper's built-in Silero VAD before
transcription.

## Degradation chains

- GPUs without efficient fp16 (older Maxwell/Pascal, GTX 16xx, or a
  CTranslate2/cuDNN mismatch) fail at model construction with a compute-type
  error; the engine walks float16 → int8_float16 → int8 instead of failing
  every chunk ([#551](https://github.com/debpalash/VoiceStudio/issues/551)).
- A CUDA out-of-memory falls back to CPU (slower, same model and accuracy) —
  flushing the resident TTS model frees VRAM for GPU-speed ASR
  ([#255](https://github.com/debpalash/VoiceStudio/issues/255)).

## Quirks

- **cuDNN 8 required on CUDA** — a missing cuDNN 8 would fast-fail the whole
  process, so the engine checks up front and reports itself unavailable
  instead ([#1371](https://github.com/debpalash/VoiceStudio/issues/1371)).
  pytorch-whisper covers that case on torch's bundled cuDNN 9.
- **Kernel refuses executable stacks.** CTranslate2 4.4.0 and older ship
  libraries that request an executable stack, which some Linux kernels reject
  at load time (whisperx pins one of these versions on Python 3.11). VoiceStudio
  clears that flag in the library file automatically the first time it probes
  the engine. If the file is read-only, the engine is reported unavailable
  along with the `patchelf --clear-execstack <library>` command to run once
  ([#692](https://github.com/debpalash/VoiceStudio/issues/692)).
- CTranslate2's GPU teardown can rarely segfault the process at unload. If
  you hit that, switch to the crash-isolated variant —
  [faster-whisper-isolated](faster-whisper-isolated.md)
  ([#730](https://github.com/debpalash/VoiceStudio/issues/730)).
- Transcribes are time-bounded: `OMNIVOICE_TRANSCRIBE_CHUNK_TIMEOUT_S`
  (default 120 s per dub chunk) and `OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S`
  (default 300 s whole-file).

Speed comparisons across engines live in [performance](../performance.md).
