"""Reference transcripts made from script text (#3062).

A cloning engine pairs a reference clip with its transcript. A transcript that
holds words the clip does not speak makes OmniVoice say those words at the start
of every chunk it renders. Two kinds of script text used to reach a transcript
that way:

* **Control markup.** ``[pause 1s]``, ``[voice:NAME]``, ``[speed:1.2]``,
  ``[rate 0.9]``, the SSML-lite ``[slow]…[/slow]`` family and ``[[term|say]]``
  overrides steer delivery but are never spoken as written.
  :func:`strip_script_markup` removes them; reaction tags such as
  ``[laughter]`` stay, because the take really contains them.
* **Truncated take text.** History rows saved before #3062 kept only the first
  :data:`LEGACY_HISTORY_TEXT_CAP` characters of a take, so a long take became a
  transcript that stops mid-word. :func:`take_reference_transcript` returns no
  transcript for text that may have been cut, and generation then transcribes
  the take's audio instead.

Pure and import-light (no torch), so routers and the engine layer share it.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Mapping, Optional, Union

#: History rows written before #3062 stored ``text[:200]``.
LEGACY_HISTORY_TEXT_CAP = 200

# Each alternative is a fixed literal head followed by one bounded class run
# that cannot contain brackets, so matching stays linear (ReDoS-safe).
_CONTROL_TAG_RE = re.compile(
    r"\[\s*(?:"
    r"pause(?:\s[^\[\]\n]{0,64})?"
    r"|voice\s*:[^\[\]\n]{0,128}"
    r"|speed\s*:[^\[\]\n]{0,32}"
    r"|rate(?:\s[^\[\]\n]{0,32})?"
    r"|/?(?:slow|fast|emphasis|spell)"
    r")\s*\]",
    re.IGNORECASE,
)
_INLINE_OVERRIDE_RE = re.compile(r"\[\[([^\]]{0,256})\]\]")
_HSPACE_RE = re.compile(r"[^\S\n]+")


def _spoken_override(match: re.Match) -> str:
    inner = match.group(1)
    return inner.split("|", 1)[1] if "|" in inner else inner


def strip_script_markup(text: Optional[str]) -> str:
    """``text`` with the script's control markup removed, as it was spoken."""
    if not text:
        return ""
    if "[" in text:
        text = _INLINE_OVERRIDE_RE.sub(_spoken_override, text)
        text = _CONTROL_TAG_RE.sub(" ", text)
    text = _HSPACE_RE.sub(" ", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def take_reference_transcript(text: Optional[str]) -> str:
    """The transcript to pair with a saved take's audio, or ``""``.

    ``""`` means "transcribe the audio": it is returned for text that may be a
    pre-#3062 truncated history row, because a transcript that stops partway
    through the take is worse than none.
    """
    if not text or len(text) == LEGACY_HISTORY_TEXT_CAP:
        return ""
    return strip_script_markup(text)


#: A ``voice_profiles`` row: a ``sqlite3.Row`` from the database, or a mapping
#: with the same keys (tests, cached copies).
ProfileRow = Union[sqlite3.Row, Mapping[str, Any]]

#: Prefix lengths older releases cut take text to before storing it as a
#: transcript: 100 when locking a take (until #3320), 200 in history (#3062).
LEGACY_CUT_LENGTHS = (100, LEGACY_HISTORY_TEXT_CAP)


def _row_value(row: ProfileRow, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def is_legacy_cut_transcript(row: ProfileRow) -> bool:
    """True when a profile's stored transcript is a cut prefix of a take.

    A locked voice's transcript was written by the lock, so a locked transcript
    of exactly a legacy cut length is treated as cut. Any other voice needs a
    take in history whose text the transcript is a cut prefix of, so a
    transcript the user typed is never discarded.
    """
    text = _row_value(row, "ref_text") or ""
    if len(text) not in LEGACY_CUT_LENGTHS:
        return False
    if _row_value(row, "is_locked") and _row_value(row, "locked_audio_path"):
        return True
    from core.db import db_conn

    try:
        with db_conn() as conn:
            hit = conn.execute(
                "SELECT 1 FROM generation_history WHERE substr(text, 1, ?) = ? "
                "AND length(text) >= ? LIMIT 1",
                # A 200-character match is the cut history row itself; a
                # 100-character lock cut came from a longer row.
                (len(text), text, len(text) if len(text) == LEGACY_HISTORY_TEXT_CAP else len(text) + 1),
            ).fetchone()
    except Exception:  # noqa: BLE001 - a missing table only means no evidence
        return False
    return hit is not None


def stored_reference_transcript(row: ProfileRow) -> Optional[str]:
    """A profile row's transcript as a reference transcript, or ``None``.

    ``None`` (transcribe the clip) for a cut legacy take prefix; otherwise the
    stored transcript without script markup. Rows are never rewritten here, so
    saved voices keep their data.
    """
    if is_legacy_cut_transcript(row):
        return None
    return strip_script_markup(_row_value(row, "ref_text")) or None
