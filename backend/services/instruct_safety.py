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
import re

logger = logging.getLogger("omnivoice.instruct")

_logged: set[str] = set()


def _is_valid(value: str) -> bool:
    from omnivoice.utils.voice_design import _INSTRUCT_ALL_VALID

    items = [i.strip().lower() for i in re.split(r"[,，]", value) if i.strip()]
    items = [i for i in items if i != "[object object]"]
    return all(i in _INSTRUCT_ALL_VALID for i in items)


def _one(value):
    if not isinstance(value, str) or not value.strip():
        return value
    if _is_valid(value):
        return value
    from omnivoice.utils.voice_design import sanitize_instruct

    kept = sanitize_instruct(value) or None
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
