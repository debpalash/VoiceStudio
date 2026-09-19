"""Export inference-only EMA weights (fp16 safetensors) from F5-TTS training checkpoints."""
import sys, glob, os, torch
from safetensors.torch import save_file
src = sys.argv[1] if len(sys.argv) > 1 else "/workspace/F5-TTS/ckpts/jake"
dst = "/workspace/f5_export"; os.makedirs(dst, exist_ok=True)
for p in sorted(glob.glob(f"{src}/model_*.pt")):
    ck = torch.load(p, map_location="cpu", weights_only=True)
    sd = ck.get("ema_model_state_dict") or ck.get("model_state_dict")
    sd = {k.replace("ema_model.", ""): v.to(torch.float16) if v.is_floating_point() else v for k, v in sd.items() if k not in ("initted", "step") and not k.endswith(".initted") and not k.endswith(".step")}
    out = f"{dst}/{os.path.basename(p)[:-3]}.safetensors"; save_file(sd, out)
    print(out, os.path.getsize(out) // 1048576, "MB", "update", ck.get("update", ck.get("step")))
