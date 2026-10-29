"""Inline reaction-tag pre-flight (services/tag_policy.py).

The half of the tag story the catalogue cannot cover: a script that already
contains ``[laughter]`` when the active engine does not declare it. The audit
is advisory — text is never rewritten — and engine truth comes from
``TTSBackend.inline_tags`` / ``expression_caps()`` (#3422), which this module
mirrors through ``services.expression``.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from services import tag_policy
from services.expression import COSYVOICE_INLINE_TAGS, OMNIVOICE_INLINE_TAGS
from services.tag_policy import (
    TAG_WARNING_HEADER,
    TAG_WARNING_TAGS_HEADER,
    audit_tags,
    inline_tags,
    tag_warning_headers,
)


# ── Grammar ─────────────────────────────────────────────────────────────────

def test_app_level_markers_are_not_reaction_tags():
    """Markers the pipeline consumes before the engine are engine-independent,
    so they must never warn — [pause] silence is stitched, SSML-lite and
    [voice:…]/[rate …] are parsed app-side."""
    assert inline_tags(
        "[pause] [pause 300ms] [pause 1.5s] [slow][/slow] [fast] "
        "[emphasis] [spell] [voice:Alice] [rate 0.9]"
    ) == []


def test_inline_pronunciation_overrides_are_not_tags():
    assert inline_tags("[[Dr|Doctor]] and [[5|five]]") == []


def test_only_known_engine_tags_are_reported():
    """Unknown bracket text is the user's business, not a tag warning; only
    tags some shipped engine renders are worth naming."""
    assert inline_tags("[my note] [todo]") == []
    assert inline_tags("[laughter] and [SIGH]") == ["[laughter]", "[sigh]"]


def test_tags_are_deduplicated_and_ordered():
    assert inline_tags("[sigh] [laughter] [sigh]") == ["[sigh]", "[laughter]"]


def test_bracket_grammar_is_bounded():
    assert inline_tags("[unclosed") == []
    assert inline_tags("[a\nb]") == []
    assert inline_tags("[" + "x" * 100 + "]") == []


def test_known_set_tracks_the_engine_declarations():
    """A new engine vocabulary added to services.expression must flow into the
    audit automatically (single source of truth)."""
    for tag in (*OMNIVOICE_INLINE_TAGS, *COSYVOICE_INLINE_TAGS):
        assert tag.lower() in tag_policy.KNOWN_INLINE_TAGS


# ── Audit against the engine's declared tags ────────────────────────────────

def test_engine_that_declares_the_tag_is_silent():
    assert audit_tags("A [laughter] here", ["[laughter]", "[sigh]"]) is None


def test_missing_tags_are_reported_in_text_order():
    warning = audit_tags(
        "A [sigh] and a [breath]",
        ["[laughter]"],  # this engine performs neither one
    )
    assert warning == {"kind": "tags_unsupported", "tags": ["[sigh]", "[breath]"]}


def test_declared_vocabulary_narrows_the_warning():
    """OmniVoice performs its 13 nonverbal tags but not [breath]; CosyVoice
    performs [breath]/[laughter] but not the [question-*] family."""
    assert audit_tags("[laughter] [breath]", OMNIVOICE_INLINE_TAGS) == {
        "kind": "tags_unsupported",
        "tags": ["[breath]"],
    }
    assert audit_tags("[sigh]", COSYVOICE_INLINE_TAGS) == {
        "kind": "tags_unsupported",
        "tags": ["[sigh]"],
    }
    assert audit_tags("[breath] [laughter]", COSYVOICE_INLINE_TAGS) is None


def test_engine_with_no_declaration_warns_for_every_known_tag():
    assert audit_tags("[laughter] plain text", None) == {
        "kind": "tags_unsupported",
        "tags": ["[laughter]"],
    }
    assert audit_tags("plain text", None) is None



def test_header_channel_is_ascii_and_percent_encoded():
    # Full tags are encoded (the renderer decodes and quotes them as-is), so a
    # tag with diacritics can never produce an invalid latin-1 header value.
    warning = audit_tags("A [breath]", [])
    headers = tag_warning_headers(warning)
    assert headers[TAG_WARNING_HEADER] == "1"
    assert headers[TAG_WARNING_TAGS_HEADER] == "%5Bbreath%5D"
    assert headers[TAG_WARNING_TAGS_HEADER].isascii()


def test_header_caps_the_tag_list():
    text = " ".join(
        f"[{tag.strip('[]')}]" for tag in OMNIVOICE_INLINE_TAGS
    )
    headers = tag_warning_headers(audit_tags(text, []))
    assert headers[TAG_WARNING_HEADER] == str(len(OMNIVOICE_INLINE_TAGS))
    assert len(headers[TAG_WARNING_TAGS_HEADER].split(",")) <= tag_policy._MAX_TAGS_IN_HEADER


def test_no_warning_means_no_headers():
    assert tag_warning_headers(None) == {}
    assert tag_warning_headers({}) == {}


def test_audit_never_raises_on_odd_input():
    for text in ("", None, "[[", "[]", "[a][b][c]", "[" * 500, "[pause]"):
        assert audit_tags(text, OMNIVOICE_INLINE_TAGS) is None or isinstance(
            audit_tags(text, OMNIVOICE_INLINE_TAGS), dict
        )
