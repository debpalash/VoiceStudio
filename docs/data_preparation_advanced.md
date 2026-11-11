# Advanced Data Preparation

The advanced pipeline adds **denoising** and **prompt noise augmentation** on top of the basic tokenization workflow. Each stage is optional.

## Prerequisites

- **Dependencies**: `uv sync --extra train` (WebDataset).
- **Denoising**: Sidon model checkpoints (`feature_extractor_cuda.pt`, `decoder_cuda.pt`) from https://huggingface.co/sarulab-speech/sidon-v0.1/tree/main.
- **Noise augmentation**: noise + RIR tar shards with `data.lst` manifests

## Pipeline Overview

```
Step 1 (optional): Denoise
  Raw audio → Sidon denoiser → clean audio

Step 2: Tokenize (with optional noise augmentation)
  Clean audio + noise augment on prefix → audio tokenizer → tokens
```


## Denoise 

Use the [Sidon](https://github.com/sarulab-speech/Sidon) speech enhancement model to remove background noise from raw audio.

```bash
export CUDA_VISIBLE_DEVICES="0,1,2,3"
python -m omnivoice.scripts.denoise_audio \
    --input_jsonl data.jsonl \
    --tar_output_pattern data/denoised/audios/shard-%06d.tar \
    --jsonl_output_pattern data/denoised/txts/shard-%06d.jsonl \
    --feature_extractor_path /path/to/sidon_feature_extractor_cuda.pt \
    --decoder_path /path/to/sidon_decoder_cuda.pt \
    --target_sample_rate 24000 \
    --batch_duration 200.0
```

What it does:
1. Reads your JSONL manifest
2. Runs Sidon denoiser on each audio file
3. Outputs denoised audio as custom WebDataset tar/jsonl shards
4. Generates a `data.lst` manifest in `data/denoised/`

> You can also pass `--input_manifest /path/to/data.lst` if you already have a custom webdataset format dataset.
> The next step would be passing the generated `data.lst` file with `--input_manifest` to `omnivoice.scripts.extract_audio_tokens` for tokens extraction.


### Tokenize with noise augmentation

Adds environmental noise and room reverb to **prompt audio** during tokenization, making the model robust to noisy reference audio at inference time. Note that in our model, we only add noise augmentation for a small proportion of data, making sure the model can also generate good audio with clean reference audio.

You need two additional datasets in WebDataset format:
- **Noise recordings**: environmental noise tar shards with a `data.lst` manifest
- **Room impulse responses (RIR)**: RIR tar shards with a `data.lst` manifest

```bash
export CUDA_VISIBLE_DEVICES="0,1,2,4"
python -m omnivoice.scripts.extract_audio_tokens_add_noise \
    --input_jsonl data.jsonl \
    --tar_output_pattern data/tokens/shard-%06d.tar \
    --jsonl_output_pattern data/txts/shard-%06d.jsonl \
    --tokenizer_path eustlb/higgs-audio-v2-tokenizer \
    --noise_manifest data/noise_shards/data.lst \
    --rir_manifest data/rir_shards/data.lst \
    --nj_per_gpu 3
```

> You can also pass `--input_manifest /path/to/data.lst` if you already have a custom webdataset format dataset.
