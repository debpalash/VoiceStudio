"""Immutable Hugging Face revisions for VoiceStudio's curated repositories.

Branch names are mutable supply-chain inputs. Every repo the product offers is
resolved here to a reviewed commit SHA; download, preflight, and repair paths
must call :func:`revision_for` instead of following ``main``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_SHA = re.compile(r"[0-9a-f]{40}\Z")

# Runtime pins and legal evidence share one source, with separate revisions.
# Changing evidence must never silently change the runtime download identity.
def _curated_revisions() -> dict[str, str]:
    import json

    path = Path(__file__).resolve().parents[1] / "config" / "model_licenses.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    pins = {}
    for record in registry["models"]:
        if record.get("runtime_pin_scope") != "central":
            continue
        revision = record.get("runtime_revision")
        if not isinstance(revision, str) or not _SHA.fullmatch(revision):
            raise ValueError(f"Invalid runtime revision for {record['id']!r}")
        if record["id"] in pins:
            raise ValueError("Duplicate runtime model identity")
        pins[record["id"]] = revision
    return pins


CURATED_REVISIONS: dict[str, str] = _curated_revisions()


def revision_for(repo_id: str) -> str:
    """Return the immutable revision for a curated repo, or raise."""
    try:
        return CURATED_REVISIONS[repo_id]
    except KeyError as exc:
        raise ValueError(f"No reviewed revision is pinned for {repo_id!r}") from exc


def _repo_dir(repo_id: str, cache_dir: str) -> Path:
    return Path(cache_dir) / ("models--" + repo_id.replace("/", "--"))


def remember_revision(repo_id: str, revision: str, cache_dir: str) -> None:
    """Persist the exact installed revision for later in-place repair."""
    if not _SHA.fullmatch(revision):
        raise ValueError("Hugging Face revision must be a 40-character commit SHA")
    repo_dir = _repo_dir(repo_id, cache_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)
    marker = repo_dir / "voicestudio-revision"
    temporary = marker.with_suffix(f".tmp-{os.getpid()}")
    temporary.write_text(revision + "\n", encoding="ascii")
    os.replace(temporary, marker)


def installed_revision(repo_id: str, cache_dir: str) -> str:
    """Return VoiceStudio's recorded revision, falling back to the curated pin."""
    # Authenticate the repository before consulting attacker-writable cache
    # metadata.  A syntactically valid marker must never authorize repair of a
    # repository outside VoiceStudio's reviewed catalog.
    curated_revision = revision_for(repo_id)
    repo_dir = _repo_dir(repo_id, cache_dir)
    # New installs write the first marker. ``refs/main`` preserves the commit
    # resolved by older VoiceStudio/huggingface_hub installs, so upgrades repair
    # the bytes the user actually installed rather than silently changing them.
    for marker in (repo_dir / "voicestudio-revision", repo_dir / "refs" / "main"):
        try:
            revision = marker.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            continue  # unreadable or corrupt marker: try the next, then the pin
        if _SHA.fullmatch(revision):
            return revision
    return curated_revision

