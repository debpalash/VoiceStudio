# Voice fine-tuning scripts

The pipeline used for the experiments in `docs/specs/voice-finetune.md`.
Inputs: `data/segments/*.wav` (5–20 s, 32 kHz mono) + `train.list`
(`path|SPEAKER|EN|transcript`) for GPT-SoVITS, or `flac/` + `metadata.csv`
(`audio_file|text`) for F5-TTS.

| script | role |
| --- | --- |
| `prep_dataset.py` | segment + transcribe raw recordings into the list format |
| `setup.sh` | fresh CUDA box: GPT-SoVITS clone, deps, pretrained weights, NLTK |
| `mkcfg2.py` | writes the SoVITS/GPT training configs (24 ep / 20 ep + DPO, warmup 300) |
| `train.sh` | prep (text, hubert, sv, semantic) → SoVITS → GPT; exports inference weights |
| `train_f5.sh` | F5-TTS v1 Base fine-tune from the same data |
| `strip_f5.py` | exports EMA fp16 weights from F5 training checkpoints |
| `metrics.py` | speaker similarity / wpm / pitch range / WER on a render |

Gotchas learned the hard way: single-GPU `gpu_numbers` must be `"0"` (`"0-0"`
silently trains on CPU); export dirs `SoVITS_weights_*`/`GPT_weights_*` must
exist or saves fail silently; prep scripts need `PYTHONPATH=<repo>/GPT_SoVITS:<repo>`.
