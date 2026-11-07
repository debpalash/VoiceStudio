"""Shared source-language normalization without importing router runtimes."""

# Source-language choices exposed by the first-party dub UI, plus every
# language code Whisper can write back after auto-detection. A restored job
# may reuse that detected value as the next upload's override, so rejecting our
# own persisted codes strands otherwise valid dubbing sessions (#1737).
# Keeping this an allow-list still rejects language names and private-use
# BCP-47 tags. Values are normalized to lowercase below.
DUB_SOURCE_LANG_CODES = frozenset({
    "af", "sq", "am", "ar", "hy", "az", "eu", "be", "bn", "bs", "bg",
    "my", "ca", "cmn-hans", "cmn-hant", "hr", "cs", "da", "nl", "en",
    "et", "fi", "fr", "gl", "ka", "de", "el", "gu", "ht", "ha", "haw",
    "he", "hi", "hu", "is", "id", "it", "ja", "jw", "kn", "kk", "km",
    "ko", "ku", "ky", "lo", "la", "lv", "lt", "mk", "ms", "ml", "mt",
    "mi", "mr", "mn", "ne", "no", "ps", "fa", "pl", "pt", "pa", "ro",
    "ru", "sm", "gd", "sr", "sn", "sd", "si", "sk", "sl", "so", "es",
    "su", "sw", "sv", "tg", "ta", "te", "th", "tr", "uk", "ur", "uz",
    "vi", "cy", "xh", "yi", "yo", "zu",
    "as", "ba", "bo", "br", "fo", "lb", "ln", "mg", "nn", "oc", "sa",
    "tk", "tl", "tt", "yue", "zh",
})

_UNKNOWN_VALUES = frozenset({"", "auto", "unknown", "none", "und"})

# Codes that are valid languages but are not in DUB_SOURCE_LANG_CODES, and
# the dub code they mean. YouTube caption tracks and older ISO 639
# assignments use several of these: ``fil`` is Filipino (``tl`` here),
# ``iw`` is the retired Hebrew code (``he``), ``jv`` is Javanese
# (Whisper writes ``jw``). Arabic varieties (``arb``/``aec``/``arz``)
# collapse to the macrolanguage ``ar``, the only Arabic code dubbing knows.
# ISO 639-2/3 codes for a dub language map to that language, never to the
# first two letters of the code.
_CODE_ALIASES = {
    "fil": "tl",
    "iw": "he",
    "jv": "jw",
    "in": "id",
    "ara": "ar",
    "arb": "ar",
    "aec": "ar",
    "arz": "ar",
    "arq": "ar",
    "est": "et",
    "swe": "sv",
    "kan": "kn",
    "kaz": "kk",
    "hye": "hy",
    "arm": "hy",
    "ben": "bn",
    "tgl": "tl",
    "lav": "lv",
    "slo": "sk",
    "slk": "sk",
    "alb": "sq",
    "sqi": "sq",
    "baq": "eu",
    "eus": "eu",
    "bur": "my",
    "mya": "my",
    "chi": "zh",
    "zho": "zh",
    # "zh-yue" is the grandfathered BCP-47 tag for Cantonese; the IANA
    # registry's preferred value is "yue". Without this the longest-prefix
    # walk falls through to "zh" and a Cantonese tag becomes Mandarin
    # (CodeRabbit, #3834).
    "zh-yue": "yue",
    "cze": "cs",
    "ces": "cs",
    "dut": "nl",
    "nld": "nl",
    "fre": "fr",
    "fra": "fr",
    "geo": "ka",
    "kat": "ka",
    "ger": "de",
    "deu": "de",
    "gre": "el",
    "ell": "el",
    "ice": "is",
    "isl": "is",
    "mac": "mk",
    "mkd": "mk",
    "mao": "mi",
    "mri": "mi",
    "may": "ms",
    "msa": "ms",
    "per": "fa",
    "fas": "fa",
    "rum": "ro",
    "ron": "ro",
    "tib": "bo",
    "bod": "bo",
    "wel": "cy",
    "cym": "cy",
    "spa": "es",
    "por": "pt",
    "ita": "it",
    "rus": "ru",
    "jpn": "ja",
    "kor": "ko",
    "hin": "hi",
    # "nb" is Norwegian Bokmål, the written standard Whisper and YouTube
    # report for Norwegian; "no" is the macrolanguage dubbing knows.
    "nb": "no",
}

# Second subtags of a "zh-..." tag that name a distinct spoken Sinitic
# language instead of Mandarin: Hakka, Min (Min Nan), Wu, Gan, Xiang,
# Jinyu. "yue" is not listed: Cantonese is supported via the "zh-yue"
# alias above, and "cmn" is Mandarin itself, so neither falls through
# wrongly. A "zh-<variety>" tag that cannot resolve to a supported code
# must stay unknown instead of falling through to "zh" (CodeRabbit,
# #3834): dubbing Hakka or Min Nan audio as Mandarin would be the same
# wrong-language bug #3821 fixed.
_ZH_VARIETY_SUBTAGS = frozenset({
    "hakka", "min", "nan", "wuu", "gan", "xiang", "hsn", "cjy",
})

# English language names (lowercase, as OpenAI-compatible ASR servers
# report them in ``verbose_json``) -> dub code. Only names that resolve to
# a code in DUB_SOURCE_LANG_CODES are listed; anything else is unknown.
_NAME_ALIASES = {
    "afrikaans": "af",
    "albanian": "sq",
    "amharic": "am",
    "arabic": "ar",
    "standard arabic": "ar",
    "egyptian arabic": "ar",
    "saidi arabic": "ar",
    "algerian arabic": "ar",
    "armenian": "hy",
    "azerbaijani": "az",
    "basque": "eu",
    "belarusian": "be",
    "bengali": "bn",
    "bosnian": "bs",
    "bulgarian": "bg",
    "burmese": "my",
    # "myanmar" is the country name some OpenAI-compatible ASR servers
    # report for Burmese; the old two-letter cut also resolved it to "my",
    # so dropping it would strand Burmese batch jobs (Greptile P1, #3834).
    "myanmar": "my",
    "catalan": "ca",
    "chinese": "zh",
    "mandarin": "zh",
    "mandarin chinese": "zh",
    "cantonese": "yue",
    "croatian": "hr",
    "czech": "cs",
    "danish": "da",
    "dutch": "nl",
    "english": "en",
    "estonian": "et",
    "finnish": "fi",
    "french": "fr",
    "galician": "gl",
    "georgian": "ka",
    "german": "de",
    "greek": "el",
    "gujarati": "gu",
    "haitian creole": "ht",
    "haitian": "ht",
    "hausa": "ha",
    "hawaiian": "haw",
    "hebrew": "he",
    "hindi": "hi",
    "hungarian": "hu",
    "icelandic": "is",
    "indonesian": "id",
    "italian": "it",
    "japanese": "ja",
    "javanese": "jw",
    "kannada": "kn",
    "kazakh": "kk",
    "khmer": "km",
    "korean": "ko",
    "kurdish": "ku",
    "kyrgyz": "ky",
    "lao": "lo",
    "latin": "la",
    "latvian": "lv",
    "lithuanian": "lt",
    "macedonian": "mk",
    "malay": "ms",
    "malayalam": "ml",
    "maltese": "mt",
    "maori": "mi",
    "marathi": "mr",
    "mongolian": "mn",
    "nepali": "ne",
    "norwegian": "no",
    "pashto": "ps",
    "persian": "fa",
    "farsi": "fa",
    "polish": "pl",
    "portuguese": "pt",
    "punjabi": "pa",
    "romanian": "ro",
    "russian": "ru",
    "samoan": "sm",
    "scottish gaelic": "gd",
    "serbian": "sr",
    "shona": "sn",
    "sindhi": "sd",
    "sinhala": "si",
    "sinhalese": "si",
    "slovak": "sk",
    "slovenian": "sl",
    "somali": "so",
    "spanish": "es",
    "sundanese": "su",
    "swahili": "sw",
    "swedish": "sv",
    "tajik": "tg",
    "tamil": "ta",
    "telugu": "te",
    "thai": "th",
    "turkish": "tr",
    "ukrainian": "uk",
    "urdu": "ur",
    "uzbek": "uz",
    "vietnamese": "vi",
    "welsh": "cy",
    "xhosa": "xh",
    "yiddish": "yi",
    "yoruba": "yo",
    "zulu": "zu",
    "assamese": "as",
    "bashkir": "ba",
    "tibetan": "bo",
    "breton": "br",
    "faroese": "fo",
    "luxembourgish": "lb",
    "lingala": "ln",
    "malagasy": "mg",
    "occitan": "oc",
    "sanskrit": "sa",
    "turkmen": "tk",
    "tagalog": "tl",
    "filipino": "tl",
    "tatar": "tt",
}


def detected_source_language(value: str | None) -> str | None:
    """Normalize an ASR / caption language to a dub source code.

    Accepts a dub code (``en``, ``yue``, ``cmn-hans``), a BCP-47 tag
    (``yue-HK``, ``nl_NL``, ``pt-BR``), a legacy / ISO 639-2/3 code
    (``fil``, ``iw``, ``jv``, ``est``) or an English language name
    (``estonian``, ``spanish``). Returns the dub code, or ``None`` when
    the language is unknown.

    A value must never become a *different* language: truncating to two
    letters turned ``estonian`` into Spanish (``es``), ``swedish`` into
    Swahili (``sw``) and ``fil`` into Finnish (``fi``) (#3821), so
    unrecognized values now return ``None`` instead of a two-letter cut.
    """
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().lower().split())
    if not text:
        return None
    # A name with spaces ("standard arabic", "haitian creole") is a name,
    # never a code: resolve it whole, or admit it is unknown.
    if " " in text:
        return _NAME_ALIASES.get(text)
    code = text.replace("_", "-")
    if code in _UNKNOWN_VALUES:
        return None
    if code in DUB_SOURCE_LANG_CODES:
        return code
    # A tag with extra subtags resolves to its longest supported prefix:
    # "cmn-Hans-CN" and "cmn-hans_CN" both mean "cmn-hans" (the old
    # underscore split resolved the latter the same way). Taking only the
    # primary subtag would give "cmn", which dubbing does not know, and
    # strand the job (CodeRabbit, #3834).
    parts = code.split("-")
    for end in range(len(parts), 0, -1):
        prefix = "-".join(parts[:end])
        if prefix in DUB_SOURCE_LANG_CODES:
            # A "zh-<variety>" tag must not fall through to "zh": the
            # varieties in _ZH_VARIETY_SUBTAGS are distinct spoken
            # languages the dub service does not support, so the tag is
            # unknown rather than Mandarin. Longer prefixes win first, so
            # "zh-yue" still resolves to "yue" via _CODE_ALIASES above.
            # The variety need not be the second subtag: script and region
            # subtags come first ("zh-hant-hakka", "zh-tw-hakka"), so scan
            # every subtag until the first extension or private-use
            # singleton ("x", "u", ...), after which subtags are not
            # language varieties (CodeRabbit, #3834).
            if prefix == "zh":
                for part in parts[1:]:
                    if len(part) == 1:  # Extension or private-use singleton.
                        break
                    if part in _ZH_VARIETY_SUBTAGS:
                        return None
            return prefix
        alias = _CODE_ALIASES.get(prefix)
        if alias is not None:
            return alias
    # Names match whole: "myanmar-english" is English, not Burmese.
    return _NAME_ALIASES.get(code)
