"""W3C Pronunciation Lexicon (PLS) files for the pronunciation dictionary.

PLS is what ElevenLabs, Azure and Google speech use for pronunciation
dictionaries, so a lexicon made for them imports here and an export works
there. Mapping to dictionary entry types:

- ``<alias>`` → ``respelling``
- ``<phoneme>`` in IPA → ``ipa``
- ``<phoneme>`` in CMU ARPABET (``x-cmu``, ``cmu-arpabet``, ``arpabet``) → ``cmu``

Other phoneme alphabets (X-SAMPA, vendor sets) are skipped and counted.
Documents with a DTD or entity declarations are refused before parsing: PLS
needs neither, and refusing them rules out entity-expansion attacks.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Iterable, Optional
from xml.sax.saxutils import escape, quoteattr

PLS_NS = "http://www.w3.org/2005/01/pronunciation-lexicon"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_CMU_ALPHABETS = {"x-cmu", "cmu", "cmu-arpabet", "arpabet", "x-arpabet"}
MAX_PLS_BYTES = 5 * 1024 * 1024


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


def _preferred(elements):
    for element in elements:
        if (element.get("prefer") or "").strip().lower() == "true":
            return element
    return elements[0] if elements else None


def parse_pls(text: str) -> dict:
    """``{"entries": [...], "language": str | None, "skipped": int}``.

    Each entry is ``{"term", "replacement", "type"}``; one per grapheme.
    """
    if len(text.encode("utf-8")) > MAX_PLS_BYTES:
        raise PlsError("The lexicon is larger than 5 MB.")
    head = text[:4096].upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in text.upper():
        raise PlsError("Lexicons with a DOCTYPE or entity declarations are not accepted.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise PlsError(f"The file is not valid XML ({exc}).") from None
    if _local(root.tag) != "lexicon":
        raise PlsError("The file is not a pronunciation lexicon (no <lexicon> root).")
    default_alphabet = root.get("alphabet") or "ipa"
    entries: list[dict] = []
    skipped = 0
    for lexeme in _children(root, "lexeme"):
        graphemes = [_text(g) for g in _children(lexeme, "grapheme") if _text(g)]
        alias = _preferred(_children(lexeme, "alias"))
        phoneme = _preferred(_children(lexeme, "phoneme"))
        if alias is not None and _text(alias):
            kind, replacement = "respelling", _text(alias)
        elif phoneme is not None and _text(phoneme):
            kind = _alphabet_type(phoneme.get("alphabet") or default_alphabet)
            replacement = _text(phoneme)
            if kind is None:
                skipped += len(graphemes) or 1
                continue
        else:
            skipped += len(graphemes) or 1
            continue
        entries.extend({"term": g, "replacement": replacement, "type": kind} for g in graphemes)
    return {"entries": entries, "language": root.get(XML_LANG), "skipped": skipped}


def to_pls(entries: Iterable[dict], language: str) -> str:
    """A PLS document for dictionary entries (``term``/``replacement``/``type``)."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<lexicon version="1.0" xmlns="{PLS_NS}" alphabet="ipa" xml:lang={quoteattr(language)}>',
    ]
    for entry in entries:
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
