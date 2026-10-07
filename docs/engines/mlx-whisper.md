# VoiceStudio — MLX Whisper Engine

MLX Whisper runs Whisper on the Apple Silicon GPU via MLX. It exists because
CTranslate2 (whisperx / faster-whisper) has **no Metal build** — on a Mac
those engines transcribe on the CPU no matter what GPU is present. Measured
on an M2 with whisper-large-v3, one 30 s dub chunk: **90.4 s on WhisperX
(CPU) vs 20.5 s on MLX (GPU)** — which is why auto-detect picks MLX Whisper
on every Apple Silicon machine
([#1127](https://github.com/debpalash/VoiceStudio/issues/1127)).

## Selecting it

- Nothing to do on Apple Silicon — auto-detect prefers it there.
- Or explicitly: **Model Catalogue**, ASR tab → **Use**, or
  `OMNIVOICE_ASR_BACKEND=mlx-whisper`.

## Best at

- **Dubbing on a Mac** — it layers the same wav2vec2 forced alignment
  WhisperX uses on top of the GPU transcription, so word timing (±10–30 ms)
  and therefore lip-sync accuracy are unchanged. Same model, same alignment,
  ~4x the speed.
- **Dictation/capture** — the capture path automatically swaps in
  `mlx-community/whisper-large-v3-turbo` (~5x faster than large-v3) unless a
  sherpa dictation model or [parakeet-mlx](parakeet-mlx.md) is preferred.

## Platform support

**Apple Silicon only.** A shared platform gate refuses Linux, Windows, and
Intel Macs before any package import, so a stray `mlx-whisper` wheel on the
wrong platform never reports itself available
([#390](https://github.com/debpalash/VoiceStudio/issues/390)). All other
platforms use the CUDA/CPU engines instead.

## Model selection

- `ASR_MODEL` — default `mlx-community/whisper-large-v3-mlx`. Any MLX-format
  Whisper repo works. Weights download on first load — see
  [downloading-models](../downloading-models.md).
- `OMNIVOICE_ALIGN_DEVICE` — force the wav2vec2 aligner's device. The aligner
  runs on MPS when it can and falls back to CPU; languages without a bundled
  aligner (~20 major languages have one) keep Whisper's native word
  timestamps. Loaded aligners are released when the engine unloads and after
  the idle timeout (`OMNIVOICE_IDLE_TIMEOUT_S`).

## Quirks

- Audio is decoded through VoiceStudio's validated ffmpeg rather than the
  bare `ffmpeg` PATH lookup mlx-whisper would do on its own — a clean
  from-source install with no system ffmpeg works fine
  ([#479](https://github.com/debpalash/VoiceStudio/issues/479)).
- The model is warmed into unified memory in the background, so the first
  transcribe after startup doesn't pay the load cost.
- In a packaged app, a native MLX library that fails to load is reported as
  "unavailable" (with fallback to another engine) rather than crashing the
  engine list.

Speed comparisons across engines live in [performance](../performance.md).
