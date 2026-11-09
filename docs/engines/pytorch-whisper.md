# VoiceStudio — PyTorch Whisper Engine

Whisper through the plain `transformers` pipeline, riding torch itself. No
extra install — transformers ships with the app — and because it runs on
torch's own stack (including torch's bundled cuDNN 9), it works on machines
where the CTranslate2 engines can't load. It is also the engine that
genuinely uses **AMD ROCm** GPUs, so auto-detect picks it on ROCm hosts
([#1529](https://github.com/debpalash/VoiceStudio/issues/1529)).

## Selecting it

- **Model Catalogue**, ASR tab → **Use** on the PyTorch Whisper
  row, or `OMNIVOICE_ASR_BACKEND=pytorch-whisper`.
- `OMNIVOICE_ASR_BACKEND=omnivoice` is accepted as a compatibility alias and
  selects this same PyTorch-native ASR path on ROCm hosts.
- Auto-detect picks it on ROCm, and as the last resort everywhere else.

## Best at

- **ROCm dubbing/transcription** — the only Whisper engine that uses the HIP
  GPU (CTranslate2 has no HIP build, MLX is Apple-only).
- **Rescue engine** when whisperx/faster-whisper can't load — e.g. the
  missing-cuDNN-8 case
  ([#255](https://github.com/debpalash/VoiceStudio/issues/255)) — since it
  needs neither CTranslate2 nor cuDNN 8.

For lip-sync-grade word timing prefer [whisperx](whisperx.md) or
[mlx-whisper](mlx-whisper.md); this engine returns the pipeline's own word
timestamps.

## Platform support

CUDA, Apple Silicon (MPS), ROCm (HIP), and CPU — wherever torch runs, on
macOS, Windows, and Linux.

## Model selection

`OMNIVOICE_PYTORCH_ASR_MODEL` — default `openai/whisper-large-v3-turbo`. Any
transformers-format Whisper repo works. Weights download on first load — see
[downloading-models](../downloading-models.md).

## VRAM preflight

Loading a model onto a nearly-full card "succeeds", and then the first
transcribe runs out of memory with zero segments. So on CUDA the engine
checks free VRAM before loading and uses the CPU instead when the card is
too full (flush the TTS model to restore GPU-speed ASR).

For the OpenAI Whisper checkpoints, the budget follows the model it loads in
fp16: the weights, plus about 1.5 GB of working memory and 0.5 GB of
headroom. Any other repository, including a fine-tune, keeps 5 GB.

| Model | Free VRAM needed |
|---|---|
| `openai/whisper-large-v3-turbo` (default) | 3.6 GB |
| `openai/whisper-large`, `-large-v2`, `-large-v3` | 5 GB |
| `openai/whisper-medium` / `-small` / `-base` / `-tiny` (and `.en`) | 3.5 / 2.5 / 2.2 / 2.1 GB |
| any other repository | 5 GB |

A 6 GB card with nothing else loaded runs the default model on the GPU
([#2041](https://github.com/debpalash/VoiceStudio/issues/2041)). Disable
the check with `OMNIVOICE_ASR_VRAM_PREFLIGHT=0`.

If a GPU run still runs out of memory (the preflight only sizes the model
weights, not activations), the backend retries the same audio with smaller
batches instead of failing: 16, then 4, then 1, or 8, then 2, then 1 when word
timestamps are requested. If even batch size 1 does not fit, the pipeline moves
to the CPU and finishes there, so no chunk is dropped.

## Quirks

- If the pipeline fails to import (`AutoFeatureExtractor` errors), the cause
  is either an incomplete transformers install or a torch/torchvision
  version mismatch — the error message names the exact reinstall command;
  the trio has to move together at the pinned versions
  ([#549](https://github.com/debpalash/VoiceStudio/issues/549),
  [#1376](https://github.com/debpalash/VoiceStudio/issues/1376)).
- Transcribes are time-bounded like every local engine:
  `OMNIVOICE_TRANSCRIBE_CHUNK_TIMEOUT_S` (default 120 s per dub chunk),
  `OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S` (default 300 s whole-file).

Speed comparisons across engines live in [performance](../performance.md).
