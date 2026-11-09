"""Immutable Hugging Face revisions for VoiceStudio's curated repositories.

Branch names are mutable supply-chain inputs. Every repo the product offers is
resolved here to a reviewed commit SHA; download, preflight, and repair paths
must call :func:`revision_for` instead of following ``main``.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

_logger = logging.getLogger("omnivoice.hf_revisions")
_SHA = re.compile(r"[0-9a-f]{40}\Z")

# Runtime pins and legal evidence share one source, with separate revisions.
# Changing evidence must never silently change the runtime download identity.
CONFIG_PATH: Path = Path(__file__).resolve().parents[1] / "config" / "model_licenses.json"


def _curated_revisions(config_path: Path = CONFIG_PATH) -> dict[str, str]:
    try:
        registry = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        # Mirrors the marker-fallback policy from 1f28c05: an unreadable or
        # corrupt curated registry must not prevent the backend from coming
        # up. ``revision_for`` raises a clear ``ValueError`` for an unpinned
        # repo, so the first download that depends on curation fails
        # loudly with a useful diagnostic instead of crashing the import.
        _logger.warning(
            "Curated HF revision registry %s is unreadable or corrupt (%s); "
            "treating every repo as unpinned until the file is repaired.",
            config_path.name, type(exc).__name__,
        )
        return {}
    models = registry.get("models") if isinstance(registry, dict) else None
    if not isinstance(models, list):
        _logger.warning(
            "Curated HF revision registry %s has an unexpected shape; "
            "treating every repo as unpinned until the file is repaired.",
            config_path.name,
        )
        return {}
    pins = {}
    for record in models:
        if not isinstance(record, dict):
            continue
        if record.get("runtime_pin_scope") != "central":
            continue
        revision = record.get("runtime_revision")
        if not isinstance(revision, str) or not _SHA.fullmatch(revision):
            raise ValueError(f"Invalid runtime revision for {record.get('id')!r}")
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

