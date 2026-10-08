# VoiceStudio Examples

This directory contains integration examples plus scripts and configs for
training, fine-tuning, and evaluating VoiceStudio.

| Use Case | Script | Description |
|---|---|---|
| Training from scratch | [run_emilia.sh](run_emilia.sh) | Full pipeline on the Emilia dataset (data check, tokenization, training) |
| Fine-tuning | [run_finetune.sh](run_finetune.sh) | Fine-tune from a pretrained checkpoint using your own JSONL data |
| Evaluation | [run_eval.sh](run_eval.sh) | Evaluate WER, speaker similarity, and UTMOS on standard test sets |

---

## Training from Scratch (Emilia)

[run_emilia.sh](run_emilia.sh) runs the full pipeline in 3 stages:

| Stage | What it does |
|---|---|
| 0 | Verify the Emilia dataset and JSONL manifests are in place |
| 1 | Tokenize audio into WebDataset shards |
| 2 | Launch multi-GPU training with `accelerate` |

**Prerequisites:**

1. Download the Emilia dataset from [OpenXLab](https://openxlab.org.cn/datasets/Amphion/Emilia) and place it under `download/`:
   ```
   download/Amphion___Emilia
   └── raw
       ├── EN
       └── ZH
   ```
2. Obtain JSONL manifests and place them in `data/emilia/manifests/`:
   - `emilia_en_train.jsonl`, `emilia_en_dev.jsonl`
   - `emilia_zh_train.jsonl`, `emilia_zh_dev.jsonl`

   You can generate them from the raw data, or download pre-processed manifests from [HuggingFace](https://huggingface.co/datasets/zhu-han/Emilia-Manifests).

**Run the full pipeline:**

```bash
bash examples/run_emilia.sh
```

Or run individual stages by setting `stage` and `stop_stage` at the top of the script (e.g. `stage=1`, `stop_stage=1` to only tokenize).

> See [docs/training.md](../docs/training.md) for config details, checkpoint resuming, and TensorBoard monitoring.

---

## Fine-tuning

[run_finetune.sh](run_finetune.sh) fine-tunes from a pretrained checkpoint on your own data.

### Step 1: Prepare Your Data

Create a JSONL manifest where each line describes one audio sample:

```jsonl
{"id": "sample_001", "audio_path": "/data/audio/001.wav", "text": "Hello world", "language_id": "en"}
{"id": "sample_002", "audio_path": "/data/audio/002.wav", "text": "你好世界", "language_id": "zh"}
```

`id`, `audio_path`, and `text` are mandatory. `language_id` is optional.

> See [docs/data_preparation.md](../docs/data_preparation.md) for the full data format specification.

### Step 2: Configure the Script

Edit the variables at the top of `run_finetune.sh`:

```bash
TRAIN_JSONL="data/my_data_train.jsonl"   # path to training JSONL
DEV_JSONL="data/my_data_dev.jsonl"       # path to dev JSONL
GPU_IDS="0,1"                            # GPUs to use
NUM_GPUS=2
OUTPUT_DIR="exp/omnivoice_finetune"      # output directory
```

### Step 3: Run

```bash
bash examples/run_finetune.sh
```

The script will:
1. Tokenize your audio into WebDataset shards
2. Launch fine-tuning with `accelerate`

Main difference between fine-tuning config ([config/train_config_finetune.json](config/train_config_finetune.json)) and the Emilia training config ([config/train_config_emilia.json](config/train_config_emilia.json)) are:

| Parameter | Emilia (from scratch) | Fine-tune | Why |
|---|---|---|---|
| `init_from_checkpoint` | `null` | `"k2-fsa/OmniVoice"` | Load pretrained weights |
| `steps` | 300,000 | 5,000 | Fewer steps for fine-tuning, can be tuned according to your data/task. |
| `learning_rate` | 1e-4 | 5e-5 | Lower LR for fine-tuning, can be tuned according to your data/task |

To use a different pretrained checkpoint, modify `init_from_checkpoint` in the config file.

---

## Evaluation

Install evaluation dependencies first:

```bash
pip install omnivoice[eval]
# or
uv sync --extra eval
```

Supported test sets: `librispeech_pc`, `seedtts_en`, `seedtts_zh`, `fleurs`, `minimax`.

```bash
bash examples/run_eval.sh
```

> See [docs/evaluation.md](../docs/evaluation.md) for metrics details, test set preparation, and running individual metrics.
