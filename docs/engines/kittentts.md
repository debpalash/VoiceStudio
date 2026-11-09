# VoiceStudio — KittenTTS Engine

KittenTTS (KittenML) is the lightweight English "flash" tier: a 25–80 MB
ONNX model with 8 preset voices that runs realtime on any CPU — no torch, no
CUDA, no GPU of any kind. Use it when you just need quick English narration
(voiceovers, demo reads, short phrases) with no reference sample.

## When to pick it

- English-only content where speed and a tiny install matter more than
  cloning.
- Machines with no usable GPU.

The trade-off against [OmniVoice](omnivoice.md): no voice cloning, English
only — but a much faster and much smaller install.

## Setup

```bash
pip install kittentts
```

Then select the engine via **Model Catalogue** (TTS tab → **Use**) or
`OMNIVOICE_TTS_BACKEND=kittentts`.

## Voices

Eight preset voices, four male/female pairs:

```text
expr-voice-2-m  expr-voice-2-f   (default: expr-voice-2-f)
expr-voice-3-m  expr-voice-3-f
expr-voice-4-m  expr-voice-4-f
expr-voice-5-m  expr-voice-5-f
```

An unknown voice id logs an info message and falls back to the default.

## Model selection

| Variable | Default | Meaning |
| --- | --- | --- |
| `OMNIVOICE_KITTENTTS_MODEL` | `KittenML/kitten-tts-mini-0.8` | HuggingFace checkpoint to load |

The ~80 MB model downloads from HuggingFace on first use (retried once on a
flaky connection). See [downloading-models.md](../downloading-models.md).

## Behaviour notes

- Output is 24 kHz mono.
- CPU-only by design — the ONNX graph has no CUDA/MPS path.
- Non-English `language` values are ignored with a log line pointing at
  OmniVoice; reference audio is likewise ignored (no cloning).
- **Long-input hardening
  ([#1173](https://github.com/debpalash/VoiceStudio/issues/1173)):** the
  shipped ONNX graph has a hard 512-token cap, and phonemization can expand
  text massively (digits especially). VoiceStudio pre-measures every chunk
  with the model's own tokenizer and splits oversized chunks at word
  boundaries, so long or digit-heavy inputs no longer abort inside
  onnxruntime with an opaque "invalid expand shape" error.

## Known limits

- English only; no cloning, no voice design, no emotion controls
  (see [expressive-speech.md](../expressive-speech.md)).
- Preset voices only — speed is the one knob.

## Troubleshooting

- Engine unavailable: `pip install kittentts` into VoiceStudio's Python
  environment and restart.
- Model load fails with `EspeakDataError`: espeak-ng (the phonemizer behind
  KittenTTS) cannot open its data folder, usually because the install path is
  too long or contains non-ASCII characters. VoiceStudio already retries with a
  short copy in `%PROGRAMDATA%` (Windows), `~/.cache` or the temp folder; if
  none is writable, set `OMNIVOICE_ESPEAK_DATA_CACHE` to a short, writable
  ASCII folder (for example `C:\espeak`) and restart. Older builds exited the
  whole backend with `Error processing file '…phontab'` here.
- Other issues: [install/troubleshooting.md](../install/troubleshooting.md).

See also: [benchmarks.md](../benchmarks.md),
[disk usage](disk-usage.md).
