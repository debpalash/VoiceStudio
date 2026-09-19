"""Bake-off metrics: speaker similarity to Jake's raw voice, pace, pitch range, ASR text.
Usage: <gptsovits venv python> metrics.py <label> <wav> [<wav> ...]  (expects the passage text in PASSAGE)"""
import sys, glob, torch, librosa, numpy as np
sys.path.insert(0, '/Users/jake.tame/GPT-SoVITS/GPT_SoVITS/eres2net'); sys.path.insert(0, '/Users/jake.tame/GPT-SoVITS/GPT_SoVITS'); sys.path.insert(0, '/Users/jake.tame/GPT-SoVITS')
from sv import SV
from faster_whisper import WhisperModel
PASSAGE = ("The house was quiet when I got back, except for the clock in the hall. I set the letter down on the table "
           "and stood there for a while, not reading it. Outside, the rain had started again, soft against the windows.")
sv = SV('cpu', False)
def emb(path, dur=25):
    y, _ = librosa.load(path, sr=16000, mono=True, duration=dur)
    return sv.compute_embedding3(torch.from_numpy(y).unsqueeze(0)).squeeze().detach()
cos = lambda a, b: float(torch.nn.functional.cosine_similarity(a, b, dim=0))
raw_mean = torch.stack([emb(p) for p in sorted(glob.glob('/tmp/finetune-prep/work/segments/*.wav'))[::60][:8]]).mean(0)
asr = WhisperModel('small.en', device='cpu', compute_type='int8')
def wer(r, h):
    import re
    n = lambda s: re.sub(r"[^a-z0-9' ]+", " ", s.lower()).split()
    r, h = n(r), n(h); d = list(range(len(h)+1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            cur = min(d[j]+1, d[j-1]+1, prev+(rw != hw)); prev, d[j] = d[j], cur
    return d[len(h)] / max(1, len(r))
print(f"{'file':34s} {'dur':>6s} {'wpm':>4s} {'f0 p10-p90':>12s} {'range':>6s} {'sim':>5s} {'WER':>5s}")
for f in sys.argv[1:]:
    y, sr = librosa.load(f, sr=16000); d = len(y)/sr
    f0, _, _ = librosa.pyin(y, fmin=60, fmax=400, sr=sr, frame_length=1024); f0 = f0[~np.isnan(f0)]
    segs, _ = asr.transcribe(f, beam_size=5); hyp = ' '.join(s.text.strip() for s in segs)
    p10, p90 = np.percentile(f0, 10), np.percentile(f0, 90)
    print(f"{f.split('/')[-1]:34s} {d:6.2f} {len(PASSAGE.split())/d*60:4.0f} {p10:5.0f}-{p90:<6.0f} {p90-p10:6.0f} {cos(emb(f), raw_mean):5.3f} {wer(PASSAGE, hyp):5.2f}")
