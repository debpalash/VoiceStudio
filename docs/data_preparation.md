# Data Preparation

VoiceStudio trains on a custom WebDataset format where audio data is packed into **tar shards** with paired **JSONL metadata** files. Each tar shard contains hundreds to thousands of samples (as `.npy` audio token arrays), drastically reducing disk I/O during training. The separated jsonl file allows for easier modification of metadata. This document explains the data format in detail and walks through the preparation pipeline.

The preparation scripts need the `train` extra: `uv sync --extra train`.


## 1. Input Format

Prepare a JSONL file where each line is a JSON object:

```jsonl
{"id": "sample_001", "audio_path": "/data/audio/001.wav", "text": "Hello world", "language_id": "en"}
{"id": "sample_002", "audio_path": "/data/audio/002.wav", "text": "你好世界", "language_id": "zh"}
```

Fields:
- `id` — unique sample identifier (used to match samples across shards and label files)
- `audio_path` — absolute path to the audio file (wav/flac/mp3, will be resampled to 24 kHz)
- `text` — transcript text
- `language_id` — (optional) language code, used for multilingual training, can be omitted


## 2. Processing

The tokenization script `extract_audio_tokens.py` converts audio into 8-layer discrete tokens and packs them into WebDataset shards.

```bash
export CUDA_VISIBLE_DEVICES="0,1,2,4"  # GPUs used for token extraction
python -m omnivoice.scripts.extract_audio_tokens \
    --input_jsonl data.jsonl \
    --tar_output_pattern output/audios/shard-%06d.tar \
    --jsonl_output_pattern output/txts/shard-%06d.jsonl \
    --tokenizer_path eustlb/higgs-audio-v2-tokenizer \
    --nj_per_gpu 3 \
    --shuffle True
```

What it does:
1. Reads your JSONL manifest
2. Encodes each audio file into discrete tokens using audio tokenizer
3. Packs tokens into WebDataset tar shards with paired jsonl metadata files
4. Generates a `data.lst` manifest file

<details>
<summary><strong>Alternative:</strong> WebDataset Input (if you already have raw-audio tar shards)</summary>

Pass the `data.lst` manifest instead of `--input_jsonl`:

```bash
export CUDA_VISIBLE_DEVICES="0,1,2,4"  # GPUs used for token extraction
python -m omnivoice.scripts.extract_audio_tokens \
    --input_manifest existing_data/data.lst \
    --tar_output_pattern output/audios/shard-%06d.tar \
    --jsonl_output_pattern output/txts/shard-%06d.jsonl \
    --tokenizer_path eustlb/higgs-audio-v2-tokenizer \
    --nj_per_gpu 3 \
    --shuffle True
```

The existing_data/data.lst is generated with:
```bash
python -m omnivoice.scripts.jsonl_to_webdataset \
    --input data.jsonl \
    --output data/shards \
    --sr 24000 \
    --shard-size 1000
```

This resamples audio to the target sample rate and packs FLAC files into tar shards with paired jsonl metadata files.

</details>



### Explanation of the script's options:

| Option | Default | Description |
|---|---|---|
| `--input_manifest` | None | Path to input dataset manifest (`data.lst`), mutually exclusive with `--input_jsonl` |
| `--input_jsonl` | None | Path to raw JSONL file, mutually exclusive with `--input_manifest` |
| `--tar_output_pattern` | (required) | Tar shard output pattern, e.g. `output/audios/shard-%06d.tar` |
| `--jsonl_output_pattern` | (required) | JSONL shard output pattern, e.g. `output/txts/shard-%06d.jsonl` |
| `--tokenizer_path` | `eustlb/higgs-audio-v2-tokenizer` | HuggingFace tokenizer path or local path |
| `--nj_per_gpu` | 3 | Worker processes per GPU |
| `--loader_workers` | 24 | DataLoader workers for streaming `IterableDataset` |
| `--shuffle` | True | Shuffle samples before sharding |
| `--shuffle-seed` | 42 | Random seed for shuffling |
| `--samples_per_shard` | 1000 | Max samples per tar shard |
| `--min_num_shards` | 32 | Minimum number of output shards (ensures shard count >= num\_gpu × num\_workers) |
| `--min_length` | 0.0 | Skip audio shorter than this (seconds) |
| `--max_length` | inf | Skip audio longer than this (seconds) |
| `--skip_errors` | False | Continue on processing errors instead of aborting |
| `--num_machines` | 1 | Total number of machines for distributed runs |
| `--machine_index` | 0 | Zero-based machine index for distributed preprocessing |


### Output Structure

Output structure with the following output patterns

```bash
--tar_output_pattern output/audios/shard-%06d.tar \
--jsonl_output_pattern output/txts/shard-%06d.jsonl
```

will be:

```
output/
├── audios/                    # WebDataset tar shards (audio tokens)
│   ├── shard-000000.tar       # Each tar packs ~1000 samples
│   ├── shard-000001.tar
│   └── ...
├── txts/                      # Per-shard companion JSONL labels
│   ├── shard-000000.jsonl     # One JSON line per sample in the corresponding tar
│   ├── shard-000001.jsonl
│   └── ...
├── data.lst                   # Manifest linking tar ↔ jsonl shards
└── errors.jsonl               # Samples that failed processing (if any)
```

`data.lst` and `errors.jsonl` are written to the **parent directory** of `audios/` and `txts/`.


### The `data.lst` manifest

Each line in `data.lst` describes one shard:

```
/path/to/shard-000000.tar /path/to/shard-000000.jsonl 1000 3600.500
/path/to/shard-000001.tar /path/to/shard-000001.jsonl 800 2880.200
```

Format: `<tar_path> <jsonl_path> <num_samples> <total_duration_seconds>`

- Paths are **absolute**
- `.tar` file contains the audio tokens.
- `.jsonl` file contains the metadata in the original provided JSONL file, allows easier access and modification of metadata without decompressing the tar file.
- This manifest is what the training data config references.

### Inside a tar shard

Each `.tar` file packs **many samples** (default 1000 per shard) into a single archive. This is the key advantage of WebDataset: instead of reading thousands of tiny files, the dataloader reads sequentially from a few large tars, drastically reducing disk I/O pressure.

Each sample in the tar is a pair of files with matching keys:

```
shard-000000.tar:
  sample_001.npy    # Audio tokens: numpy array, shape [8, T], dtype int16
  sample_002.npy
  ...
  sample_1000.npy
```

## 3. Data Config for Training

After creating WebDataset shards, write a data config JSON that references them:

```json
{
    "train": [
        {
            "language_id": "en",
            "manifest_path": ["data/custom/tokens/train/data.lst"],
            "repeat": 1
        }
    ],
    "dev": [
        {
            "language_id": "en",
            "manifest_path": ["data/custom/tokens/dev/data.lst"],
            "repeat": 1
        }
    ]
}
```
- `manifest_path` — list of `data.lst` files (one per shard directory)
- `repeat` — how many times to repeat this dataset per epoch (useful for balancing languages)
- `language_id` is not used, just for a better data organization.

See [examples/config/](../examples/config/) for ready-to-use data config files.

> See [docs/data_preparation_advanced.md](../docs/data_preparation_advanced.md) for denoising and noise augmentation.