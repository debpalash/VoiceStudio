#!/bin/bash
# F5-TTS v1 Base fine-tune on JAKE data. Needs /workspace/f5data.tar (flac/ + metadata.csv) and onstart done.
# Usage: nohup bash /workspace/train_f5.sh > /workspace/train_f5.log 2>&1 &
set -e
cd /workspace
until [ -f /workspace/ONSTART_DONE ]; do sleep 10; done
mkdir -p f5in/wavs && tar xf f5data.tar -C f5in
cd f5in
find flac -name '*.flac' | xargs -P 8 -I{} sh -c 'b=$(basename {} .flac); ffmpeg -hide_banner -loglevel error -y -i {} -ar 24000 -ac 1 wavs/$b.wav'
echo "wavs: $(find wavs -name '*.wav' | wc -l)"
sed -i 's#^wavs/#/workspace/f5in/wavs/#' metadata.csv
head -3 metadata.csv
cd /workspace/F5-TTS
echo "=== PREP $(date)"
python src/f5_tts/train/datasets/prepare_csv_wavs.py /workspace/f5in/metadata.csv data/jake_char
ls data/jake_char; python -c "import json;d=json.load(open('data/jake_char/duration.json'))['duration'];print('clips',len(d),'hours %.2f'%(sum(d)/3600))"
echo "=== TRAIN $(date)"
accelerate launch --num_processes 1 src/f5_tts/train/finetune_cli.py \
  --exp_name F5TTS_v1_Base --dataset_name jake --finetune \
  --pretrain /workspace/ckpts_pre/F5TTS_v1_Base/model_1250000.safetensors \
  --tokenizer char --learning_rate 1e-5 \
  --batch_size_per_gpu 12800 --batch_size_type frame --max_samples 64 \
  --epochs 60 --num_warmup_updates 200 \
  --save_per_updates 500 --keep_last_n_checkpoints -1 --last_per_updates 100 \
  --logger tensorboard
ls -la ckpts/jake
echo "=== ALL_DONE $(date)"
