# VoiceStudio — PyTorch Whisper Engine

> Native Windows ROCm changes on this branch are **DRAFT / not fully validated**.
> Earlier hardware checks do not certify this selective integration; full
> WhisperX remains unavailable. See the [draft scope and path limitations](../install/windows-rocm.md).

Whisper through the plain `transformers` pipeline, riding torch itself. No
extra install — transformers ships with the app — and because it runs on
torch's own stack (including torch's bundled cuDNN 9), it works on machines
where the CTranslate2 engines can't load. It is also the engine that
genuinely uses **AMD ROCm** GPUs. Auto-detect picks it on ROCm hosts without
a verified CTranslate2 HIP build
([#1529](https://github.com/debpalash/VoiceStudio/issues/1529)).

## Selecting it

- **Model Catalogue**, ASR tab → **Use** on the PyTorch Whisper
  row, or `OMNIVOICE_ASR_BACKEND=pytorch-whisper`.
- `OMNIVOICE_ASR_BACKEND=omnivoice` is accepted as a compatibility alias and
  selects this same PyTorch-native ASR path on ROCm hosts.
- Auto-detect picks it on Linux ROCm and on Windows ROCm without a verified
  CTranslate2 HIP build; otherwise it is the last resort.

## Best at

- **ROCm dubbing/transcription** — the PyTorch fallback uses the HIP GPU even
  when a CTranslate2 HIP wheel is unavailable (MLX is Apple-only). On Windows,
  the verified CTranslate2 HIP wheel runs faster-whisper on the GPU too.
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

The `openai/whisper-large-v3-turbo` pipeline generated 19 word chunks offline
on a Radeon RX 9070 XT with PyTorch ROCm 7.2.1; both the pipeline and model
weights stayed on `cuda:0` (PyTorch's device name for the HIP GPU). The
`gpu_compat` routing inventory therefore reports ROCm acceleration. This is
not evidence that every Whisper checkpoint or AMD GPU works.

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

The preflight sizes the weights; the generation workspace grows with the
batch on top. If a transcribe still hits a CUDA out-of-memory, the engine
steps the batch down (16 → 4 → 1, or 8 → 2 → 1 with word timestamps) and
finishes on the CPU rather than dropping the chunk — no silent holes in a
dub transcript.

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
