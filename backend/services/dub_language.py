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


# Whisper's own language table (code -> name), plus the aliases the language
# picker uses. Kept here (not text_normalization's number-formatting table,
# which only knows a few dozen languages) so every Whisper-supported pick pins.
WHISPER_LANGUAGES = {
    "en": "english", "zh": "chinese", "de": "german", "es": "spanish", "ru": "russian",
    "ko": "korean", "fr": "french", "ja": "japanese", "pt": "portuguese", "tr": "turkish",
    "pl": "polish", "ca": "catalan", "nl": "dutch", "ar": "arabic", "sv": "swedish",
    "it": "italian", "id": "indonesian", "hi": "hindi", "fi": "finnish", "vi": "vietnamese",
    "he": "hebrew", "uk": "ukrainian", "el": "greek", "ms": "malay", "cs": "czech",
    "ro": "romanian", "da": "danish", "hu": "hungarian", "ta": "tamil", "no": "norwegian",
    "th": "thai", "ur": "urdu", "hr": "croatian", "bg": "bulgarian", "lt": "lithuanian",
    "la": "latin", "mi": "maori", "ml": "malayalam", "cy": "welsh", "sk": "slovak",
    "te": "telugu", "fa": "persian", "lv": "latvian", "bn": "bengali", "sr": "serbian",
    "az": "azerbaijani", "sl": "slovenian", "kn": "kannada", "et": "estonian",
    "mk": "macedonian", "br": "breton", "eu": "basque", "is": "icelandic", "hy": "armenian",
    "ne": "nepali", "mn": "mongolian", "bs": "bosnian", "kk": "kazakh", "sq": "albanian",
    "sw": "swahili", "gl": "galician", "mr": "marathi", "pa": "punjabi", "si": "sinhala",
    "km": "khmer", "sn": "shona", "yo": "yoruba", "so": "somali", "af": "afrikaans",
    "oc": "occitan", "ka": "georgian", "be": "belarusian", "tg": "tajik", "sd": "sindhi",
    "gu": "gujarati", "am": "amharic", "yi": "yiddish", "lo": "lao", "uz": "uzbek",
    "fo": "faroese", "ht": "haitian creole", "ps": "pashto", "tk": "turkmen",
    "nn": "nynorsk", "mt": "maltese", "sa": "sanskrit", "lb": "luxembourgish",
    "my": "myanmar", "bo": "tibetan", "tl": "tagalog", "mg": "malagasy", "as": "assamese",
    "tt": "tatar", "haw": "hawaiian", "ln": "lingala", "ha": "hausa", "ba": "bashkir",
    "jw": "javanese", "su": "sundanese", "yue": "cantonese",
}
WHISPER_NAME_TO_CODE = {name: code for code, name in WHISPER_LANGUAGES.items()} | {
    "burmese": "my", "valencian": "ca", "flemish": "nl", "haitian": "ht",
    "letzeburgesch": "lb", "pushto": "ps", "panjabi": "pa", "moldavian": "ro",
    "moldovan": "ro", "sinhalese": "si", "castilian": "es", "mandarin": "zh",
    "filipino": "tl", "farsi": "fa",
    # Names the Electron language picker uses for these Whisper languages.
    "standard arabic": "ar", "norwegian bokmål": "no", "norwegian nynorsk": "nn",
    "plateau malagasy": "mg", "jv": "jw", "nb": "no",
    # Legacy and three-letter tags YouTube puts on caption tracks.
    "fil": "tl", "iw": "he",
}


def whisper_language(language: str | None) -> str | None:
    """Language name (picker/profile) or ISO code -> Whisper code.

    ``None`` for blank/"Auto"/not a Whisper language, which keeps Whisper's
    auto-detect.
    """
    s = str(language or "").strip().lower()
    if not s or s == "auto":
        return None
    if s in WHISPER_NAME_TO_CODE:
        return WHISPER_NAME_TO_CODE[s]
    return s if s in WHISPER_LANGUAGES else None


def detected_source_language(value: str | None) -> str | None:
    """Normalize an ASR or caption language to an allow-listed source code.

    Whisper names (``"english"`` from OpenAI-compatible servers) map to their
    code, and region, script and ``-orig`` subtags are dropped. Letters of the
    language subtag never are: ``fil`` is Filipino, not Finnish.
    """
    code = (value or "").strip().lower().replace("_", "-")
    if code in ("", "auto", "unknown", "none", "und"):
        return None
    if code in DUB_SOURCE_LANG_CODES:
        return code
    primary = code.split("-", 1)[0]
    code = WHISPER_NAME_TO_CODE.get(code) or WHISPER_NAME_TO_CODE.get(primary, primary)
    return code if code in DUB_SOURCE_LANG_CODES else None
