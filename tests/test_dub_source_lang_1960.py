"""#1960 — a rejected source language must say WHICH code it rejected.

The report was "400 Bad Request: Invalid source language code" and nothing
else. That cannot be acted on or triaged: it does not say which of the ninety
or so codes was wrong, so neither the user nor a maintainer reading the
auto-filed issue can tell whether the picker offered something the backend
does not accept, or a stale preference from an older build is still being
sent. I could not determine the cause from the report — which is the point.

The value is a language code chosen from a menu, not private data, and the
neighbouring engine validator already echoes its input the same way.

The last test is the durable one: it reads the picker's own list and asserts
the backend accepts all of it, so a code added to the menu cannot silently
become a 400.
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from api.routers.dub_core import _DUB_SOURCE_LANG_CODES, _source_lang_override

_REPO = pathlib.Path(__file__).resolve().parents[1]
_LANGUAGES_JS = _REPO / "electron" / "src" / "shared" / "utils" / "languages.js"


def test_the_rejection_names_the_code():
    with pytest.raises(HTTPException) as caught:
        _source_lang_override("zz-XX")
    detail = caught.value.detail
    assert "zz-xx" in detail
    assert caught.value.status_code == 400


def test_the_rejection_says_what_to_do():
    with pytest.raises(HTTPException) as caught:
        _source_lang_override("nope")
    assert "auto-detect" in caught.value.detail


@pytest.mark.parametrize("code", ["", "   ", "auto", "und", None])
def test_detect_is_still_detect(code):
    assert _source_lang_override(code) is None


def test_accepted_codes_are_unchanged():
    # This only improved a message; it must not start accepting or rejecting
    # anything different.
    for code in sorted(_DUB_SOURCE_LANG_CODES):
        assert _source_lang_override(code) == code


def test_every_code_the_picker_offers_is_accepted():
    src = _LANGUAGES_JS.read_text(encoding="utf-8")
    codes = re.findall(r"code:\s*['\"]([A-Za-z-]+)['\"]", src)
    assert len(codes) > 50, "LANG_CODES did not parse; update this extraction"
    rejected = []
    for code in codes:
        try:
            _source_lang_override(code)
        except HTTPException:
            rejected.append(code)
    assert rejected == [], (
        "the Dubbing source-language menu offers codes the backend rejects, so "
        f"picking them returns 400: {rejected}"
    )
