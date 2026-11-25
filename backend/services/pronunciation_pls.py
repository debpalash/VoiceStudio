"""W3C Pronunciation Lexicon (PLS) files for the pronunciation dictionary.

PLS is what ElevenLabs, Azure and Google speech use for pronunciation
dictionaries, so a lexicon made for them imports here and an export works
there. Mapping to dictionary entry types:

- ``<alias>`` → ``respelling`` (an empty ``<alias/>`` is a deletion override:
  the word is not spoken)
- ``<phoneme>`` in IPA → ``ipa``
- ``<phoneme>`` in CMU ARPABET (``x-cmu``, ``cmu-arpabet``, ``arpabet``) → ``cmu``

Other phoneme alphabets (X-SAMPA, vendor sets) are skipped and counted.
Documents with a DTD or entity declarations are refused before parsing: PLS
needs neither, and refusing them rules out entity-expansion attacks.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException
from typing import Iterable, Optional
from xml.sax.saxutils import escape, quoteattr

PLS_NS = "http://www.w3.org/2005/01/pronunciation-lexicon"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_CMU_ALPHABETS = {"x-cmu", "cmu", "cmu-arpabet", "arpabet", "x-arpabet"}
MAX_PLS_BYTES = 5 * 1024 * 1024
# Phoneme rules shared with the dictionary's save validation: IPA is a charset
# gate, CMU/ARPABET is space-separated letters with an optional stress digit.
IPA_BAD = re.compile(r"[\[\]\|\x00-\x1f]")
CMU_TOKEN = re.compile(r"^[A-Za-z]{1,3}[0-2]?$")


def phonemes_ok(kind: str, text: str) -> bool:
    """Whether ``text`` is a phoneme string the dictionary accepts for ``kind``."""
    text = (text or "").strip()
    if not text:
        return False
    if kind == "cmu":
        return all(CMU_TOKEN.match(token) for token in text.split())
    return not IPA_BAD.search(text)


class PlsError(ValueError):
    """The text is not a usable PLS lexicon; the message says why."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element, name: str):
    return [child for child in element if _local(child.tag) == name]


def _text(element) -> str:
    return " ".join((element.text or "").split())


def _alphabet_type(alphabet: str) -> Optional[str]:
    alphabet = alphabet.strip().lower()
    if alphabet == "ipa":
        return "ipa"
    if alphabet in _CMU_ALPHABETS:
        return "cmu"
    return None


def _reading(element, default_alphabet: str):
    """``(type, replacement)`` for one ``<alias>``/``<phoneme>``, or None if
    the dictionary could not use it (so the next reading gets its turn)."""
    if _local(element.tag) == "alias":
        # A respelling is kept as written, inner line breaks included; only the
        # indentation around it goes, and a blank alias is a deletion override.
        return "respelling", (element.text or "").strip()
    text = _text(element)
    kind = _alphabet_type(element.get("alphabet") or default_alphabet)
    return (kind, text) if kind and phonemes_ok(kind, text) else None


def _preferred_reading(lexeme, default_alphabet: str):
    """PLS picks across aliases and phonemes together: the first usable one
    marked ``prefer="true"``, else the first usable one in document order."""
    readings = []
    for child in lexeme:
        if _local(child.tag) not in ("alias", "phoneme"):
            continue
        reading = _reading(child, default_alphabet)
        if reading is None:
            continue
        if (child.get("prefer") or "").strip().lower() == "true":
            return reading
        readings.append(reading)
    return readings[0] if readings else None


def parse_pls(text: str) -> dict:
    """``{"entries": [...], "language": str | None, "skipped": int}``.

    Each entry is ``{"term", "replacement", "type"}``; one per grapheme.
    """
    if len(text.encode("utf-8")) > MAX_PLS_BYTES:
        raise PlsError("The lexicon is larger than 5 MB.")
    upper = text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise PlsError("Lexicons with a DOCTYPE or entity declarations are not accepted.")
    try:
        root = SafeET.fromstring(text, forbid_dtd=True)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise PlsError(f"The file is not valid XML ({exc}).") from None
    if _local(root.tag) != "lexicon":
        raise PlsError("The file is not a pronunciation lexicon (no <lexicon> root).")
    default_alphabet = root.get("alphabet") or "ipa"
    entries: list[dict] = []
    skipped = 0
    for lexeme in _children(root, "lexeme"):
        graphemes = [_text(g) for g in _children(lexeme, "grapheme") if _text(g)]
        reading = _preferred_reading(lexeme, default_alphabet)
        if reading is None:
            skipped += len(graphemes) or 1
            continue
        kind, replacement = reading
        entries.extend({"term": g, "replacement": replacement, "type": kind} for g in graphemes)
    return {"entries": entries, "language": root.get(XML_LANG), "skipped": skipped}


# Characters XML 1.0 can carry; escaping cannot represent the rest (U+0001, …).
_XML_UNREPRESENTABLE = re.compile("[^\t\n\r\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def to_pls(entries: Iterable[dict], language: str) -> str:
    """A PLS document for dictionary entries (``term``/``replacement``/``type``).

    Raises :class:`PlsError` when an entry holds a character XML cannot carry.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<lexicon version="1.0" xmlns="{PLS_NS}" alphabet="ipa" xml:lang={quoteattr(language)}>',
    ]
    for entry in entries:
        for value in (entry["term"], entry["replacement"]):
            if _XML_UNREPRESENTABLE.search(value):
                raise PlsError(
                    f"The entry {entry['term']!r} contains a control character a PLS lexicon "
                    "cannot hold. Export the dictionary as JSON instead."
                )
        term, replacement = escape(entry["term"]), escape(entry["replacement"])
        if entry["type"] == "respelling":
            body = f"<alias>{replacement}</alias>"
        elif entry["type"] == "cmu":
            body = f'<phoneme alphabet="x-cmu">{replacement}</phoneme>'
        else:
            body = f"<phoneme>{replacement}</phoneme>"
        lines.append(f"  <lexeme><grapheme>{term}</grapheme>{body}</lexeme>")
    lines.append("</lexicon>")
    return "\n".join(lines) + "\n"
