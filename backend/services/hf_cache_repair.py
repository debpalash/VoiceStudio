"""Self-heal for HF cache snapshots whose entries no longer resolve.

The Hugging Face hub cache stores each file's bytes once under
``models--<org>--<name>/blobs/<hash>`` and exposes every revision as
``snapshots/<rev>/<filename>`` entries that link into ``blobs/``. Several
real-world events leave a snapshot entry *broken* — a dangling symlink (its
blob target doesn't exist) or a zero-byte stand-in file — while the actual
bytes are safely on disk under ``blobs/``: a blob-naming mismatch between
download modes, an interrupted rename mid-download, antivirus interference.

``os.path.isfile()`` on a dangling symlink is False, so transformers concludes
the weights are missing ("… does not appear to have a file named
pytorch_model.bin or model.safetensors") even though the multi-GB download
completed. A plain ``snapshot_download`` doesn't reliably fix this — depending
on hub version and platform symlink support, the existing-but-broken entry can
short-circuit the restore. Deleting exactly the broken entries first makes
``snapshot_download`` deterministically restore them (reusing completed blobs
where the naming matches, re-downloading only where it doesn't).

Conservative by design, repairing STATE rather than chasing one cause:
  * never touches ``blobs/`` (the downloaded bytes),
  * never touches snapshot entries that resolve,
  * never force-redownloads healthy files,
  * never raises — any internal failure logs and returns a summary,
  * a healthy cache is a cheap lstat/stat walk of ``snapshots/`` (no hashing,
    no network) on every platform; the heal is generic, not Windows-gated.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("omnivoice.hf_cache_repair")

# Snapshot entries that are never legitimately zero bytes: weight formats and
# JSON/sentencepiece config-tokenizer files (an empty file is not valid JSON /
# not a valid serialized model). Zero-byte files with any OTHER suffix — an
# empty .txt, .md, .gitattributes, a marker file a repo genuinely ships empty —
# are left alone: when unsure, don't flag.
_NEVER_EMPTY_SUFFIXES = frozenset({
    # weights / tensors
    ".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".onnx", ".gguf",
    ".msgpack", ".h5", ".pb", ".tflite",
    # config / tokenizer
    ".json", ".model", ".spm",
})


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def hf_cache_home() -> str:
    """The hub cache root in effect. Mirrors huggingface_hub's resolution
    (``HF_HUB_CACHE`` > ``HF_HOME``/hub > default) but reads the env at call
    time — hub's constants freeze at import, which is too early for tests and
    for the Windows short-cache redirect in ``core.config``."""
    env = (os.environ.get("HF_HUB_CACHE") or "").strip()
    if env:
        return env
    hf_home = (os.environ.get("HF_HOME") or "").strip()
    if hf_home:
        return os.path.join(hf_home, "hub")
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        return HF_HUB_CACHE
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")


def repo_cache_dir(repo_id: str, cache_dir: str | None = None) -> str:
    """The ``models--<org>--<name>`` folder for ``repo_id`` (repo_type=model)."""
    return os.path.join(cache_dir or hf_cache_home(),
                        "models--" + repo_id.replace("/", "--"))


def _is_dangling_symlink(path: str) -> bool:
    # islink() uses lstat (True even when the target is gone); exists()
    # resolves the link — False for a dangling one. Never raises for a path
    # that came out of os.walk.
    return os.path.islink(path) and not os.path.exists(path)


def _is_suspicious_zero_byte(path: str) -> bool:
    """A zero-byte REGULAR file standing where model content must be.

    Conservative: only weight/config-typed names are flagged (those are never
    legitimately empty — the bytes to restore them live in ``blobs/`` or on
    the Hub); anything else is presumed intentional and left alone."""
    if os.path.islink(path):
        return False  # resolving symlinks are handled by the dangling check
    try:
        if not os.path.isfile(path) or os.path.getsize(path) != 0:
            return False
    except OSError:
        return False
    return os.path.splitext(path)[1].lower() in _NEVER_EMPTY_SUFFIXES


def find_dangling_entries(repo_cache_dir: str) -> list[str]:
    """Broken entries under ``<repo_cache_dir>/snapshots/*/``: dangling
    symlinks plus suspicious zero-byte regular files (see above).

    Returns absolute paths. On a healthy cache this is a no-op scan — a pure
    lstat/stat walk of ``snapshots/`` (``blobs/`` is never visited), no
    hashing, no network. Never raises."""
    broken: list[str] = []
    snapshots = os.path.join(repo_cache_dir, "snapshots")
    if not os.path.isdir(snapshots):
        return broken
    try:
        # followlinks=False: a dangling symlink is not a dir, so os.walk lists
        # it among the files of its parent — exactly where we scan.
        for root, _dirs, files in os.walk(snapshots, followlinks=False):
            for name in files:
                path = os.path.join(root, name)
                if _is_dangling_symlink(path) or _is_suspicious_zero_byte(path):
                    broken.append(path)
    except OSError as walk_err:  # pragma: no cover - defensive
        logger.warning("HF cache scan of %s aborted: %s", snapshots, walk_err)
    return broken


def _force_copy_mode(cache_root: str) -> bool:
    """Best-effort: make huggingface_hub materialize snapshot entries as real
    file COPIES instead of symlinks for the rest of this process.

    Why: hub's ``are_symlinks_supported()`` probe can succeed in-process while
    real snapshot symlink creation fails or produces broken links (Windows
    without Developer Mode is the reported case) — and the result is memoized
    in the private ``file_download._are_symlinks_supported_in_dir`` dict, so a
    plain ``snapshot_download`` retry would recreate the SAME dangling links.
    Pre-seeding that memo with False flips hub into copy mode. It's private
    API, so any failure (attribute/shape changed across hub versions) is
    logged and reported as False — the caller then skips the copy-mode pass
    rather than crash. Deliberately NOT undone: on a host where links come
    out broken, every later download should use copies too."""
    try:
        from pathlib import Path
        import huggingface_hub.file_download as _fd

        memo = getattr(_fd, "_are_symlinks_supported_in_dir", None)
        if not isinstance(memo, dict):
            raise TypeError(
                f"_are_symlinks_supported_in_dir is {type(memo).__name__}, expected dict"
            )
        # Same key normalization hub's are_symlinks_supported() applies.
        memo[str(Path(cache_root).expanduser().resolve())] = False
        return True
    except Exception as e:
        logger.warning(
            "Could not force copy-mode for the HF cache (%s) — "
            "huggingface_hub's private memo may have changed; skipping the "
            "copy-mode repair pass.", e,
        )
        return False


def repair_repo_cache(repo_id: str, cache_dir: str | None = None) -> dict:
    """Repair a repo's cache: delete broken snapshot entries (and ONLY those),
    then ``snapshot_download`` to restore the missing files — hub reuses
    completed blobs where the naming matches and re-downloads otherwise.

    Verified after the fact: if the restore recreated dangling links (a host
    where hub's symlink probe passes but real links come out broken — Windows
    without Developer Mode), force copy mode and repair once more so the
    snapshot ends up with real files.

    Returns a summary dict; never raises:
      ``found``    broken entries detected up front,
      ``removed``  entries actually deleted (both passes),
      ``restored`` True when a snapshot_download completed,
      ``outcome``  "healthy" | "healed_with_links" | "healed_with_copies"
                   | "repair_failed",
      ``ok``       True unless outcome == "repair_failed",
      ``error``    "" or why the repair failed.
    """
    summary: dict = {
        "repo_id": repo_id,
        "repo_dir": "",
        "found": 0,
        "removed": 0,
        "restored": False,
        "outcome": "repair_failed",
        "ok": False,
        "error": "",
    }
    try:
        cache_root = cache_dir or hf_cache_home()
        repo_dir = repo_cache_dir(repo_id, cache_root)
        summary["repo_dir"] = repo_dir
        broken = find_dangling_entries(repo_dir)
        summary["found"] = len(broken)
        if not broken:
            summary["ok"] = True  # nothing broken → nothing to do
            summary["outcome"] = "healthy"
            return summary
        if _env_flag("HF_HUB_OFFLINE") or _env_flag("TRANSFORMERS_OFFLINE"):
            # Don't delete what we can't restore: offline mode means the
            # follow-up snapshot_download is off the table.
            summary["error"] = (
                "Hugging Face offline mode is enabled "
                "(HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE) — cannot restore files"
            )
            logger.warning(
                "Model cache for %s has %d broken snapshot entr%s but HF "
                "offline mode is set — skipping repair.",
                repo_id, len(broken), "y" if len(broken) == 1 else "ies",
            )
            return summary

        # Resolve the exact revision before deleting even a broken pointer. An
        # unreviewed repository can never turn cache repair into a mutable-
        # branch network fetch.
        from services.hf_revisions import installed_revision
        revision = installed_revision(repo_id, cache_root)

        def _remove(paths: list[str]) -> int:
            n = 0
            for path in paths:
                try:
                    os.remove(path)  # removes the link/file itself, never a blob
                    n += 1
                    logger.info(
                        "HF cache self-heal: removed broken snapshot entry %s", path
                    )
                except OSError as rm_err:
                    logger.warning(
                        "HF cache self-heal: could not remove broken entry %s: %s",
                        path, rm_err,
                    )
            return n

        summary["removed"] = _remove(broken)
        if summary["removed"] == 0:
            summary["error"] = "broken entries could not be removed"
            return summary

        from huggingface_hub import snapshot_download

        dl_kwargs: dict = {
            "repo_id": repo_id,
            "revision": revision,
            "cache_dir": cache_root,
        }
        endpoint = os.environ.get("HF_ENDPOINT")
        if endpoint:
            dl_kwargs["endpoint"] = endpoint
        from services.hf_auth import token_for_endpoint
        if token_for_endpoint(endpoint, None) is False:
            dl_kwargs["token"] = False  # a mirror never receives the HF token
        snapshot_download(**dl_kwargs)  # nosec B615 -- installed immutable revision
        summary["restored"] = True

        # Verify-after-repair: hub's memoized symlink probe can claim support
        # while the links it just recreated dangle again. If so, force copy
        # mode and repair once more so real files land in the snapshot.
        still_broken = find_dangling_entries(repo_dir)
        if not still_broken:
            summary["ok"] = True
            summary["outcome"] = "healed_with_links"
            logger.info(
                "HF cache self-heal for %s: removed %d broken snapshot entr%s "
                "and restored the snapshot from existing blobs / the Hub.",
                repo_id, summary["removed"],
                "y" if summary["removed"] == 1 else "ies",
            )
            return summary
        logger.warning(
            "HF cache self-heal for %s: the restore recreated %d broken "
            "link(s) — forcing copy-mode and repairing once more.",
            repo_id, len(still_broken),
        )
        if not _force_copy_mode(cache_root):
            summary["error"] = (
                "the snapshot restore recreated broken links and copy-mode "
                "could not be forced"
            )
            return summary
        summary["removed"] += _remove(still_broken)
        snapshot_download(**dl_kwargs)  # nosec B615 -- installed immutable revision
        remaining = find_dangling_entries(repo_dir)
        if remaining:
            summary["error"] = (
                f"{len(remaining)} snapshot entr"
                f"{'y is' if len(remaining) == 1 else 'ies are'} still broken "
                "after the copy-mode repair"
            )
            return summary
        summary["ok"] = True
        summary["outcome"] = "healed_with_copies"
        logger.info(
            "HF cache self-heal for %s: healed with real file copies "
            "(symlinks on this host come out broken; hub stays in copy-mode "
            "for the rest of this run).", repo_id,
        )
        return summary
    except Exception as e:  # never raise — repair is best-effort
        summary["error"] = f"{type(e).__name__}: {e}"
        logger.warning(
            "HF cache self-heal for %s failed: %s", repo_id, summary["error"],
        )
        return summary
