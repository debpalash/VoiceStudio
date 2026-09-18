#!/usr/bin/env python3
"""Fine-tune dataset prep: normalize -> segment -> transcribe -> manifests.

Input : directory of raw recordings (mp3/m4a/wav, any SR/channels)
Output: 32kHz mono WAV segments (GPT-SoVITS) + train/val .list files,
        plus segments.jsonl that feeds the OmniVoice tokenizer step.

Stages (run with --stage all, or one of audit/convert/segment/transcribe/manifest):
  audit      per-file duration/level report (read-only)
  convert    -> work/wavs/<slug>.wav  (32k mono, highpass, loudness-norm)
  segment    -> work/segments/<slug>_NNNN.wav + segments.jsonl (3-15s VAD-ish chunks)
  transcribe faster-whisper per segment (needs: pip install faster-whisper)
  manifest   -> train.list / val.list (GPT-SoVITS) + train/dev split files

Stdlib-only except the transcribe stage. Requires ffmpeg/ffprobe on PATH.

Example:
  python3 prep.py --in ~/Desktop/Audio --out /tmp/finetune-prep/work --speaker JAKE
  python3 prep.py --in ~/Desktop/Audio --out work --stage segment --skip-asr
"""
import argparse, json, math, os, re, subprocess, sys
from pathlib import Path

SEG_MIN, SEG_TARGET, SEG_MAX = 1.0, 15.0, 20.0
DEV_RATIO = 0.05


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def slug(name):
    s = re.sub(r"\.[^.]+$", "", name)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s or "audio"


def probe(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,sample_rate,channels",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1", str(path)])
    info = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    return {"codec": info.get("codec_name"), "sr": int(info.get("sample_rate", 0)),
            "ch": int(info.get("channels", 0)), "dur": float(info.get("duration", 0))}


def loudness(path):
    r = run(["ffmpeg", "-hide_banner", "-i", str(path), "-map", "0:a",
             "-af", "volumedetect", "-f", "null", "-"])
    mean = maxv = None
    for line in r.stderr.splitlines():
        if "mean_volume" in line:
            mean = float(line.split("mean_volume:")[1].split()[0])
        if "max_volume" in line:
            try:
                maxv = float(line.split("max_volume:")[1].split()[0])
            except ValueError:
                maxv = None
    return mean, maxv


def input_files(indir):
    exts = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus"}
    return sorted(p for p in Path(indir).iterdir()
                  if p.is_file() and p.suffix.lower() in exts
                  and not p.name.startswith("."))


def stage_audit(indir):
    files = input_files(indir)
    if not files:
        sys.exit(f"no audio files in {indir}")
    print(f"{'minutes':>7} {'mean':>10} {'max':>8}  format            file")
    total = 0.0
    for f in files:
        pr = probe(f)
        mean, maxv = loudness(f)
        total += pr["dur"]
        flag = ""
        if mean is not None and mean < -28:
            flag = "  <-- QUIET, will get extra gain"
        print(f"{pr['dur']/60:7.1f} {mean:>9.1f}dB {maxv if maxv is None else f'{maxv:7.1f}dB'}  "
              f"{pr['codec']}/{pr['sr']}Hz/{pr['ch']}ch  {f.name}{flag}")
    print(f"TOTAL: {total/3600:.2f}h ({total/60:.0f}min) across {len(files)} files")
    return files


def stage_convert(indir, outdir, sr):
    wavs = Path(outdir) / "wavs"
    wavs.mkdir(parents=True, exist_ok=True)
    for f in input_files(indir):
        dest = wavs / (slug(f.name) + ".wav")
        if dest.exists():
            print(f"skip (exists): {dest.name}")
            continue
        print(f"convert: {f.name} -> {dest.name}")
        r = run(["ffmpeg", "-hide_banner", "-y", "-i", str(f), "-map", "0:a",
                 "-ac", "1", "-ar", str(sr),
                 "-af", "highpass=f=60,loudnorm=I=-22:TP=-1.5:LRA=11",
                 "-c:a", "pcm_s16le", str(dest)])
        if r.returncode != 0:
            sys.exit(f"ffmpeg failed on {f.name}:\n{r.stderr[-2000:]}")
    return wavs


def find_silences(wav, noise_db=-35, min_dur=0.4):
    r = run(["ffmpeg", "-hide_banner", "-i", str(wav), "-map", "0:a",
             "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
             "-f", "null", "-"])
    sil, start = [], None
    for line in r.stderr.splitlines():
        if "silence_start" in line:
            start = float(line.split("silence_start:")[1].strip())
        elif "silence_end" in line and start is not None:
            end = float(line.split("silence_end:")[1].split("|")[0].strip())
            sil.append((start, end))
            start = None
    return sil


def plan_segments(dur, silences):
    """Cut points at silence midpoints; merge to hit 3-15s windows."""
    cuts = [0.0]
    for s, e in silences:
        cuts.append((s + e) / 2)
    cuts.append(dur)
    segs, cur = [], cuts[0]
    for nxt in cuts[1:]:
        # Hard cap first: never emit a span longer than SEG_MAX.
        while nxt - cur >= SEG_MAX:
            segs.append((cur, cur + SEG_TARGET))
            cur += SEG_TARGET
        span = nxt - cur
        if span >= SEG_TARGET or nxt == dur or (dur - nxt) < 3.0:
            if span >= 3.0:
                segs.append((cur, nxt))
                cur = nxt
            # else: tiny span, keep accumulating into next cut
    if dur - cur >= SEG_MIN:
        if segs and dur - cur < 3.0:
            s, _ = segs.pop()
            if dur - s <= SEG_MAX + 5:
                segs.append((s, dur))
            else:  # tail merge would overshoot: keep both pieces
                segs.append((s, cur))
                segs.append((cur, dur))
        else:
            # emit tail, hard-splitting if needed
            while dur - cur >= SEG_MAX:
                segs.append((cur, cur + SEG_TARGET))
                cur += SEG_TARGET
            if dur - cur >= SEG_MIN:
                segs.append((cur, dur))
    assert all(e - s <= SEG_MAX + 5 for s, e in segs), segs
    return [(s, e) for s, e in segs if e - s >= SEG_MIN]


def stage_segment(outdir):
    wavs = Path(outdir) / "wavs"
    segdir = Path(outdir) / "segments"
    segdir.mkdir(exist_ok=True)
    manifest = Path(outdir) / "segments.jsonl"
    rows, total_speech, dropped = [], 0.0, 0
    for wav in sorted(wavs.glob("*.wav")):
        dur = probe(wav)["dur"]
        segs = plan_segments(dur, find_silences(wav))
        print(f"{wav.name}: {dur/60:.1f}min -> {len(segs)} segments")
        for i, (s, e) in enumerate(segs):
            utt = f"{wav.stem}_{i:04d}"
            dest = segdir / (utt + ".wav")
            if not dest.exists():
                r = run(["ffmpeg", "-hide_banner", "-y", "-ss", f"{s:.3f}",
                         "-to", f"{e:.3f}", "-i", str(wav), "-map", "0:a",
                         "-c:a", "pcm_s16le", str(dest)])
                if r.returncode != 0:
                    print(f"  WARN cut failed for {utt}, skipping")
                    dropped += 1
                    continue
            rows.append({"utt_id": utt, "wav": str(dest), "dur": round(e - s, 2),
                         "src": wav.name, "start": round(s, 2), "text": ""})
            total_speech += e - s
    with open(manifest, "w") as fh:
        for r_ in rows:
            fh.write(json.dumps(r_) + "\n")
    print(f"segments: {len(rows)} kept, {dropped} failed, "
          f"{total_speech/3600:.2f}h net speech -> {manifest}")
    return manifest


def stage_transcribe(outdir, model="small.en"):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("faster-whisper not installed. Run:\n"
                 "  pip install faster-whisper   (or: uv pip install faster-whisper)\n"
                 "then re-run with --stage transcribe. (--skip-asr leaves text empty.)")
    manifest = Path(outdir) / "segments.jsonl"
    rows = [json.loads(l) for l in open(manifest) if l.strip()]
    todo = [r_ for r_ in rows if not r_.get("text")]
    print(f"transcribing {len(todo)}/{len(rows)} segments with {model} ...")
    wm = WhisperModel(model, device="auto")
    for n, r_ in enumerate(todo):
        segs, _ = wm.transcribe(r_["wav"], language="en", beam_size=5,
                                vad_filter=True)
        r_["text"] = " ".join(s.text.strip() for s in segs).strip()
        if (n + 1) % 50 == 0:
            print(f"  {n+1}/{len(todo)}")
    with open(manifest, "w") as fh:
        for r_ in rows:
            fh.write(json.dumps(r_) + "\n")
    empty = sum(1 for r_ in rows if not r_.get("text"))
    print(f"done. {len(rows)-empty} transcribed, {empty} empty (silence/no-speech).")


def stage_manifest(outdir, speaker, lang="EN"):
    manifest = Path(outdir) / "segments.jsonl"
    rows = [json.loads(l) for l in open(manifest) if l.strip()]
    voiced = [r_ for r_ in rows if r_.get("text")]
    if not voiced:
        sys.exit("no transcribed segments. Run --stage transcribe first "
                 "(or transcribe externally and fill segments.jsonl text fields).")
    # Stratified split: every 20th segment -> dev (covers all sources)
    dev = [r_ for i, r_ in enumerate(voiced) if i % 20 == 0]
    train = [r_ for i, r_ in enumerate(voiced) if i % 20 != 0]

    def write_list(path, rs):
        with open(path, "w") as fh:
            for r_ in rs:
                txt = re.sub(r"\s+", " ", r_["text"]).strip()
                fh.write(f"{r_['wav']}|{speaker}|{lang}|{txt}\n")

    write_list(Path(outdir) / "train.list", train)
    write_list(Path(outdir) / "val.list", dev)
    with open(Path(outdir) / "train.jsonl", "w") as fh:
        for r_ in train:
            fh.write(json.dumps(r_) + "\n")
    with open(Path(outdir) / "dev.jsonl", "w") as fh:
        for r_ in dev:
            fh.write(json.dumps(r_) + "\n")
    th = sum(r_["dur"] for r_ in train) / 3600
    dh = sum(r_["dur"] for r_ in dev) / 3600
    print(f"train: {len(train)} segs, {th:.2f}h -> train.list + train.jsonl")
    print(f"dev  : {len(dev)} segs, {dh:.2f}h -> val.list + dev.jsonl")
    print(f"empty/no-speech segments excluded: {len(rows)-len(voiced)}")
    print("Next: GPT-SoVITS trains directly on train.list/val.list; "
          "OmniVoice needs its tokenizer run over train/dev.jsonl on the GPU box.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="indir", required=True)
    ap.add_argument("--out", dest="outdir", required=True)
    ap.add_argument("--speaker", default="JAKE")
    ap.add_argument("--sr", type=int, default=32000,
                    help="output sample rate (32000 GPT-SoVITS, 24000 OmniVoice)")
    ap.add_argument("--stage", default="all",
                    choices=["all", "audit", "convert", "segment",
                             "transcribe", "manifest"])
    ap.add_argument("--asr-model", default="small.en")
    ap.add_argument("--skip-asr", action="store_true")
    a = ap.parse_args()
    Path(a.outdir).mkdir(parents=True, exist_ok=True)
    stages = ["audit", "convert", "segment", "transcribe", "manifest"] \
        if a.stage == "all" else [a.stage]
    if "audit" in stages:
        stage_audit(a.indir)
    if "convert" in stages:
        stage_convert(a.indir, a.outdir, a.sr)
    if "segment" in stages:
        stage_segment(a.outdir)
    if "transcribe" in stages:
        if a.skip_asr:
            print("transcribe: skipped (--skip-asr)")
        else:
            stage_transcribe(a.outdir, a.asr_model)
    if "manifest" in stages:
        if a.skip_asr and a.stage == "all":
            print("manifest: skipped (needs transcripts; re-run --stage manifest later)")
        else:
            stage_manifest(a.outdir, a.speaker)


if __name__ == "__main__":
    main()
