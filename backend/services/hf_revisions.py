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
import threading
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
            pass  # temp file already gone; the original error is re-raised below
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

# Serializes the read-compare-unlink of a clear against a concurrent mark, so a
# finishing install cannot remove the marker a newer install just wrote.
_pending_lock = threading.Lock()


def mark_install_pending(repo_id: str, revision: str, cache_dir: str) -> None:
    """Record that ``revision`` of ``repo_id`` is being downloaded."""
    if not _SHA.fullmatch(revision):
        raise ValueError("Hugging Face revision must be a 40-character commit SHA")
    repo_dir = _repo_dir(repo_id, cache_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)
    marker = repo_dir / INSTALL_PENDING_MARKER
    with _pending_lock:
        _write_marker(marker, revision)


def clear_install_pending(repo_id: str, revision: str, cache_dir: str) -> None:
    """Remove the pending marker if it still names ``revision``. Never raises.

    Callers that have just finished a download must use
    :func:`settle_install_pending` instead: a download call returning does not
    prove the revision is whole.
    """
    marker = _repo_dir(repo_id, cache_dir) / INSTALL_PENDING_MARKER
    try:
        with _pending_lock:
            if marker.read_text(encoding="ascii").strip() == revision:
                marker.unlink()
    except (OSError, UnicodeError) as exc:
        # Missing or unreadable marker: nothing to clear. Log the type only; the
        # exception text carries the absolute (home) path.
        _logger.debug("pending marker not cleared (%s)", type(exc).__name__)


def expected_files_from_plan(plan) -> "list[tuple[str, int]] | None":
    """``[(repo-relative name, size)]`` from a dry-run plan, None when unusable."""
    files = []
    for entry in plan or ():
        name = getattr(entry, "filename", None)
        if not isinstance(name, str) or not name:
            return None
        files.append((name, int(getattr(entry, "file_size", 0) or 0)))
    return files or None


def fetch_expected_files(**download_kwargs) -> "list[tuple[str, int]] | None":
    """Ask the Hub which files this revision holds (a dry run). None on any failure."""
    try:
        from huggingface_hub import snapshot_download

        kwargs = {k: v for k, v in download_kwargs.items()
                  if k not in ("force_download", "local_dir_use_symlinks", "tqdm_class", "max_workers")}
        kwargs["dry_run"] = True
        return expected_files_from_plan(list(snapshot_download(**kwargs)))  # nosec B615 -- immutable revision pin
    except Exception as exc:
        _logger.debug("expected file list unavailable (%s)", type(exc).__name__)
        return None


def revision_verified_complete(repo_dir: str, revision: str, expected_files=None) -> bool:
    """True only when every expected file of ``revision`` is present and whole.

    ``snapshot_download`` can return an existing incomplete snapshot instead of
    raising (a metadata timeout), so "the download returned" proves nothing. An
    unknown expectation is not proof either: it returns False.
    """
    if not expected_files:
        return False
    repo = Path(repo_dir)
    snapshot = repo / "snapshots" / revision
    try:
        for name, size in expected_files:
            path = snapshot / name
            actual = path.stat().st_size  # follows the link: a missing blob raises
            if actual <= 0 and size > 0:
                return False
            if size > 0 and actual != size:
                return False
        blobs = repo / "blobs"
        if blobs.is_dir() and any(n.endswith((".incomplete", ".part")) for n in os.listdir(blobs)):
            return False
    except OSError:
        return False
    return True


def _catalogue_entry(repo_id: str) -> dict:
    try:
        from services.model_catalogue import KNOWN_MODELS

        return next((m for m in KNOWN_MODELS if m.get("repo_id") == repo_id and not m.get("id")), {})
    except Exception:
        return {}


def fallback_verified_complete(repo_dir: str, revision: str, model: "dict | None" = None) -> bool:
    """Completeness proof for when the Hub's file list is unavailable.

    Weaker than :func:`revision_verified_complete`, but it still catches the
    unfinished-transfer states: no partial blob in the repo, every entry of the
    revision's snapshot resolves to a non-empty blob, a plausible weight is
    present and any weights the catalogue entry declares are all there.
    """
    from services.model_catalogue import snapshot_has_weights

    repo = Path(repo_dir)
    snapshot = repo / "snapshots" / revision
    try:
        blobs = repo / "blobs"
        if blobs.is_dir() and any(n.endswith((".incomplete", ".part")) for n in os.listdir(blobs)):
            return False
        found = False
        for root, _dirs, files in os.walk(snapshot):
            for name in files:
                found = True
                if os.stat(os.path.join(root, name)).st_size <= 0:  # a dangling link raises
                    return False
        if not found:
            return False
        if not (model or {}).get("config_only") and not snapshot_has_weights(str(snapshot)):
            return False
        for weight in (model or {}).get("known_weights") or ():
            if (snapshot / weight).stat().st_size <= 0:
                return False
    except OSError:
        return False
    return True


def settle_install_pending(repo_id: str, revision: str, cache_dir: str, *,
                           expected_files=None, fetch_kwargs=None, model=None) -> str:
    """Clear the pending marker only on proof the revision is complete.

    Returns ``"cleared"`` (verified, or nothing was pending), ``"incomplete"``
    (the Hub's file list is known and files are missing or partial) or
    ``"unverified"`` (no file list and the weaker fallback proof failed). The
    marker stays in the last two. Without a file list (mirror lacking dry-run,
    metadata timeout) :func:`fallback_verified_complete` applies, so a good
    install is not left reading as incomplete.
    """
    repo_dir = _repo_dir(repo_id, cache_dir)
    if install_pending_revision(str(repo_dir)) != revision:
        return "cleared"
    if not expected_files and fetch_kwargs is not None:
        expected_files = fetch_expected_files(**fetch_kwargs)
    failed = "incomplete"
    if expected_files:
        verified = revision_verified_complete(str(repo_dir), revision, expected_files)
    else:
        failed = "unverified"
        verified = fallback_verified_complete(
            str(repo_dir), revision, model if model is not None else _catalogue_entry(repo_id))
        _logger.info(
            "install completeness: file list unavailable, used the fallback proof (%s)",
            "complete" if verified else "incomplete",
        )
    if verified:
        clear_install_pending(repo_id, revision, cache_dir)
        return "cleared"
    return failed


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

