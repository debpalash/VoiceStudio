"""``detected_source_language`` must never turn one language into another.

Truncating an unlisted value to two letters used to do exactly that:
``fil`` (Filipino) became Finnish, ``estonian`` became Spanish and
``swedish`` became Swahili (#3821). Unrecognized values are unknown now.
"""
import pytest

from services.dub_language import (
    DUB_SOURCE_LANG_CODES,
    _CODE_ALIASES,
    _NAME_ALIASES,
    detected_source_language,
)


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
        # Grandfathered tag for Cantonese: "zh-yue" means Yue, never
        # Mandarin (CodeRabbit, #3834).
        ("zh-yue", "yue"),
        ("pt-BR", "pt"),
        ("zh-Hans", "zh"),
        # A variety after the private-use singleton is not a language
        # subtag: "zh-Hant-x-hakka" is still Traditional-script Chinese.
        ("zh-Hant-x-hakka", "zh"),
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
        # Norwegian Bokmål ("nb", as Whisper/YouTube report it) is the
        # macrolanguage Norwegian ("no") dubbing knows.
        ("nb", "no"),
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
        # A name with a suffix is not that name: Myanmar English is English.
        "myanmar-english",
        # "zh-<variety>" tags are distinct spoken languages, not Mandarin:
        # Hakka, Min Nan, Wu, Gan, Xiang, Jinyu (CodeRabbit, #3834).
        "zh-hakka",
        "zh-min-nan",
        "zh-min",
        "zh-nan",
        "zh-wuu",
        "zh-gan",
        "zh-xiang",
        "zh-cjy",
        "zh-hsn",
        "zh-hakka-TW",
        # The variety can sit after script/region subtags too.
        "zh-hant-hakka",
        "zh-tw-hakka",
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


def test_alias_tables_only_map_to_supported_dub_codes():
    """Every alias must resolve to a code dubbing accepts (velixio, #3834).

    A new entry whose value is not in DUB_SOURCE_LANG_CODES would make
    detected_source_language return a code dubbing rejects, so the tables
    are guarded here instead of at each call site.
    """
    for table in (_CODE_ALIASES, _NAME_ALIASES):
        for alias, code in table.items():
            assert code in DUB_SOURCE_LANG_CODES, f"{alias!r} -> {code!r}"
