#!/usr/bin/env python3
"""Render the dubbing demo's five audio tracks with the VoiceStudio engine.

`scripts/build_dub_demo.sh` builds the videos, subtitles and manifest, but it
got its audio from macOS `say` — so the demo could only be built on a Mac, and
on every other platform the Dub workspace's demo player pointed at files that
were never generated. The engine runs wherever the app does, which makes it the
portable answer, and it has the side benefit of the demo being rendered by the
thing it is demonstrating.

Writes `source.src.wav` + `dubbed_<code>.src.wav` next to where the videos will
be built; `build_dub_demo.sh` picks those up automatically and falls back to
`say` when they are absent.

Prerequisites are the same as scripts/render_demos_omnivoice.py: the project
venv and cached model weights.

Usage:
    python3 scripts/render_dub_demo_audio.py [--skip-existing]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
# Must match OUT_DIR in build_dub_demo.sh, which must in turn sit under the
# directory main.py mounts at /demo_audio (backend/assets/samples).
OUT_DIR = BACKEND_DIR / "assets" / "samples" / "demo" / "dubbing"
SCRIPTS_JSON = REPO_ROOT / "scripts" / "dub_demo_scripts.json"

sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from render_demos_omnivoice import watermark_file  # noqa: E402 — needs sys.path above

# The videos are built at 44.1 kHz; rendering straight to it saves the shell
# script a resample step and keeps every track at one rate.
VIDEO_SAMPLE_RATE = 44100


def _render(model, text: str, language: str, instruct: str, out: Path, sample_rate: int) -> None:
    """Synthesize one track and write it at the video pipeline's sample rate."""
    import torch
    import torchaudio

    audios = model.generate(text=text, instruct=instruct, language=language, num_step=32)
    audio = audios[0]
    if audio.dim() == 1:
        audio = audio.unsqueeze(0)
    if audio.shape[0] > 1:
        audio = audio.mean(dim=0, keepdim=True)
    peak = audio.abs().max().item()
    if peak > 0:
        audio = audio / peak * 0.97
    out.parent.mkdir(parents=True, exist_ok=True)
    audio = audio.to(torch.float32).cpu()
    if sample_rate != VIDEO_SAMPLE_RATE:
        audio = torchaudio.functional.resample(audio, sample_rate, VIDEO_SAMPLE_RATE)
    from services.audio_io import _safe_torchaudio_save

    _safe_torchaudio_save(str(out), audio, VIDEO_SAMPLE_RATE, bits_per_sample=16)
    # Level-match the tracks: a dub demo where switching language also changes
    # the volume reads as a bug in the dubbing, not in the demo assets. See
    # render_demos_omnivoice.py for why the output rate has to be pinned.
    tmp = out.with_suffix(".norm.wav")
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(out),
            "-af", "loudnorm=I=-18:TP=-1.5:LRA=11",
            "-ar", str(VIDEO_SAMPLE_RATE), "-ac", "1",
            "-c:a", "pcm_s16le", str(tmp),
        ],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and tmp.exists() and tmp.stat().st_size:
        tmp.replace(out)
    else:
        tmp.unlink(missing_ok=True)
        print(f"  ! loudnorm skipped for {out.name}: {result.stderr.strip()[:100]}")
    # These tracks are muxed into a video that ships in the app, so they carry
    # the same provenance mark as any other synthetic audio the app produces
    # (#1169). Last step, after loudnorm — see watermark_file.
    watermark_file(out, VIDEO_SAMPLE_RATE, context=f"demo:dub:{out.stem}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Don't re-render tracks that already exist on disk.",
    )
    args = parser.parse_args()

    spec = json.loads(SCRIPTS_JSON.read_text(encoding="utf-8"))
    instruct = spec["voice_instruct"]
    tracks = [("source", spec["source"])] + [
        (f"dubbed_{entry['code']}", entry) for entry in spec["dubbed"]
    ]

    print("Loading VoiceStudio engine (this can take 30-60 s on first run)…")
    try:
        import asyncio

        from services.model_manager import get_model

        model = asyncio.run(get_model())
    except Exception as exc:  # noqa: BLE001 - the message is the whole point
        print(f"\nERROR: Could not load VoiceStudio engine: {exc}")
        print("Run `uv sync` first; weights download on first synthesis (~5 GB).")
        sys.exit(1)
    sample_rate = getattr(model, "sampling_rate", 24000)
    print("Engine loaded.\n")

    for stem, entry in tracks:
        out = OUT_DIR / f"{stem}.src.wav"
        if args.skip_existing and out.exists():
            print(f"  · skip (exists): {out.name}")
            continue
        _render(model, entry["script"], entry["language"], instruct, out, sample_rate)
        print(f"  ✓ {out.name} ({entry['label']})")

    print("\nDone. Now run scripts/build_dub_demo.sh to build the videos.")


if __name__ == "__main__":
    main()
