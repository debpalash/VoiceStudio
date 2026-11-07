"""Accepted pronunciation markup survives the real normalization pre-pass."""
import pytest


@pytest.mark.parametrize("content", [
    "label|" + "word " * 26 + "5",
    "label|" + "x" * 248 + " 5",
    "label|" + "word " * 26 + "\n5",
])
def test_supported_inline_override_preserves_explicit_replacement(content):
    from services.pronunciation import apply_pronunciation
    from services.text_normalization import normalize_for_tts

    assert 128 < len(content) <= 256
    markup = "[[" + content + "]]"
    # The native Malayalam verbalizer needs no optional num2words import.
    raw = markup + " 5"
    normalized = normalize_for_tts(raw, "Malayalam")
    assert normalized == markup + " അഞ്ച്"
    assert apply_pronunciation(normalized) == content.partition("|")[2] + " അഞ്ച്"


def test_long_single_bracket_metadata_remains_opaque():
    from services.text_normalization import normalize_text

    marker = "[voice:" + "name " * 52 + "5]"
    assert normalize_text(marker + " 5", "Malayalam") == marker + " അഞ്ച്"
