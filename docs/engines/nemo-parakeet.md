# VoiceStudio — Parakeet TDT (NVIDIA NeMo) Engine

NVIDIA's Parakeet TDT via the NeMo toolkit: a FastConformer encoder with a
Token-and-Duration Transducer decoder. It beats Whisper large-v3 on English
benchmarks (~6% WER) and supports **25 (mostly European) languages** with
automatic language detection. The 0.6B model is fast even on CPU — measured
RTF 0.08–0.23 on an Apple Silicon M2 CPU (2026-07-02), ~20x faster than
faster-whisper large-v3 int8 on the same host.

## Do not install NeMo into the app venv

`nemo_toolkit`'s ASR extras pin `transformers>=4.57,<4.58`, which conflicts
with VoiceStudio's own `transformers>=5.3` requirement and **will break the
backend** (ImportError on startup) if installed into the shared venv. There
is currently no safe in-app install path for this engine; in-app isolation
is tracked separately.

If you want the Parakeet models without a separate environment, use these
instead — same model family, no NeMo dependency:

- **Apple Silicon:** [parakeet-mlx](parakeet-mlx.md) (installed by default on
  mac-ARM source installs).
- **Any platform, CPU:** [sherpa-onnx-asr](sherpa-onnx-asr.md) — selectable
  int8 ONNX exports of Parakeet TDT v2/v3; Whisper Tiny remains the
  cross-platform dictation default.

## Finding setup help

Open **Settings → Models → ASR** and expand **Parakeet TDT** to read the
engine's setup warning. **Download weights** downloads model files only;
it does not install NeMo or make an unavailable engine ready. There is no
NeMo **Install** button because installing it into the managed Python
environment would break the backend. Use one of the compatible engines
above instead; **Re-check** only checks dependencies again.

## Selecting it

Only meaningful for an independently managed backend where NeMo is already
available in a **separate, dedicated Python environment**. VoiceStudio does
not create or switch to that environment:

- **Settings → Models**, ASR tab → **Use** on the Parakeet TDT row, or
- `OMNIVOICE_ASR_BACKEND=nemo-parakeet`.

Auto-detect never picks it; it's an explicit opt-in.

## Best at

- **English and European-language transcription** where WER matters more
  than word-level subtitle timing.
- **CPU-only hosts** — faster than realtime without any GPU.

## Platform support

CUDA or CPU (the old hard CUDA gate was removed — see the RTF numbers
above). Availability is a pure dependency check on `nemo.collections.asr`.

## Model selection

`ASR_MODEL_NEMO` — default `nvidia/parakeet-tdt-0.6b-v3`. Weights download
on first load — see [downloading-models](../downloading-models.md).

## Quirks

- Output is a **single segment** for the whole file (NeMo doesn't VAD-split
  like Whisper), with word timestamps when the model exposes them — fine for
  dictation and plain transcripts, not ideal for long-form subtitles.
- The detected language isn't exposed cleanly by NeMo, so results report
  `en` regardless of the actual (auto-detected) language.
