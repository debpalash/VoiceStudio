"""Shared source-language normalization without importing router runtimes."""

from __future__ import annotations

import re

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

# Codes a caption track or a remote ASR server reports for a language this
# allow-list spells another way. Every entry is the SAME language, never a
# neighbour: the retired ISO 639-1 codes YouTube still emits ("iw" Hebrew,
# "in" Indonesian, "ji" Yiddish), YouTube's "jv" for the "jw" used here,
# Filipino/Tagalog, Mandarin's macrolanguage code, and Norwegian Bokmål.
# Anything not listed and not in the allow-list stays unknown (#3821).
DUB_SOURCE_LANG_ALIASES = {
    "iw": "he",
    "in": "id",
    "ji": "yi",
    "jv": "jw",
    "fil": "tl",
    "tgl": "tl",
    "cmn": "zh",
    "nb": "no",
    "nob": "no",
}

# Picker/ASR names the bundled language vocabulary does not carry. Same
# spellings ``services.pronunciation`` resolves its scopes with (#2542).
DUB_SOURCE_LANG_NAMES = {"mandarin": "zh", "arabic": "ar", "tagalog": "tl"}

# A bare ISO 639 id with optional region/script subtags: en, kbt, es-MX,
# yue-HK, zh_Hans. Deliberately not "any prefix" — see _resolve_code.
_LANG_TAG_RE = re.compile(r"^([a-z]{2,3})(?:[-_][a-z0-9]{1,8})*$")

_UNKNOWN = frozenset({"", "auto", "unknown", "none", "und"})


def _bundled_language_names() -> dict[str, str]:
    """Lowercase language name → ISO id, from the vocabulary the pickers are
    built from. Pure data — no model import, and a missing map only means
    names are not resolved."""
    try:
        from omnivoice.utils.lang_map import LANG_NAME_TO_ID
    except Exception:  # noqa: BLE001 — degrade to codes-only resolution
        return {}
    return LANG_NAME_TO_ID


def _resolve_code(code: str) -> str | None:
    """The allow-list code for one bare language id, or None.

    None is the safe answer: a code this pipeline cannot name is unknown, and
    an unknown source language asks the user to pick one. Returning a
    neighbouring language instead translates the transcript from the wrong
    language silently, which is what this function used to do by keeping the
    first two letters (#3821).
    """
    if code in DUB_SOURCE_LANG_CODES:
        return code
    alias = DUB_SOURCE_LANG_ALIASES.get(code)
    return alias if alias in DUB_SOURCE_LANG_CODES else None


def detected_source_language(value: str | None) -> str | None:
    """Normalize an ASR language without truncating valid three-letter codes."""
    if not isinstance(value, str):
        return None
    code = " ".join(value.split()).lower()
    if code in _UNKNOWN:
        return None
    direct = _resolve_code(code)
    if direct:
        return direct
    # OpenAI-compatible ASR servers report names ("estonian", "swedish");
    # "estonian" used to become "es" (Spanish).
    named = DUB_SOURCE_LANG_NAMES.get(code) or _bundled_language_names().get(code)
    if named:
        resolved = _resolve_code(named)
        if resolved:
            return resolved
    # Region/script subtags keep their base language: "yue-HK" is Cantonese,
    # "es_ES" is Spanish. A base that is not in the allow-list stays unknown
    # ("new" is Newari, not Nepali).
    match = _LANG_TAG_RE.match(code)
    if match:
        return _resolve_code(match.group(1))
    return None
