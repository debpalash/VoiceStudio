#!/bin/bash
# Phase 2: prep -> SoVITS -> GPT(DPO). Needs /workspace/data/{segments,train.list} + mkcfg2.py.
# Usage: nohup bash /workspace/train.sh > /workspace/train.log 2>&1 &
set -e
cd /workspace/GPT-SoVITS
export inp_text=/workspace/data/train_clean.list inp_wav_dir=/workspace/data/segments
export exp_name=JAKE opt_dir=logs/JAKE i_part=0 all_parts=1
export _CUDA_VISIBLE_DEVICES=0 is_half=True version=v2ProPlus
export bert_pretrained_dir=GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
export cnhubert_base_dir=GPT_SoVITS/pretrained_models/chinese-hubert-base
export sv_path=GPT_SoVITS/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt
export pretrained_s2G=GPT_SoVITS/pretrained_models/v2Pro/s2Gv2ProPlus.pth
export s2config_path=GPT_SoVITS/configs/s2v2ProPlus.json
mkdir -p logs/JAKE
echo "=== 1a text"; python3 -s GPT_SoVITS/prepare_datasets/1-get-text.py
cp logs/JAKE/2-name2text-0.txt logs/JAKE/2-name2text.txt
echo "=== 1b hubert"; python3 -s GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py
echo "=== sv"; python3 -s GPT_SoVITS/prepare_datasets/2-get-sv.py
echo "=== 1c semantic"; python3 -s GPT_SoVITS/prepare_datasets/3-get-semantic.py
printf 'item_name\tsemantic_audio\n' > logs/JAKE/6-name2semantic.tsv
cat logs/JAKE/6-name2semantic-0.tsv >> logs/JAKE/6-name2semantic.tsv
wc -l logs/JAKE/2-name2text.txt logs/JAKE/6-name2semantic.tsv; ls logs/JAKE/7-sv_cn | wc -l
echo "=== PREP_DONE $(date)"
python3 /workspace/mkcfg2.py
echo "=== S2 (SoVITS)"; python3 -s GPT_SoVITS/s2_train.py --config /tmp/tmp_s2.json
ls -la SoVITS_weights_v2ProPlus
echo "=== S1 (GPT)"; hz=25hz python3 -s GPT_SoVITS/s1_train.py --config_file /tmp/tmp_s1.yaml
ls -la GPT_weights_v2ProPlus
echo "=== ALL_DONE $(date)"
