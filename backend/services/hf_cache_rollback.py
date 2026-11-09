"""Roll a Hugging Face cache repository back after a cancelled download.

A cancelled install must not leave partial data behind, but the same cache
folder can already hold files the user downloaded earlier (another revision,
another catalogue entry using the same repository, or a previous complete
install). :func:`capture` records what existed before the download started;
:func:`rollback` removes only what the download added since then, plus any
partial transfer file (``*.incomplete`` from huggingface_hub, ``*.part`` and
its resume manifest from the segmented downloader), and restores the small
revision markers the download may have rewritten.

Deletes are retried because Windows refuses to remove a file another handle
still holds open; whatever cannot be removed is reported, not raised.
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

logger = logging.getLogger("omnivoice.hf_cache_rollback")

# Never usable as complete data: a transfer that was interrupted.
_PARTIAL_SUFFIXES = (".incomplete", ".part", ".part.done", ".part.done.tmp")
# Small text markers a download rewrites in place; restored byte for byte.
_MARKER_LIMIT = 4096
_DELETE_ROUNDS = 6
_DELETE_DELAY_S = 0.25


def _repo_folder(repo_id: str) -> str:
    return "models--" + repo_id.replace("/", "--")


def _is_partial(name: str) -> bool:
    return name.endswith(_PARTIAL_SUFFIXES)


@dataclass
class _RepoState:
    roots: dict[str, bool] = field(default_factory=dict)  # root -> existed
    entries: dict[str, set[str]] = field(default_factory=dict)  # root -> rel paths
    markers: dict[str, bytes] = field(default_factory=dict)  # abs path -> bytes


@dataclass
class CacheSnapshot:
    cache_dir: str
    repos: dict[str, _RepoState] = field(default_factory=dict)


def _walk(root: str) -> set[str]:
    """Every file, link and directory under ``root`` (relative, unfollowed)."""
    found: set[str] = set()
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            found.add(os.path.relpath(os.path.join(current, name), root))
    return found


def _roots(cache_dir: str, repo_id: str) -> list[str]:
    folder = _repo_folder(repo_id)
    return [os.path.join(cache_dir, folder), os.path.join(cache_dir, ".locks", folder)]


def capture(cache_dir: str, repo_ids: Iterable[str]) -> CacheSnapshot:
    """Record the cache state of ``repo_ids`` before a download writes to it."""
    snapshot = CacheSnapshot(cache_dir=str(cache_dir))
    for repo_id in dict.fromkeys(repo_ids):
        state = _RepoState()
        for root in _roots(snapshot.cache_dir, repo_id):
            existed = os.path.isdir(root)
            state.roots[root] = existed
            state.entries[root] = _walk(root) if existed else set()
        repo_dir = _roots(snapshot.cache_dir, repo_id)[0]
        for rel in state.entries[repo_dir]:
            if rel == "voicestudio-revision" or rel.startswith("refs" + os.sep):
                path = os.path.join(repo_dir, rel)
                try:
                    if os.path.isfile(path) and os.path.getsize(path) <= _MARKER_LIMIT:
                        with open(path, "rb") as handle:
                            state.markers[path] = handle.read()
                except OSError:
                    continue
        snapshot.repos[repo_id] = state
    return snapshot


def _remove(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        def _writable(func: Callable, target: str, _exc) -> None:
            os.chmod(target, stat.S_IWRITE)
            func(target)

        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_writable)
        else:
            shutil.rmtree(path, onerror=_writable)
        return
    try:
        os.remove(path)
    except PermissionError:
        # Read-only files (Windows) cannot be removed until made writable.
        os.chmod(path, stat.S_IWRITE)
        os.remove(path)


def _delete_all(paths: list[str], sleep: Callable[[float], None]) -> list[str]:
    """Delete ``paths`` in rounds; Windows may hold a file open briefly."""
    remaining = list(paths)
    for round_index in range(_DELETE_ROUNDS):
        failed: list[str] = []
        for path in remaining:
            if not os.path.lexists(path):
                continue
            try:
                _remove(path)
            except FileNotFoundError:
                continue
            except OSError:
                failed.append(path)
        remaining = failed
        if not remaining:
            return []
        if round_index + 1 < _DELETE_ROUNDS:
            sleep(_DELETE_DELAY_S * (round_index + 1))
    return remaining


def rollback(
    snapshot: CacheSnapshot,
    *,
    skip: Iterable[str] = (),
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Remove what the download added since :func:`capture`.

    ``skip`` names repositories another active install is writing to; they
    are left untouched. Returns the paths that could not be removed.
    """
    skipped = set(skip)
    leftover: list[str] = []
    for repo_id, state in snapshot.repos.items():
        if repo_id in skipped:
            continue
        doomed: list[str] = []
        for root, existed in state.roots.items():
            if not os.path.isdir(root):
                continue
            if not existed:
                doomed.append(root)
                continue
            before = state.entries[root]
            for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
                rel_current = os.path.relpath(current, root)
                keep_dirs = []
                for name in dirs:
                    rel = os.path.normpath(os.path.join(rel_current, name))
                    path = os.path.join(current, name)
                    if rel not in before:
                        doomed.append(path)
                    elif not os.path.islink(path):
                        keep_dirs.append(name)
                    # A directory link that existed before (weights moved to
                    # another drive) is kept as is and never traversed.
                dirs[:] = keep_dirs
                for name in files:
                    rel = os.path.normpath(os.path.join(rel_current, name))
                    if rel not in before or _is_partial(name):
                        doomed.append(os.path.join(current, name))
        leftover.extend(_delete_all(doomed, sleep))
        for path, content in state.markers.items():
            try:
                with open(path, "rb") as handle:
                    if handle.read() == content:
                        continue
            except OSError:
                pass  # Missing or unreadable marker: rewrite it below.
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                temporary = f"{path}.rollback-{os.getpid()}"
                with open(temporary, "wb") as handle:
                    handle.write(content)
                os.replace(temporary, path)
            except OSError:
                leftover.append(path)
    if leftover:
        logger.warning(
            "cancelled download cleanup left %d path(s) that were still in use",
            len(leftover),
        )
    return leftover
