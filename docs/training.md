# Training

Install the training dependencies first:

```bash
uv sync --extra train
```

`uv sync` does not activate the environment in your shell. Run the training
commands through it with `uv run --extra train ...` (for example
`uv run --extra train accelerate launch ...`), or activate `.venv` first.

## Training Config

All training is controlled by a JSON training config file and a JSON data config file. 

See [examples/config/](../examples/config/) for ready-to-use configs.

Training config file on Emilia is: [examples/config/train_config_emilia.json](../examples/config/train_config_emilia.json)

Data config file for Emilia is: [examples/config/data_config_emilia.json](../examples/config/data_config_emilia.json)


Key fields in training config file:

| Field | Description | Default |
|---|---|---|
| `llm_name_or_path` | local LLM path or huggingface id | Qwen/Qwen3-0.6B |
| `steps` | Total training steps | 300,000 |
| `learning_rate` | Peak learning rate | 1e-4 |
| `batch_tokens` | Tokens per batch on each GPU | 8192 |

`output_dir` and `data_config` are passed via command line (see below).

## Launching Training

```bash
accelerate launch \
    --gpu_ids "0,1,2,3,4,5,6,7" \
    --num_processes 8 \
    -m omnivoice.cli.train \
    --train_config config/train_config_emilia.json \
    --data_config config/data_config_emilia.json \
    --output_dir exp/omnivoice_emilia
```

## Resuming Training

Set `resume_from_checkpoint` in your training config to resume from an existing checkpoint:

```json
{
    "resume_from_checkpoint": "exp/omnivoice/checkpoint-100000"
}
```

## Initializing from a Pretrained Model

To start training from a pretrained VoiceStudio checkpoint (for fine-tuning):

```json
{
    "init_from_checkpoint": "exp/omnivoice/checkpoint-100000"
}
```

## Monitoring

Training logs to TensorBoard:
```bash
tensorboard --logdir exp/omnivoice_emilia/tensorboard
```
