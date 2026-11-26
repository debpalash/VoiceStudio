"""Immutable auditions of completed speech; never used as published-track cache."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

_TOKEN = re.compile(r"^[a-f0-9]{64}$")
_RENDER = re.compile(r"^[a-f0-9]{32}$")


def preview_path(job_dir: str, render_id: str, token: str) -> str:
    # Exact allowlists first; then the normalized path must stay inside the
    # job's preview folder (abspath + startswith: the containment proof).
    if not _RENDER.fullmatch(render_id) or not _TOKEN.fullmatch(token):
        raise ValueError("Invalid speech preview")
    root = os.path.abspath(os.path.join(job_dir, ".speech-previews"))
    path = os.path.abspath(os.path.join(root, render_id, token + ".wav"))
    if not path.startswith(root + os.sep):
        raise ValueError("Invalid speech preview")
    return path


def prune_previews(job_dir: str, keep: int = 4) -> int:
    """Drop all but the ``keep`` most recent renders' auditions.

    Every render gets a fresh directory and its links keep superseded takes
    alive, so without pruning a re-rendered job grows on disk forever.
    Recent renders (including one still running) keep their URLs playable.
    """
    root = os.path.join(job_dir, ".speech-previews")
    try:
        entries = [entry for entry in os.scandir(root)
                   if _RENDER.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False)]
    except OSError:
        return 0
    entries.sort(key=lambda entry: entry.stat(follow_symlinks=False).st_mtime_ns, reverse=True)
    removed = 0
    for entry in entries[keep:]:
        shutil.rmtree(entry.path, ignore_errors=True)
        removed += 1
    return removed


def save_preview(job_dir: str, render_id: str, segment_id: str, source: str) -> str:
    token = hashlib.sha256(str(segment_id).encode()).hexdigest()
    destination = preview_path(job_dir, render_id, token)
    # The job must still exist: deleting a session never recreates its data.
    if not os.path.isdir(job_dir):
        raise FileNotFoundError(job_dir)
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)
    return token
