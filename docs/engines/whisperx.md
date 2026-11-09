# VoiceStudio — WhisperX Engine

WhisperX is the default ASR engine on CUDA and plain-CPU hosts: faster-whisper
(CTranslate2) transcription plus a **wav2vec2 forced-alignment** pass that
snaps word boundaries to ±10–30 ms (Whisper's own timestamps are ±100–300 ms).
That word timing is what dubbing lip-sync depends on, which is why auto-detect
prefers it wherever CTranslate2 can use the GPU.

## Selecting it

- **Model Catalogue**, ASR tab → **Use** on the WhisperX row, or
- pin it with `OMNIVOICE_ASR_BACKEND=whisperx` (the env var always wins over
  the Settings pick; with neither set, auto-detect chooses per-hardware).

## Best at

- **Dubbing** — the forced alignment is the accuracy tier lip-sync needs.
- **Batch transcription** with word-level subtitles.
- Multi-speaker work: it pairs with pyannote speaker diarization — see
  [diarization](../features/diarization.md).

## Platform support

| Host | What happens |
| --- | --- |
| NVIDIA CUDA | GPU, float16 (degrades automatically, see below) |
| CPU (any OS) | int8 — works, but slow for large-v3 |
| Apple Silicon | CPU only — CTranslate2 has no Metal build, so auto-detect prefers [mlx-whisper](mlx-whisper.md) there ([#1127](https://github.com/debpalash/VoiceStudio/issues/1127)) |
| AMD ROCm | CPU only — CTranslate2 has no HIP build, so auto-detect prefers [pytorch-whisper](pytorch-whisper.md) there ([#1529](https://github.com/debpalash/VoiceStudio/issues/1529)) |

## Model selection

- `ASR_MODEL_WHISPERX` — default `large-v3`. Accepts the usual size aliases
  (`tiny` … `large-v3`, `distil-large-v3`) or a full HF repo id. Weights
  download on first load — see [downloading-models](../downloading-models.md).
- `OMNIVOICE_ALIGN_DEVICE` — force the wav2vec2 aligner's device. Aligners
  exist for ~20 major languages; other languages keep Whisper's native word
  timestamps instead of failing.

## VRAM preflight and degradation

Loading fp16 large-v3 onto a nearly-full 8 GB card dies as a *native* CUDA
abort — no Python exception, the whole backend goes down
([#723](https://github.com/debpalash/VoiceStudio/issues/723)). So before every
load the engine checks free VRAM against per-compute-type budgets
(float16 5.0 GB, int8_float16 3.5 GB, int8 3.0 GB, scaled down for smaller
models) and degrades the compute type — or falls to CPU int8 — instead of
starting a load that would kill the process. Disable with
`OMNIVOICE_ASR_VRAM_PREFLIGHT=0`.

Two more fallback chains run at load time:

- GPUs without efficient fp16 (older Maxwell/Pascal, GTX 16xx) raise a
  compute-type error — the engine retries int8_float16, then int8
  ([#551](https://github.com/debpalash/VoiceStudio/issues/551)).
- A genuine CUDA OOM retries on CPU int8, so dubbing still completes
  (slower, same model and accuracy).

## Quirks

- **cuDNN 8 required on CUDA.** CTranslate2 links cuDNN 8; if it's missing the
  process fast-fails with no traceback, so the engine is reported unavailable
  up front and selection falls through to pytorch-whisper, which uses torch's
  own cuDNN 9 ([#1371](https://github.com/debpalash/VoiceStudio/issues/1371)).
- **Kernel refuses executable stacks.** CTranslate2 4.4.0 and older ship
  libraries that request an executable stack, which some Linux kernels reject
  at load time (whisperx pins one of these versions on Python 3.11). VoiceStudio
  clears that flag in the library file automatically the first time it probes
  the engine. If the file is read-only, the engine is reported unavailable
  along with the `patchelf --clear-execstack <library>` command to run once
  ([#692](https://github.com/debpalash/VoiceStudio/issues/692)).
- A partially-installed environment (interrupted sync, antivirus quarantine)
  can break WhisperX's deep import chain (whisperx → pyannote →
  lightning_fabric). The engine is then reported unavailable with a repair
  hint — reinstall, or `uv sync --reinstall` on a source checkout
  ([#1185](https://github.com/debpalash/VoiceStudio/issues/1185)).
- Audio is decoded through VoiceStudio's validated ffmpeg, not a bare `ffmpeg`
  PATH lookup ([#479](https://github.com/debpalash/VoiceStudio/issues/479)).
- Transcribes are time-bounded: each dub chunk by
  `OMNIVOICE_TRANSCRIBE_CHUNK_TIMEOUT_S` (default 120 s), whole files by
  `OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S` (default 300 s). Raise them for very
  long files on slow hardware.

Speed comparisons across engines live in [performance](../performance.md).
