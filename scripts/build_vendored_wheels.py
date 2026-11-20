#!/usr/bin/env python3
"""Rebuild, or verify, the wheels vendored under ``vendor/wheels/`` (#4253).

Each wheel is built from the PyPI sdist recorded in ``vendor/wheels/SOURCES.json``
(URL plus sha256, copied from the former ``uv.lock`` entry) with the build
backend pinned in ``vendor/wheels/build-constraints.txt``.

    uv run python scripts/build_vendored_wheels.py            # rebuild all wheels
    uv run python scripts/build_vendored_wheels.py --verify   # rebuild to a temp dir and
                                                              # compare every file's content

After a rebuild run ``uv lock`` so the wheel hashes in ``uv.lock`` follow.
Downloads only the sdist URLs listed in SOURCES.json (the URLs ``uv.lock`` used
to pin); nothing else.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHEELS = ROOT / "vendor" / "wheels"
SOURCES = WHEELS / "SOURCES.json"
CONSTRAINTS = WHEELS / "build-constraints.txt"


def _build(entry: dict, work: Path, out: Path) -> Path:
    sdist = work / entry["sdist_url"].rsplit("/", 1)[1]
    with urllib.request.urlopen(entry["sdist_url"], timeout=60) as response:  # noqa: S310 - pinned https URL
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry["sdist_sha256"]:
        raise SystemExit(f"{entry['name']}: sdist sha256 {digest} != pinned {entry['sdist_sha256']}")
    sdist.write_bytes(data)
    src = work / entry["name"]
    with tarfile.open(sdist) as tar:
        tar.extractall(src, filter="data")
    (project,) = [p for p in src.iterdir() if p.is_dir()]
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out), "--build-constraints", str(CONSTRAINTS), str(project)],
        check=True,
    )
    (wheel,) = out.glob(f"{entry['name'].replace('-', '_')}-{entry['version']}-*.whl")
    return wheel


def _contents(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist() if not n.endswith("/RECORD")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true", help="compare to the committed wheels, change nothing")
    args = parser.parse_args()
    entries = json.loads(SOURCES.read_text(encoding="utf-8"))["wheels"]
    bad = 0
    for entry in entries:
        with tempfile.TemporaryDirectory() as tmp:
            work, out = Path(tmp, "work"), Path(tmp, "out")
            work.mkdir()
            built = _build(entry, work, out)
            target = WHEELS / entry["wheel"]
            if args.verify:
                same = _contents(built) == _contents(target)
                print(f"{entry['name']}: {'identical contents' if same else 'DIFFERS'}")
                bad += not same
            else:
                if built.name != entry["wheel"]:
                    raise SystemExit(f"built {built.name}, SOURCES.json expects {entry['wheel']}")
                target.write_bytes(built.read_bytes())
                print(f"wrote {target.relative_to(ROOT)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
