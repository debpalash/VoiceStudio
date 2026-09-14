# VoiceStudio — MLX-Audio Engine (Apple Silicon)

MLX-Audio (Blaizzy/mlx-audio) wraps 14+ TTS engines — Kokoro, CSM, Dia,
Qwen3-TTS, Chatterbox, MeloTTS, OuteTTS, and more — behind a single adapter
that runs on Apple's MLX framework. It is **Apple Silicon only**: the engine
is not shipped on Linux, Windows, or Intel Macs, and a stray wheel on those
platforms never reports as available
([#390](https://github.com/debpalash/VoiceStudio/issues/390)).

## When to pick it

- You're on an M-series Mac and want small, fast models tuned for it.
- You want one of the specific hosted models (Kokoro for small multilingual,
  CSM for cloning, Qwen3-TTS for voice design, Dia for dialogue, …).

## Setup

```bash
pip install mlx-audio
```

Then select the engine via **Model Catalogue** (TTS tab → **Use**) or
`OMNIVOICE_TTS_BACKEND=mlx-audio`.

## Model selection

One backend hosts many models. The curated set:

| Key | Model | Niche |
| --- | --- | --- |
| `kokoro` (default) | `mlx-community/Kokoro-82M-bf16` | small multilingual |
| `csm` | `mlx-community/csm-1b-8bit` | voice cloning |
| `qwen3-tts` | `mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-4bit` | voice design |
| `dia` | `mlx-community/Dia-1.6B` | dialogue |
| `chatterbox` | `mlx-community/Chatterbox-TTS-4bit` | expressive |
| `melotts` | `mlx-community/MeloTTS-English-v3-MLX` | lightweight VITS |
| `outetts` | `mlx-community/Llama-OuteTTS-1.0-1B-4bit` | LM-based |

Pick a model in the **Model Catalogue** curated picker
([#981](https://github.com/debpalash/VoiceStudio/issues/981)) or set
`OMNIVOICE_MLX_AUDIO_MODEL` to either a curated key (`kokoro`) or any full
HF repo id. The env var overrides the persisted UI choice.

## Behaviour notes

- Output is 24 kHz mono. Results from models with a different native rate
  (such as Dia at 44.1 kHz) are resampled before stitching and export.
- **Cloning works only with the `csm` model** — it is the only curated model
  confirmed to accept a reference clip. Other models silently ignore
  reference audio, so the engine reports cloning support only when CSM is
  selected (dub/batch jobs gate on this).
- Voice design (text description → voice) is available through the
  Qwen3-TTS VoiceDesign model.
- Language support is per-model (Kokoro ~8 languages, others vary). An
  unsupported language for Kokoro produces a clear error naming what it
  does support ([#977](https://github.com/debpalash/VoiceStudio/issues/977))
  — leave language on Auto or switch to a multilingual engine.

## Platform notes

This engine is exempt from cross-platform parity as a platform-only
capability behind explicit opt-in: it exists only where Apple's MLX runtime
exists. On any other platform the engine picker shows it unavailable with
the reason.

## Troubleshooting

- Unavailable on an M-series Mac: `pip install mlx-audio` into
  VoiceStudio's Python environment; in a packaged app build, MLX's native
  libraries may fail to load — the engine reports unavailable rather than
  crashing.
- Other issues: [install/troubleshooting.md](../install/troubleshooting.md).

See also: [benchmarks.md](../benchmarks.md),
[languages.md](../languages.md),
[downloading-models.md](../downloading-models.md),
[disk usage](disk-usage.md).
