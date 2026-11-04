"""Bare Arabic must resolve to arb, not language-agnostic mode (#3723).

The OmniVoice vocabulary names the base Arabic entry "Standard Arabic"
(arb) and has no bare "Arabic" row, so after the dub gate admits bare
Arabic the model resolver used to warn and fall back to None. The alias
below pins the fix; dialects keep their own codes.
"""

from omnivoice.models.omnivoice import _resolve_language


def test_bare_arabic_label_resolves_to_arb():
    assert _resolve_language("Arabic") == "arb"
    assert _resolve_language("arabic") == "arb"


def test_ar_code_resolves_to_arb():
    assert _resolve_language("ar") == "arb"


def test_standard_arabic_and_dialects_unchanged():
    assert _resolve_language("Standard Arabic") == "arb"
    assert _resolve_language("Egyptian Arabic") == "arz"
    assert _resolve_language("English") == "en"


def test_unknown_language_still_falls_back_to_none():
    assert _resolve_language("Klingon") is None
