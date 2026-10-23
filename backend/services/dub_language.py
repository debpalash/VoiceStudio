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


def detected_source_language(value: str | None) -> str | None:
    """Normalize an ASR language without truncating valid three-letter codes."""
    code = (value or "").strip().lower().split("_", 1)[0]
    if code in ("", "auto", "unknown", "none", "und"):
        return None
    if code in DUB_SOURCE_LANG_CODES:
        return code
    short = code[:2]
    return short if short in DUB_SOURCE_LANG_CODES else None
