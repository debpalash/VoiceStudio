#!/bin/bash
# Phase 1 on a fresh Vast.ai CUDA box: clone, deps, pretrained models.
# Usage: nohup bash /workspace/setup.sh > /workspace/setup.log 2>&1 &
set -e
cd /workspace
if [ ! -d GPT-SoVITS ]; then git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS; fi
cd /workspace/GPT-SoVITS
python3 -c "import torch; assert torch.cuda.is_available()" || { echo "NO CUDA TORCH"; exit 1; }
export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq ffmpeg git > /dev/null
pip install -q -r requirements.txt torchcodec "huggingface_hub>=0.34,<1.0" "transformers==4.57.6"
export NLTK_ALLOW_PROXIED_URLOPEN=1
python3 -c "import nltk; [nltk.download(p, quiet=True) for p in ['cmudict','averaged_perceptron_tagger_eng','punkt','punkt_tab']]"
# one pattern per call: multi-pattern --include silently skipped a folder earlier
for pat in s1v3.ckpt v2Pro/s2Gv2ProPlus.pth v2Pro/s2Dv2ProPlus.pth \
           "chinese-hubert-base/*" "chinese-roberta-wwm-ext-large/*" "sv/*"; do
  hf download lj1995/GPT-SoVITS --local-dir GPT_SoVITS/pretrained_models --include "$pat"
done
ls GPT_SoVITS/pretrained_models/s1v3.ckpt GPT_SoVITS/pretrained_models/v2Pro/s2Gv2ProPlus.pth
echo "=== SETUP_DONE $(date)"
