#!/usr/bin/env python3
"""Package the dubbing demo payload for upload as GitHub Release assets.

The demo videos and source audio are not bundled with the app; they are
fetched on demand (``backend/services/demo_assets.py``) from a pre-release of
``debpalash/omnivoice-gallery`` tagged ``dub-demo-v1``. This script turns a
directory of rendered demo files (the output of ``build_dub_demo.sh``) into:

* one release asset per mp4 / ``.src.wav`` plus a ``SHA256SUMS`` file in --out;
* optionally (--table) the pinned ``demo_assets.json`` the app verifies against;
* optionally (--posters) a small jpg still per clip, committed with the app so
  the card has an offline fallback.

Nothing is uploaded; the maintainer uploads the files in --out:

    gh release create dub-demo-v1 --repo debpalash/omnivoice-gallery \
        --prerelease --title "Dubbing demo v1" /tmp/dub-demo-v1/*
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SRC = REPO / "backend" / "assets" / "samples" / "demo" / "dubbing"
TAG = "dub-demo-v1"
CACHE_DIR = "dubbing-v1"
POSTER_WIDTH = 480


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_files(src: Path) -> list[Path]:
    return sorted([*src.glob("*.mp4"), *src.glob("*.src.wav")], key=lambda p: p.name)


def make_poster(video: Path, dest: Path) -> None:
    """One frame ~3s in, scaled down, quality lowered until it is under 40 KB."""
    for quality in (6, 8, 11, 14, 18, 24):
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", "3", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale={POSTER_WIDTH}:-2",
             "-q:v", str(quality), str(dest)],
            check=True,
        )
        if dest.stat().st_size < 40_000:
            return
    raise SystemExit(f"could not get {dest.name} under 40 KB")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="directory holding the rendered demo files")
    ap.add_argument("--out", type=Path, required=True, help="output directory for the release assets")
    ap.add_argument("--table", type=Path, help="write the pinned demo_assets.json here")
    ap.add_argument("--posters", type=Path, help="write <name>.jpg stills here")
    args = ap.parse_args()

    files = payload_files(args.src)
    if not files:
        print(f"no mp4/.src.wav files in {args.src}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    entries = []
    for path in files:
        dest = args.out / path.name
        if path.resolve() != dest.resolve():
            shutil.copyfile(path, dest)
        entries.append({
            "name": path.name,
            "sha256": sha256_of(dest),
            "bytes": dest.stat().st_size,
            # Videos are what the Download button fetches; the .src.wav files
            # are dev/test fixtures only and are never downloaded by the app.
            "kind": "video" if path.suffix == ".mp4" else "dev",
        })
        if args.posters and path.suffix == ".mp4":
            args.posters.mkdir(parents=True, exist_ok=True)
            make_poster(path, args.posters / f"{path.stem}.jpg")
    (args.out / "SHA256SUMS").write_text(
        "".join(f"{e['sha256']}  {e['name']}\n" for e in entries), encoding="utf-8"
    )
    if args.table:
        args.table.write_text(
            json.dumps({"tag": TAG, "cache_dir": CACHE_DIR, "files": entries}, indent=2) + "\n",
            encoding="utf-8",
        )
    for e in entries:
        print(f"{e['sha256']}  {e['bytes']:>9}  {e['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
