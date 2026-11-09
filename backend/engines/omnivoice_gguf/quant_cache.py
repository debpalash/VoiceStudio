"""Offline resolution of the pinned OmniVoice GGUF weights.

The engine reads its base+tokenizer pair from the local HuggingFace
cache and nothing else. Readiness, the load probe and generation all
go through this module, so a host without a complete pair is told how
to install one instead of quietly downloading a second set of weights.

Two policies live here:

* **Compatibility** (#3042) — the selected tier is used whenever its
  pair is installed. Only when that pair is absent, and the selection
  is not an explicit CPU choice, does the smallest complete cached
  automatic pair (Q8_0, then BF16) stand in. Readiness and generation
  never download a replacement, and an installed quant is never
  rewritten.
* **Strictness** — an explicit user override is honoured exactly, and
  override-only entries (F32) are never auto-selected.

The cache is read with ``huggingface_hub.try_to_load_from_cache``,
which is a pure filesystem lookup: it never contacts the hub.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("omnivoice.gguf")

#: Quant tags ordered by footprint, smallest first. The automatic
#: fallback prefers the smallest installed pair, so the conservative
#: memory policy still holds when the default tier is absent.
_FOOTPRINT_ORDER = ("Q4_K_M", "Q8_0", "BF16", "F32")


def _footprint_rank(filename: str) -> int:
    """Rank a quant filename by footprint (the smaller quant first)."""
    for index, tag in enumerate(_FOOTPRINT_ORDER):
        if tag in filename:
            return index
    return len(_FOOTPRINT_ORDER)


def _automatic_keys(quant_map: dict) -> list[str]:
    """Compute classes the automatic selector may pick, smallest first.

    ``_meta`` and ``_extras`` are excluded: ``_extras`` holds
    override-only quants (F32) that the automatic policy must never
    select on its own.
    """
    keys = [
        key
        for key, entry in quant_map.items()
        if key not in ("_meta", "_extras")
        and isinstance(entry, dict)
        and "base" in entry
        and "tokenizer" in entry
    ]
    return sorted(keys, key=lambda key: _footprint_rank(quant_map[key]["base"]))


def cached_file(repo_id: str, filename: str, revision: str) -> Path | None:
    """Return the cached path of ``filename`` at ``revision``, or None.

    ``try_to_load_from_cache`` is a pure filesystem lookup — it never
    contacts the hub. A miss, the hub's "known absent" sentinel, a
    directory, a dangling symlink and a zero-byte partial download all
    read as "not installed".
    """
    if not filename:
        return None
    from huggingface_hub import try_to_load_from_cache

    try:
        cached = try_to_load_from_cache(
            repo_id=repo_id, filename=filename, revision=revision
        )
    except Exception:  # pragma: no cover - hub internals must not break readiness
        logger.debug("HF cache lookup failed for %s", filename, exc_info=True)
        return None
    if not isinstance(cached, str) or not cached:
        return None
    path = Path(cached)
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return None
    except OSError:
        return None
    return path


def installed_pair(
    repo_id: str, revision: str, entry: dict
) -> tuple[Path, Path] | None:
    """Return the cached base+tokenizer paths of one quant entry.

    A pair is complete only when both files are present at the pinned
    revision; a half-installed pair is not an installation.
    """
    base = cached_file(repo_id, entry.get("base", ""), revision)
    if base is None:
        return None
    tokenizer = cached_file(repo_id, entry.get("tokenizer", ""), revision)
    if tokenizer is None:
        return None
    return base, tokenizer


def resolve_installed_pair(
    quant_map: dict,
    target: dict,
    *,
    repo_id: str,
    revision: str,
    allow_fallback: bool = False,
) -> tuple[Path, Path, dict] | None:
    """Resolve the installed pair for ``target``.

    Args:
        quant_map: the parsed ``quant_map.json``.
        target: the entry the selection policy chose.
        repo_id: the HuggingFace repo the weights live in.
        revision: the pinned commit SHA — the pin is exact.
        allow_fallback: when true, an already-installed automatic pair
            is used if ``target`` itself is not installed. Override-only
            entries are never candidates.

    Returns:
        ``(base, tokenizer, entry)`` for the pair in use, or None when
        nothing usable is cached.
    """
    pair = installed_pair(repo_id, revision, target)
    if pair is not None:
        return pair[0], pair[1], target
    if not allow_fallback:
        return None
    target_files = (target.get("base"), target.get("tokenizer"))
    for key in _automatic_keys(quant_map):
        entry = quant_map[key]
        if (entry.get("base"), entry.get("tokenizer")) == target_files:
            continue
        pair = installed_pair(repo_id, revision, entry)
        if pair is not None:
            logger.info(
                "GGUF: using the installed %s pair (%s) — the selected tier "
                "is not installed",
                key,
                entry.get("base"),
            )
            return pair[0], pair[1], entry
    return None


__all__ = ["cached_file", "installed_pair", "resolve_installed_pair"]
