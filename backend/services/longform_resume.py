"""Durable resume for longform (audiobook / story) renders.

Chapter WAVs are already content-addressed in a shared cache, so re-rendering an
identical plan reuses what finished — the synthesis-level resume. The missing
piece this module adds is **durability of the *plan itself***: a render that's
interrupted (crash, app quit, power loss) leaves a `resume.json` manifest in the
job's work dir holding the compiled plan + render params, so the job can be
resumed later *without the user still having the original script* — which matters
for Stories, whose plan is compiled from cast+lines and can't be retyped.

Pure file/JSON I/O (no torch, no model) so it's unit-testable. The router wires
it into the SSE renderer (write on start, clear on done) and exposes
``GET /audiobook/jobs`` (resumable) + ``POST /audiobook/resume/{job_id}``.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from core.durable_io import flush_dir, flush_fd

MANIFEST_VERSION = 1
_MANIFEST_NAME = "resume.json"
# Longform front doors that produce a resumable work dir (job_type → dir prefix).
RESUMABLE_TYPES = ("audiobook", "story")
# A job id is server-generated (uuid4 hex) — confine to a strict token so a
# request-supplied id (the /audiobook/resume/{job_id} path param) can never
# carry a path separator, `..`, NUL, or anything that escapes OUTPUTS_DIR
# (CodeQL py/path-injection). Anchored, single bounded quantifier → ReDoS-safe.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Exact-match allowlist on the WHOLE work-dir name (the value actually joined
# onto OUTPUTS_DIR). Validating the joined string itself — not just job_id — is
# the barrier CodeQL's path-injection query recognizes (same shape as
# audiobook._safe_cover_path's _COVER_NAME_RE). Mirrors RESUMABLE_TYPES.
_SAFE_SEG_RE = re.compile(r"^(?:audiobook|story)_[A-Za-z0-9_-]{1,64}$")


def work_dir(job_type: str, job_id: str) -> Optional[str]:
    """The per-job work directory ``OUTPUTS_DIR/<job_type>_<job_id>``, resolved
    strictly inside OUTPUTS_DIR. Returns None for an unknown ``job_type``, an
    id that isn't a bare safe token, or any path that escapes OUTPUTS_DIR — so a
    crafted ``job_id`` can never reach a foreign path (py/path-injection-safe)."""
    if job_type not in RESUMABLE_TYPES or not _SAFE_ID_RE.match(job_id or ""):
        return None
    # os.path.basename strips any directory component (the sanitizer CodeQL's
    # path-injection query recognizes — same as audiobook._safe_cover_path), and
    # the exact-match allowlist on the result is a second barrier: the joined
    # value is provably a single bare dir name of the expected shape.
    seg = os.path.basename(f"{job_type}_{job_id}")
    if not _SAFE_SEG_RE.match(seg):
        return None
    from core.config import OUTPUTS_DIR
    root = os.path.realpath(OUTPUTS_DIR)
    path = os.path.realpath(os.path.join(root, seg))
    # commonpath containment — the form static analysis recognizes (belt over
    # the regex). Raises ValueError on mixed drives (Windows) → reject.
    try:
        if os.path.commonpath([path, root]) != root:
            return None
    except ValueError:
        return None
    return path


def manifest_path(job_type: str, job_id: str) -> Optional[str]:
    d = work_dir(job_type, job_id)
    return os.path.join(d, _MANIFEST_NAME) if d else None


def build_manifest(
    *,
    job_id: str,
    job_type: str,
    plan_chapters: list[dict],
    params: dict,
    title: str = "",
) -> dict:
    """Assemble the manifest dict. ``plan_chapters`` is the canonical span-plan
    (``[{title, spans:[{voice_id,text,pause_ms_after,speed}]}]``); ``params`` is
    the render kwargs (default_voice / fmt / bitrate / loudness / cover_path /
    metadata / lexicon). Pure — no I/O."""
    return {
        "version": MANIFEST_VERSION,
        "job_id": job_id,
        "job_type": job_type,
        "title": title or "",
        "total_chapters": len(plan_chapters),
        "params": params,
        "plan": plan_chapters,
    }


def write_manifest(manifest: dict) -> Optional[str]:
    """Persist the manifest to the job work dir. Best-effort — resume is an
    enhancement, never block the render — returns the path or None on failure /
    unsafe id."""
    path = manifest_path(manifest.get("job_type", ""), manifest.get("job_id", ""))
    if path is None:
        return None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False)
            # Flushed before the rename: without it a power-off can publish
            # the name ahead of the data and leave an empty manifest, which is
            # how an interrupted job stopped being resumable (#2279).
            f.flush()
            flush_fd(f.fileno())
        os.replace(tmp, path)  # atomic — a half-written manifest never resumes
        flush_dir(os.path.dirname(path))
        return path
    except OSError:
        return None


def read_manifest(job_type: str, job_id: str) -> Optional[dict]:
    """Load a job's resume manifest, or None if absent/unreadable/foreign-shape/
    unsafe id."""
    path = manifest_path(job_type, job_id)
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        return None
    if not isinstance(data.get("plan"), list):
        return None
    return data


def clear_manifest(job_type: str, job_id: str) -> None:
    """Remove the manifest once a job completes (no resume needed). Best-effort."""
    path = manifest_path(job_type, job_id)
    if path is None:
        return
    try:
        os.remove(path)
    except OSError:
        return  # best-effort; already gone or unwritable


def has_manifest(job_type: str, job_id: str) -> bool:
    path = manifest_path(job_type, job_id)
    return bool(path) and os.path.isfile(path)


def scan_resumable() -> list[dict]:
    """Enumerate resumable jobs by scanning OUTPUTS_DIR for ``<type>_<id>`` work
    dirs that hold a ``resume.json``.

    Returns ``[{"job_type", "job_id", "manifest_path"}, …]`` where **every path
    component is sourced from os.listdir** (the trusted filesystem), never from
    request input. The ``manifest_path`` is built here from the listed dir name,
    so callers can read it directly without re-deriving a path from a
    request-supplied id (CodeQL py/path-injection-safe — no tainted value ever
    reaches a file operation)."""
    from core.config import OUTPUTS_DIR
    out: list[dict] = []
    try:
        root = os.path.realpath(OUTPUTS_DIR)
        names = os.listdir(root)
    except OSError:
        return out
    for name in names:
        for jt in RESUMABLE_TYPES:
            prefix = f"{jt}_"
            mpath = os.path.join(root, name, _MANIFEST_NAME)
            if name.startswith(prefix) and os.path.isfile(mpath):
                out.append({"job_type": jt, "job_id": name[len(prefix):],
                            "manifest_path": mpath})
    return out


def discard_manifest_file(path: str) -> None:
    """Remove a manifest by a path obtained via :func:`scan_resumable` (trusted,
    os.listdir-derived). Best-effort — used to retire an interrupted job once
    it's been resumed under a fresh id."""
    try:
        os.remove(path)
    except OSError:
        return


def load_manifest_file(path: str) -> Optional[dict]:
    """Read + validate a manifest from a path obtained via :func:`scan_resumable`
    (a trusted, os.listdir-derived path — NOT a request-derived one). Returns
    None on missing/unreadable/foreign-shape."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        return None
    if not isinstance(data.get("plan"), list):
        return None
    return data
