"""Pre-flight audit of inline reaction tags against the active engine's set.

Reaction tags (``[laughter]``, ``[sigh]``, ``[breath]``, the ``[question-*]``
family, …) are engine-native: only engines that declare them in
``expression_caps()["tags"]`` (``TTSBackend.inline_tags``) render them. Every
other engine receives the tag as literal text and speaks it ("bracket
laughter"). The catalogue already gates *insertion* on that truth (#3422); this
module covers the other half — text that already contains a tag, e.g. a pasted
script or a tag typed before switching engines.

Advisory only: the pipeline's contract is that the user's text reaches the
engine verbatim (see docs/expressive-speech.md). Callers turn the audit into a
response header so the UI can say which part will sound wrong; nothing here
rewrites or strips text.

Pure text + declared-capability comparison — no registry import, so it is safe
from any request path and trivially testable. App-level markers
(``[pause …]``, the SSML-lite pair, ``[voice:…]``, ``[[…]]`` pronunciation
overrides) are handled before any engine sees them and are never warnings.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import quote

from services.expression import COSYVOICE_INLINE_TAGS, OMNIVOICE_INLINE_TAGS

#: Every reaction tag some shipped engine renders (lowercase, brackets
#: included). Sourced from services.expression so a new engine vocabulary is
#: covered as soon as its tags are declared there.
KNOWN_INLINE_TAGS: frozenset[str] = frozenset(
    tag.lower() for tag in (*OMNIVOICE_INLINE_TAGS, *COSYVOICE_INLINE_TAGS)
)

# Tag grammar: one bracket pair, no nesting/fences, newline-free, bounded
# length. The lookarounds keep the pronunciation layer's ``[[term|repl]]``
# overrides out of the match, and prose in long brackets is ignored.
_TAG_RE = re.compile(r"(?<!\[)\[([^\[\]\n]{1,64})\](?!\])")

# Markers the pipeline consumes before the engine runs: [pause], the SSML-lite
# tags (incl. closing forms), [voice:NAME] and [rate …]. Longform/Stories
# additionally parse them; none of them is the engine's business.
_APP_TAG_WORDS = frozenset({"pause", "slow", "fast", "emphasis", "spell", "rate"})

# Response header names. The classic WAV response has no body-side channel for
# text; the NDJSON preview applies the same headers before the first chunk, so
# one count + tag list covers both delivery paths.
TAG_WARNING_HEADER = "X-OmniVoice-Tag-Warning"
TAG_WARNING_TAGS_HEADER = "X-OmniVoice-Tag-Warning-Tags"

_MAX_TAGS_IN_HEADER = 8
_MAX_TAGS_HEADER_CHARS = 240


def _app_marker(name: str) -> bool:
    """True for tags the pipeline consumes before an engine sees them."""
    s = name.strip().lower()
    if not s:
        return False
    if s.startswith("/"):  # closing SSML-lite form, e.g. [/slow]
        s = s[1:].strip()
    head = s.split(None, 1)[0]
    if head in _APP_TAG_WORDS:
        return True
    return head.startswith("voice:")


def inline_tags(text: str) -> list[str]:
    """Reaction-style bracket tags in *text*, lowercased, de-duplicated, in order.

    App-level markers are excluded; unrecognised bracket text (prose in
    brackets, custom annotations) is not reported — only tags a shipped engine
    actually renders are worth a warning.
    """
    if not text or "[" not in text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in _TAG_RE.finditer(text):
        name = match.group(1).strip()
        if not name or _app_marker(name):
            continue
        tag = f"[{name.lower()}]"
        if tag not in KNOWN_INLINE_TAGS or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def audit_tags(text: str, supported_tags: Iterable[str] | None) -> Optional[dict]:
    """Warning dict for tags the engine will speak literally, or None.

    ``supported_tags`` is the active engine's ``expression_caps()["tags"]``.
    Shape: ``{"kind": "tags_unsupported", "tags": [...]}`` — stable enough for
    a header helper or a JSON frame. An engine that declares no tags produces a
    warning for every known reaction tag it receives; an engine whose list is
    ``None`` (undeclared) is treated the same way, because a tag it does not
    declare is a tag it does not render.
    """
    tags = inline_tags(text)
    if not tags:
        return None
    supported = {
        str(tag).strip().lower() for tag in (supported_tags or ()) if str(tag).strip()
    }
    unsupported = [tag for tag in tags if tag not in supported]
    if not unsupported:
        return None
    return {"kind": "tags_unsupported", "tags": unsupported}


def tag_warning_headers(warnings: Optional[dict]) -> dict[str, str]:
    """Response headers for a classic (non-streaming) generation response."""
    if not warnings:
        return {}
    tags: Iterable[str] = warnings.get("tags") or []
    tags = list(tags)
    encoded: list[str] = []
    for tag in tags[:_MAX_TAGS_IN_HEADER]:
        value = quote(str(tag), safe="")
        if sum(len(part) + 1 for part in encoded) + len(value) > _MAX_TAGS_HEADER_CHARS:
            break
        encoded.append(value)
    headers = {TAG_WARNING_HEADER: str(len(tags))}
    if encoded:
        headers[TAG_WARNING_TAGS_HEADER] = ",".join(encoded)
    return headers


__all__ = [
    "KNOWN_INLINE_TAGS",
    "TAG_WARNING_HEADER",
    "TAG_WARNING_TAGS_HEADER",
    "audit_tags",
    "inline_tags",
    "tag_warning_headers",
]
