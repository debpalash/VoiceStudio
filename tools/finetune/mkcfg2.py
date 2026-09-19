import json, os, yaml
os.makedirs("logs/JAKE/logs_s2_v2ProPlus", exist_ok=True)
os.makedirs("logs/JAKE/logs_s1_v2ProPlus", exist_ok=True)
os.makedirs("SoVITS_weights_v2ProPlus", exist_ok=True)
os.makedirs("GPT_weights_v2ProPlus", exist_ok=True)
s2 = json.load(open("GPT_SoVITS/configs/s2v2ProPlus.json"))
s2["train"].update(
    batch_size=8, epochs=24, grad_ckpt=True, lora_rank=0,
    pretrained_s2G="GPT_SoVITS/pretrained_models/v2Pro/s2Gv2ProPlus.pth",
    pretrained_s2D="GPT_SoVITS/pretrained_models/v2Pro/s2Dv2ProPlus.pth",
    gpu_numbers="0", if_save_latest=True, if_save_every_weights=True, save_every_epoch=4,
)
s2["model"]["version"] = "v2ProPlus"
s2["data"]["exp_dir"] = s2["s2_ckpt_dir"] = "logs/JAKE"
s2["save_weight_dir"] = "SoVITS_weights_v2ProPlus"
s2["name"] = "JAKE"; s2["version"] = "v2ProPlus"
json.dump(s2, open("/tmp/tmp_s2.json", "w"), indent=1); print("s2 ok")
s1 = yaml.load(open("GPT_SoVITS/configs/s1longer-v2.yaml"), Loader=yaml.FullLoader)
s1["train"].update(
    batch_size=8, epochs=20, save_every_n_epoch=4,
    if_save_every_weights=True, if_save_latest=True, if_dpo=True,
    half_weights_save_dir="GPT_weights_v2ProPlus", exp_name="JAKE",
)
# 495 clips / batch 8 = 62 steps per epoch; the stock 2000-step warmup never
# reached peak lr in 15 epochs. Warm up over ~5 epochs instead.
s1["optimizer"]["warmup_steps"] = 300
s1["pretrained_s1"] = "GPT_SoVITS/pretrained_models/s1v3.ckpt"
s1["train_semantic_path"] = "logs/JAKE/6-name2semantic.tsv"
s1["train_phoneme_path"] = "logs/JAKE/2-name2text.txt"
s1["output_dir"] = "logs/JAKE/logs_s1_v2ProPlus"
open("/tmp/tmp_s1.yaml", "w").write(yaml.dump(s1, default_flow_style=False)); print("s1 ok")
