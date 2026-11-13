"""Degrade an unsupported OmniVoice ``instruct`` instead of failing the render.

OmniVoice reads ``instruct`` as a closed vocabulary of voice-design tags and
raises on anything else. LLM translation fills dub segments with free-text
directions ("slow", "energetic"), so one such segment used to abort a whole
dub job (#4054). Every engine whose ``instruct_vocabulary`` is ``"tags"``
passes its ``instruct`` through :func:`tag_safe_instruct` right before the
model call, which covers dub, batch, audiobook, preview and API callers alike.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("omnivoice.instruct")

_logged: set[str] = set()


def _accepted(value: str) -> bool:
    """Whether OmniVoice's own resolver takes ``value`` (vocabulary and categories)."""
    from omnivoice.models.omnivoice import _resolve_instruct

    try:
        _resolve_instruct(value)
        return True
    except ValueError:
        return False


def _one(value):
    if not isinstance(value, str) or not value.strip():
        return value
    # Collapse whitespace runs first: OmniVoice's comma split backtracks across
    # them, so a long run of spaces would cost quadratic time in the resolver.
    value = " ".join(value.split())
    if _accepted(value):
        return value
    from omnivoice.utils.voice_design import sanitize_instruct

    # Whitelisted tags, one per category; then drop from the end until the
    # resolver agrees (covers a dialect mixed with an accent).
    items = [i for i in sanitize_instruct(value).split(", ") if i]
    while items and not _accepted(", ".join(items)):
        items.pop()
    kept = ", ".join(items) or None
    if value not in _logged and len(_logged) < 200:
        _logged.add(value)
        logger.warning(
            "OmniVoice accepts only voice-design tags; instruct %r %s",
            value[:80],
            f"was reduced to {kept!r}" if kept else "was dropped for this segment",
        )
    return kept


def tag_safe_instruct(instruct):
    """``instruct`` (string, list, or None) reduced to what OmniVoice accepts."""
    if isinstance(instruct, (list, tuple)):
        return [_one(v) for v in instruct]
    return _one(instruct)
