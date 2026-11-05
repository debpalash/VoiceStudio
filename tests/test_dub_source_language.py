"""A detected source language must never become a different language (#3821).

``detected_source_language`` used to fall back to the first two letters of any
value it did not recognize, so YouTube's ``fil`` became Finnish and an ASR
server's ``estonian`` became Spanish. Translation then ran from that wrong
language with no warning, and supported languages (``iw``, ``jv``,
``yue-HK``) were dropped as "source language missing".
"""
import pytest

from services.dub_language import (
    DUB_SOURCE_LANG_CODES,
    detected_source_language,
)

#: Values reported by yt-dlp caption tracks and OpenAI-compatible ASR servers
#: that name a language the dub allow-list spells another way.
SAME_LANGUAGE = [
    ("fil", "tl"),        # Filipino — was Finnish
    ("fil-orig", "tl"),   # yt-dlp's original-language suffix
    ("tgl", "tl"),
    ("tagalog", "tl"),    # was Tamil
    ("iw", "he"),         # YouTube's Hebrew — was dropped
    ("jv", "jw"),         # YouTube's Javanese — was dropped
    ("yue-HK", "yue"),    # Cantonese with a region — was dropped
    ("cmn", "zh"),
    ("mandarin", "zh"),
    ("estonian", "et"),   # was Spanish
    ("swedish", "sv"),    # was Swahili
    ("kannada", "kn"),    # was Georgian
    ("kazakh", "kk"),     # was Georgian
    ("armenian", "hy"),   # was Arabic
    ("bengali", "bn"),    # was Belarusian
    ("latvian", "lv"),    # was Latin
    ("slovak", "sk"),     # was Slovenian
    ("spanish", "es"),
    ("german", "de"),
    ("portuguese", "pt"),
]

#: Values that name a language this pipeline cannot translate from. Unknown is
#: the correct answer — it asks the user to pick a source language instead of
#: silently translating from a language that shares the first two letters.
NOT_IN_ALLOW_LIST = [
    "new",            # Newari — was Nepali
    "newari",
    "pam",            # Kapampangan — was Punjabi
    "kapampangan",
    "standard arabic",  # resolves to "arb", a macrolanguage this list lacks
    "",
    "auto",
    "unknown",
    "none",
    "und",
    "unknown-language",
    "klingon",
]


@pytest.mark.parametrize("value,expected", SAME_LANGUAGE)
def test_reported_language_keeps_its_own_language(value, expected):
    assert detected_source_language(value) == expected


@pytest.mark.parametrize("value", NOT_IN_ALLOW_LIST)
def test_unlisted_language_stays_unknown(value):
    assert detected_source_language(value) is None


@pytest.mark.parametrize("value", [None, True, 1, 1.5, ["nl"], {"code": "nl"}])
def test_non_string_language_is_unknown(value):
    assert detected_source_language(value) is None


@pytest.mark.parametrize("value,expected", [
    ("FR", "fr"),          # case-insensitive
    (" es ", "es"),        # surrounding whitespace
    ("es_ES", "es"),       # underscore region (existing behaviour)
    ("pt-BR", "pt"),
    ("zh-Hant", "zh"),
    ("cmn-hans", "cmn-hans"),  # script-qualified, already in the allow-list
    ("yue", "yue"),        # three-letter code the list carries
])
def test_tags_keep_their_base_language(value, expected):
    assert detected_source_language(value) == expected


def test_every_allow_listed_code_round_trips():
    for code in DUB_SOURCE_LANG_CODES:
        assert detected_source_language(code) == code


def test_names_only_ever_resolve_to_allow_listed_codes():
    """Whatever a server calls a language, the answer is a code we can dubbing
    from or unknown — never a code the allow-list does not carry."""
    from omnivoice.utils.lang_map import LANG_NAME_TO_ID

    for name in LANG_NAME_TO_ID:
        resolved = detected_source_language(name)
        assert resolved is None or resolved in DUB_SOURCE_LANG_CODES, name
