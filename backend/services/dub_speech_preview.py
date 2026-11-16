"""Immutable auditions of completed speech; never used as published-track cache."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

from core.path_security import contained_join

_TOKEN = re.compile(r"^[a-f0-9]{64}$")
_RENDER = re.compile(r"^[a-f0-9]{32}$")


def preview_path(job_dir: str, render_id: str, token: str) -> str:
    if not _RENDER.fullmatch(render_id) or not _TOKEN.fullmatch(token):
        raise ValueError("Invalid speech preview")
    path = contained_join(job_dir, os.path.join(".speech-previews", render_id, token + ".wav"))
    if path is None:
        raise ValueError("Invalid speech preview")
    return path


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
