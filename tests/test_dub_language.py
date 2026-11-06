"""``detected_source_language`` must never turn one language into another.

Truncating an unlisted value to two letters used to do exactly that:
``fil`` (Filipino) became Finnish, ``estonian`` became Spanish and
``swedish`` became Swahili (#3821). Unrecognized values are unknown now.
"""
import pytest

from services.dub_language import detected_source_language


@pytest.mark.parametrize(
    "value, expected",
    [
        # Codes already in the dub allow-list are kept as-is.
        ("en", "en"),
        ("nl_NL", "nl"),
        ("yue", "yue"),
        ("cmn-hans", "cmn-hans"),
        ("cmn-hant", "cmn-hant"),
        # BCP-47 tags resolve to their primary subtag.
        ("yue-HK", "yue"),
        ("pt-BR", "pt"),
        ("zh-Hans", "zh"),
        ("es-MX", "es"),
        # Tags with extra subtags resolve to the longest supported prefix:
        # "cmn-Hans-CN" and "cmn-hans_CN" both mean "cmn-hans", never
        # the bare "cmn" dubbing does not know (CodeRabbit, #3834).
        ("cmn-Hans-CN", "cmn-hans"),
        ("cmn-hans_CN", "cmn-hans"),
        ("cmn-Hant-TW", "cmn-hant"),
        # YouTube caption codes and legacy codes map to their own language.
        ("fil", "tl"),
        ("fil-orig", "tl"),
        ("iw", "he"),
        ("jv", "jw"),
        # English language names (OpenAI-compatible ASR ``verbose_json``).
        ("estonian", "et"),
        ("swedish", "sv"),
        ("kannada", "kn"),
        ("kazakh", "kk"),
        ("armenian", "hy"),
        ("bengali", "bn"),
        ("tagalog", "tl"),
        ("filipino", "tl"),
        ("latvian", "lv"),
        ("slovak", "sk"),
        ("spanish", "es"),
        ("german", "de"),
        ("portuguese", "pt"),
        ("cantonese", "yue"),
        ("hebrew", "he"),
        # Country name some OpenAI-compatible ASR servers report for
        # Burmese; the old two-letter cut also resolved it to "my", so
        # losing it would strand Burmese batch jobs (Greptile P1, #3834).
        ("myanmar", "my"),
        ("standard arabic", "ar"),
        ("egyptian arabic", "ar"),
        # ISO 639-2/3 codes map to their own language, not a two-letter cut.
        ("est", "et"),
        ("swe", "sv"),
        ("kan", "kn"),
        ("kaz", "kk"),
        ("hye", "hy"),
        ("ben", "bn"),
        ("tgl", "tl"),
        ("lav", "lv"),
        ("slk", "sk"),
        ("arb", "ar"),
    ],
)
def test_detected_source_language_resolves_to_its_own_language(value, expected):
    assert detected_source_language(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        # Unlisted three-letter codes are unknown, not another language:
        # ``new`` is Newari, not Nepali; ``pam`` is Kapampangan, not Punjabi.
        "new",
        "pam",
        "xyz",
        "klingon",
        # Names / codes with no dub equivalent stay unknown.
        "esperanto",
        "unknown-language",
    ],
)
def test_unlisted_language_never_becomes_another_language(value):
    assert detected_source_language(value) is None


@pytest.mark.parametrize("value", [None, "", " ", "auto", "und", "unknown", "none"])
def test_unknown_detection_stays_unknown(value):
    assert detected_source_language(value) is None


def test_non_string_detection_stays_unknown():
    assert detected_source_language(123) is None
    assert detected_source_language(["en"]) is None
