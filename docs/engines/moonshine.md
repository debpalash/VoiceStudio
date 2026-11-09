# VoiceStudio — Moonshine Engine

Moonshine is an edge-optimized ASR family built for CPU-only machines.
Unlike Whisper it processes variable-length audio (no padding everything to
30 s), which keeps latency low on short clips — sub-200 ms class on capture
buffers. It's the lightest local option for quick transcription on hardware
where even int8 whisper-large is too slow.

## Selecting it

- Install one of the runtimes into the app venv:
  `uv pip install moonshine-onnx` (lighter, tried first) or
  `moonshine-voice`.
- Then **Settings → Models**, ASR tab → **Use** on the Moonshine row,
  or `OMNIVOICE_ASR_BACKEND=moonshine`.

Auto-detect never picks it; it's an explicit opt-in.

## Best at

- **Quick notes and short-clip transcription on low-power CPU machines.**
- Environments where a sub-1 GB footprint matters more than word timing or
  language coverage.

## Not suited for

- **Dubbing.** Output is plain text as a **single segment spanning the whole
  file — no word or segment timestamps** — so there's nothing for lip-sync
  or subtitle timing to work with. Use a Whisper-family engine or
  [sherpa-onnx-asr](sherpa-onnx-asr.md) for those jobs.
- Multilingual work: results report English; for broad language coverage use
  [whisperx](whisperx.md) or [funasr](funasr.md).

## Platform support

CPU only, by design — macOS, Windows, and Linux. It claims no GPU.

## Model selection

`ASR_MODEL_MOONSHINE` — default `moonshine/base`. Weights download on first
load — see [downloading-models](../downloading-models.md).

## Quirks

- The engine tries `moonshine_onnx` first and falls back to
  `moonshine_voice` — installing either one is enough.
- Segment bounds are synthesized from the audio duration (start 0, end =
  file length), since the model reports none.
