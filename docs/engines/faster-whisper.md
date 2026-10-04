# VoiceStudio — Faster-Whisper Engine

> Native Windows ROCm changes on this branch are **DRAFT / not fully validated**.
> Earlier hardware checks do not certify this selective integration; full
> WhisperX remains unavailable. See the [draft scope and path limitations](../install/windows-rocm.md).

Faster-Whisper runs Whisper on CTranslate2 — the same transcription core
WhisperX uses, **without** the wav2vec2 forced-alignment pass by default. It's the safe
cross-platform fallback when whisperx isn't installed, and the capture/dictation
fallback on non-Apple machines.

## Selecting it

- **Model Catalogue**, ASR tab → **Use** on the Faster-Whisper row, or
- pin it with `OMNIVOICE_ASR_BACKEND=faster-whisper`.

Auto-detect only picks it when [whisperx](whisperx.md) is unavailable.

## Best at

- **Subtitles, dictation buffers, and batch transcription** where Whisper's
  native word timing (±100–300 ms) is good enough.
- For dubbing lip-sync, prefer [whisperx](whisperx.md) where available,
  [mlx-whisper](mlx-whisper.md) on Apple Silicon, or the Windows ROCm
  supported-language alignment path below; their forced alignment tightens word boundaries.

## Platform support

- **CUDA** — float16, with automatic degradation (below).
- **CPU** — int8 on macOS, Windows, and Linux.
- **Apple Silicon GPU** — CTranslate2 has no Metal backend; auto-detect prefers mlx-whisper.
- **Windows ROCm** — a verified CTranslate2 HIP wheel transcribes on AMD GPU
  using the `cuda` device string; ordinary CUDA wheels cannot. Without a HIP
  wheel, auto-detect prefers pytorch-whisper ([#1529](https://github.com/debpalash/VoiceStudio/issues/1529)).
- **Linux ROCm** — the HIP wheel gate is Windows-only; auto-detect prefers
  pytorch-whisper for GPU transcription.

## Model selection

`ASR_MODEL_FASTER` — default `Systran/faster-whisper-large-v3`. Accepts the
size aliases (`tiny` … `large-v3`, `distil-large-v3`) or any CTranslate2
Whisper repo on HF. Weights download on first load — see
[downloading-models](../downloading-models.md).

Segments are cleaned up by faster-whisper's built-in Silero VAD before
transcription. This VAD model uses ONNX Runtime's `CPUExecutionProvider` on
both NVIDIA and AMD, even when CTranslate2 transcribes on a GPU. GPU transcription is therefore
not a GPU-only inference pipeline. The engine can also select CPU if its GPU
probe fails or retry on CPU after a GPU out-of-memory error; no strict
GPU-only mode is currently available.

## Windows ROCm alignment

On Windows ROCm with a verified CTranslate2 HIP wheel, requesting transcription
**with word timestamps** loads WhisperX's separate wav2vec2 aligner when one
exists for the detected language. Fast dictation without word timestamps,
translation, and languages without a built-in aligner retain faster-whisper's
native timing. CUDA, Linux, and ordinary CPU hosts keep their existing paths. The
hybrid avoids WhisperX's broken pyannote ASR import; it uses faster-whisper's
Silero VAD rather than WhisperX's full ASR/diarization pipeline.

The first word-timestamp transcription in a supported language loads its
checkpoint (for example, English: torchaudio's ~378 MB
`WAV2VEC2_ASR_BASE_960H`; Polish: WhisperX's
`jonatasgrosman/wav2vec2-large-xlsr-53-polish` snapshot) and NLTK `punkt_tab`
if missing. Downloads use persistent Torch/Hugging Face caches and the app's
`nltk_data` directory; engine discovery/startup never downloads alignment
assets. No terminal, environment opt-in, or separate manual caching is needed.
Offline with complete caches works. If an optional alignment checkpoint or
NLTK data is absent offline, or its download fails, transcription returns
faster-whisper's native word timestamps instead. The ASR model itself must
still be cached, and alignment computation errors still fail explicitly.
Both `HF_HUB_OFFLINE` and `TRANSFORMERS_OFFLINE` disable optional Torch, Hugging
Face and NLTK alignment downloads: `1`, `on`, `yes` and `true` are accepted
case-insensitively. Complete cached aligners still run on the selected GPU;
missing optional assets retain native ASR timing without moving inference to CPU.
The aligner may split a transcript segment into multiple sentences. This is
accepted when the transcript text is preserved in order (ignoring whitespace)
and every returned word has timestamps; dropped or changed text is rejected.
Large first-time downloads may exceed the transcription timeout on slow links;
retry after caching finishes. The Polish download requests only PyTorch model
files, not the unrelated Flax or language-model variants in the same HF repo.
On an RX 9070 XT, English aligned 22/22 demo words, Polish aligned 8/8,
and French aligned 12/12 from a real recording; the French sample also passed
fully offline. Other supported aligner languages are wired to WhisperX's
model list but have not received equivalent real-GPU tests. A packaged Electron build
also returned an English transcription through the GUI's same-origin
`/api/transcribe` route using the cached tiny model. Other ASR models,
languages and full GUI workflows are not yet validated end to end.
The full [WhisperX engine](whisperx.md) remains unavailable on this stack.

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
- On Linux kernels that refuse an executable stack, the CTranslate2 native
  library (4.4.0 and older) is rejected with "cannot enable executable stack"
  (an OSError, not an ImportError). VoiceStudio clears that ELF flag in place
  on first probe so the engine loads; if the file cannot be written it reports
  itself unavailable with the repair command rather than crashing engine
  selection ([#692](https://github.com/debpalash/VoiceStudio/issues/692)).
- CTranslate2's GPU teardown can rarely segfault the process at unload. If
  you hit that, switch to the crash-isolated variant —
  [faster-whisper-isolated](faster-whisper-isolated.md)
  ([#730](https://github.com/debpalash/VoiceStudio/issues/730)).
- Transcribes are time-bounded: `OMNIVOICE_TRANSCRIBE_CHUNK_TIMEOUT_S`
  (default 120 s per dub chunk) and `OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S`
  (default 300 s whole-file).

Speed comparisons across engines live in [performance](../performance.md).
