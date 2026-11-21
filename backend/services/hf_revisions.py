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
import tempfile
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


def _write_marker(marker: Path, revision: str) -> None:
    """Atomically write ``marker`` via a unique temp file in the same directory."""
    fd, name = tempfile.mkstemp(dir=marker.parent, prefix=marker.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(revision + "\n")
        os.replace(name, marker)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def remember_revision(repo_id: str, revision: str, cache_dir: str) -> None:
    """Persist the exact installed revision for later in-place repair."""
    if not _SHA.fullmatch(revision):
        raise ValueError("Hugging Face revision must be a 40-character commit SHA")
    repo_dir = _repo_dir(repo_id, cache_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)
    marker = repo_dir / "voicestudio-revision"
    _write_marker(marker, revision)


# Names the revision an install is still downloading. Written before the first
# byte and removed once the download returns with every file, so a snapshot of
# that revision that already holds *some* weight (a finished tokenizer while the
# main weight is still transferring, or after a crash) is not reported as
# installed. Other revisions in the same repository are unaffected.
INSTALL_PENDING_MARKER = "voicestudio-installing"


def mark_install_pending(repo_id: str, revision: str, cache_dir: str) -> None:
    """Record that ``revision`` of ``repo_id`` is being downloaded."""
    if not _SHA.fullmatch(revision):
        raise ValueError("Hugging Face revision must be a 40-character commit SHA")
    repo_dir = _repo_dir(repo_id, cache_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)
    marker = repo_dir / INSTALL_PENDING_MARKER
    _write_marker(marker, revision)


def clear_install_pending(repo_id: str, revision: str, cache_dir: str) -> None:
    """Remove the pending marker if it still names ``revision``. Never raises."""
    marker = _repo_dir(repo_id, cache_dir) / INSTALL_PENDING_MARKER
    try:
        if marker.read_text(encoding="ascii").strip() == revision:
            marker.unlink()
    except (OSError, UnicodeError):
        pass


def install_pending_revision(repo_dir: str) -> "str | None":
    """Revision an install of the repository at ``repo_dir`` has not finished."""
    try:
        revision = (Path(repo_dir) / INSTALL_PENDING_MARKER).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    return revision if _SHA.fullmatch(revision) else None


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

