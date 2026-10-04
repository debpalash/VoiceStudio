# VoiceStudio — WhisperX Engine

> Native Windows ROCm changes on this branch are **DRAFT / not fully validated**.
> Earlier hardware checks do not certify this selective integration; full
> WhisperX remains unavailable. See the [draft scope and path limitations](../install/windows-rocm.md).

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
| AMD ROCm on Windows | The full WhisperX engine is **unavailable**: its ASR import via pyannote fails on this torchaudio stack. Auto-detect selects [faster-whisper](faster-whisper.md) with verified HIP CTranslate2 and invokes the independent alignment module on word-timestamp requests for languages with a built-in aligner. English, Polish and French were tested on GPU; other languages have not been verified end to end. Without a verified HIP wheel, auto-detect prefers [pytorch-whisper](pytorch-whisper.md) for HIP ([#1529](https://github.com/debpalash/VoiceStudio/issues/1529)). Ordinary PyPI CT2 CUDA wheels are **not** HIP-compatible. Explicit WhisperX selection reports unavailable. |
| AMD ROCm on Linux | The CTranslate2 HIP wheel check is Windows-only; auto-detect prefers [pytorch-whisper](pytorch-whisper.md) for GPU transcription. |

## Model selection

- `ASR_MODEL_WHISPERX` — default `large-v3`. Accepts the usual size aliases
  (`tiny` … `large-v3`, `distil-large-v3`) or a full HF repo id. Weights
  download on first load — see [downloading-models](../downloading-models.md).
- `OMNIVOICE_ALIGN_DEVICE` — force the wav2vec2 aligner's device. Aligners
  exist for ~20 major languages; other languages keep Whisper's native word
  timestamps instead of failing. On ROCm, forced alignment is a separate
  torch/torchaudio operation and is not verified by CTranslate2 GPU detection.

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
- **Windows ROCm WhisperX engine is not yet validated.** CTranslate2 HIP
  successfully transcribes with faster-whisper and native word timestamps.
  The WhisperX wav2vec2 **alignment module** separately aligned all 22 English
  words of the demo WAV on the RX 9070 XT after explicitly caching its English
  checkpoint and NLTK data for the test. This does not prove
  the full WhisperX ASR-and-alignment pipeline. A separately cached Polish
  checkpoint also aligned 8/8 words in a short sample on the Radeon GPU;
  a French recording aligned 12/12 words and passed a fully offline rerun.
  WhisperX 3.7.4 requires pyannote.audio below 4 (3.4.0 is the newest allowed)
  and torchaudio 2.8, while the tested ROCm runtime has torchaudio 2.9.1.
  Its lazy ASR import fails because pyannote.audio references the removed
  `torchaudio.AudioMetaData`.
  Auto-detect excludes the full WhisperX engine on Windows ROCm and surfaces
  the import error for explicit selection. Faster-whisper's independent
  supported-language alignment path loads on transcription requests only.
  English, Polish and French samples were validated, but short Polish
  transcription was imperfect and other languages and models remain untested.
  Do not interpret a passing HIP CT2 probe as proof of full WhisperX support.
- On Linux kernels that refuse an executable stack, CTranslate2's native
  library (4.4.0 and older — what whisperx 3.4.5 pins on Python 3.11) is
  rejected with "cannot enable executable stack". VoiceStudio now clears that
  one ELF flag in place on first probe and the engine loads normally; if the
  library cannot be written (a read-only bundle), the engine reports itself
  unavailable with the repair command instead of crashing
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
