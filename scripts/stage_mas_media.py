"""Acquire pinned media binaries at build time for private MAS validation.

This command requires --download explicitly. It does not grant redistribution
clearance; the shared compliance gate remains mandatory before distribution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


def stage_media(source: Path, output: Path, archive: dict) -> dict:
    if output.exists():
        raise FileExistsError(f"Refusing to replace existing media tools: {output}")
    for name in ("ffmpeg", "ffprobe"):
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing regular bundled media executable: {name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".mas-media-", dir=output.parent))
    try:
        files = []
        for name in ("ffmpeg", "ffprobe"):
            target = scratch / name
            shutil.copy2(source / name, target)
            target.chmod(0o755)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            files.append({"file": name, "sha256": digest, "sourceSha256": digest})
        provenance = {"schemaVersion": 1, "archive": archive, "files": files,
                      "redistributionApproved": False}
        (scratch / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        scratch.rename(output)
        return provenance
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("MAS media staging requires a macOS worker")
    if args.output.exists():
        raise FileExistsError(f"Refusing to replace existing media tools: {args.output}")
    # A new private cache forces the existing acquisition path to verify the
    # pinned archive instead of trusting a previously writable tool cache.
    with tempfile.TemporaryDirectory(prefix="voicestudio-mas-media-") as cache:
        os.environ["OMNIVOICE_DATA_DIR"] = cache
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
        from services import media_tools
        result = media_tools.acquire_bundled(wait=True)
        if result["state"] != "done":
            raise RuntimeError("Pinned media acquisition did not complete")
        url, digest, size = media_tools._expected_bundle()
        provenance = stage_media(Path(media_tools.bundled_dir()), args.output,
                                 {"url": url, "sha256": digest, "bytes": size})
    print(json.dumps({"files": len(provenance["files"]), "redistributionApproved": False}))


if __name__ == "__main__":
    main()
