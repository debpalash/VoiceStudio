"""Every diagnosed failure class reads in the user's language and offers its fix.

The backend names a failure with a stable class (``core.failure._HINTS`` plus
the dynamic HF_MIRROR_UNREACHABLE) and sends English text. The Electron app
shows a localized message and an in-app action for each class from ONE table,
``electron/src/shared/utils/failureCatalog.ts``. A class added to the backend
without an entry there reached users as the backend's English sentence with no
fix button — the owner's Portuguese Clone page showed exactly that for
REFERENCE_STT_MISSING. These tests fail on that drift.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "electron/src/shared/utils/failureCatalog.ts"
LOCALES = ROOT / "electron/src/renderer/src/i18n/locales"
SOURCE = CATALOG.read_text(encoding="utf-8")

_BODY = SOURCE.split("export const FAILURE_CATALOG = {", 1)[1].split("} as const", 1)[0]
_ENTRIES = dict(re.findall(r"^  ([A-Z_0-9]+): \{\s*messageKey: '([\w.]+)'", _BODY, re.M))
_TARGETS = set(re.findall(r"open\('(\w+)'\)", _BODY))
_ACTIONS = {f"failureAction.{t}" for t in _TARGETS} | {
    "failureAction.free_memory",
    "failureAction.add_transcript",
}


def _lookup(catalog: dict, dotted: str):
    node = catalog
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def test_catalog_covers_every_backend_failure_class():
    from core.failure import _HINTS  # resolved at run time, never a stale module

    backend = set(_HINTS) | {"HF_MIRROR_UNREACHABLE"}
    assert backend - set(_ENTRIES) == set(), "add these classes to failureCatalog.ts"
    assert set(_ENTRIES) - backend == set(), "catalog names classes the backend never sends"


@pytest.mark.parametrize("locale", sorted(p.stem for p in LOCALES.glob("*.json")))
def test_every_message_and_action_label_is_translated(locale):
    catalog = json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
    english = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
    missing = [k for k in sorted(set(_ENTRIES.values()) | _ACTIONS) if not _lookup(catalog, k)]
    assert missing == []
    if locale != "en":
        # A class message copied from English is not a translation.
        untranslated = [
            k for k in sorted(set(_ENTRIES.values()) | _ACTIONS)
            if k.startswith("failure") and _lookup(catalog, k) == _lookup(english, k)
        ]
        assert untranslated == []


def _reference_stt_sentence() -> str:
    source = (ROOT / "omnivoice/models/omnivoice.py").read_text(encoding="utf-8")
    block = source.split("raise _ReferenceAsrNotInstalled(", 1)[1].split(")", 1)[0]
    return "".join(re.findall(r'"([^"]*)"', block))


def test_owned_text_signatures_match_the_backend_sentences():
    """The app recognises VoiceStudio's own sentences when they arrive as bare
    text; each signature must still be part of the sentence the backend writes,
    and that sentence must still classify to the same class."""
    from core.failure import (
        INVALID_MEDIA_FILE_MESSAGE,
        classify,
        NO_AUDIO_TRACK_MESSAGE,
        SOURCE_LANGUAGE_REQUIRED_MESSAGE,
    )

    sentences = {
        "NO_AUDIO_TRACK": NO_AUDIO_TRACK_MESSAGE,
        "INVALID_MEDIA_FILE": INVALID_MEDIA_FILE_MESSAGE,
        "SOURCE_LANGUAGE_REQUIRED": SOURCE_LANGUAGE_REQUIRED_MESSAGE,
        "REFERENCE_STT_MISSING": _reference_stt_sentence(),
    }
    pairs = re.findall(
        r"\['([^']+)', '([A-Z_]+)'\]", SOURCE.split("OWNED_FAILURE_SIGNATURES", 1)[1]
    )
    assert {topic for _, topic in pairs} == set(sentences)
    for signature, topic in pairs:
        assert signature in sentences[topic].lower(), topic
        assert classify(sentences[topic]) == topic, topic
