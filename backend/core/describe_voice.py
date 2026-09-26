"""Free-text voice-description → voice-design parameter mapper (issue #317).

Parity with the hosted omnivoice.app "Describe your voice" field, implemented
fully locally: a deterministic keyword/phrase mapper that projects a natural-
language description (e.g. ``"a warm elderly British storyteller, slightly
raspy"``) onto the **existing** voice-design parameter space — the same six
categories the Design tab's attribute picker drives (Gender / Age / Pitch /
Style / EnglishAccent / ChineseDialect).

Design notes
============
* **No model, no network.** This is an ordered synonym-table matcher, not an
  LLM call — it runs identically on macOS/Windows/Linux with zero deps beyond
  the stdlib, preserving the local-first guarantee.
* **Single source of truth.** Every canonical token this module can emit is
  validated at import time against the engine taxonomy in
  ``omnivoice/utils/voice_design.py`` (loaded via ``core.archetypes``), so the
  mapper can never produce an instruct item the engine validator would reject
  (the issue-#89 / #115 crash modes). The Chinese translations of each token
  (e.g. ``男``/``中年``) are *derived* from that taxonomy, never hardcoded.
* **Ordered rules, first match wins.** Within a category, rules are checked in
  a hand-ordered list so more specific phrases outrank generic ones
  ("young child" → child, not young adult; "very deep" → very low pitch, not
  low pitch). Within one rule, the earliest occurrence in the text is reported
  as the matched phrase. Deterministic by construction.
* **Graceful degradation.** Anything the taxonomy can't express (timbre words
  like "raspy", role words like "storyteller") is returned in ``unmatched`` so
  the UI can tell the user exactly which parts were ignored instead of failing
  silently (issue #317's validation-feedback note). A description with no
  matches at all yields all-``Auto`` attrs and an empty instruct.

Localization note (CLAUDE.md): the only hardcoded CJK here is
``DIALECT_PINYIN`` — a functional pinyin → Chinese-dialect-token mapping
(model vocabulary, like ``electron/src/shared/utils/constants.js``). Registered in
``tests/test_no_hardcoded_cjk.py``'s allowlist with this justification.
"""
from __future__ import annotations

import re

# Reuse the taxonomy already loaded (stdlib-only, by file path) by the
# archetype engine — same single source of truth, one loader to maintain.
from core.archetypes import _VD

_EN_TO_ZH = _VD._INSTRUCT_EN_TO_ZH          # {"male": "男", ...}
_ZH_RE = _VD._ZH_RE
_VALID = _VD._INSTRUCT_ALL_VALID            # every token the engine accepts
_DIALECTS = set(_VD._INSTRUCT_CATEGORIES[5])  # the 12 Chinese dialect tokens

# Category names match the frontend's CATEGORIES keys (utils/constants.js) and
# the archetype ``attrs`` shape, so the response drops straight into vdStates.
CATEGORY_ORDER = ("Gender", "Age", "Pitch", "Style", "EnglishAccent", "ChineseDialect")


def instruct_to_vd_states(instruct: str | None) -> dict[str, str]:
    """Project a saved validator-token instruct onto the complete UI recipe."""
    attrs = {category: "Auto" for category in CATEGORY_ORDER}
    sanitized = _VD.sanitize_instruct(instruct)
    if not sanitized:
        return attrs
    for token in sanitized.split(", "):
        category_index = _VD._instruct_category_index(token)
        if category_index < 0 or category_index >= len(CATEGORY_ORDER):
            continue
        # The first four frontend categories use the English canonical token;
        # dialects and accents already use their engine-native form.
        canonical = _VD._INSTRUCT_ZH_TO_EN.get(token, token)
        attrs[CATEGORY_ORDER[category_index]] = canonical
    return attrs

# ── Pinyin / romanized names → Chinese-dialect tokens (functional vocabulary) ─
DIALECT_PINYIN = {
    "henan": "河南话",
    "shaanxi": "陕西话",
    "sichuan": "四川话",
    "szechuan": "四川话",
    "guizhou": "贵州话",
    "yunnan": "云南话",
    "guilin": "桂林话",
    "jinan": "济南话",
    "shijiazhuang": "石家庄话",
    "gansu": "甘肃话",
    "ningxia": "宁夏话",
    "qingdao": "青岛话",
    "dongbei": "东北话",
    "northeastern chinese": "东北话",
}

# ── Synonym tables ────────────────────────────────────────────────────────────
# Per category: ordered list of (canonical_token, [phrases]). First rule with
# any hit wins the category, so specific phrases must precede generic ones.
# Each canonical token's Chinese translation from the taxonomy is appended
# automatically at compile time (so "中年" maps to "middle-aged", etc.).

_GENDER_RULES = [
    ("female", [
        "female", "woman", "women", "lady", "ladies", "girl", "girls",
        "feminine", "gal", "grandma", "grandmother", "granny", "mother",
        "mom", "mum", "aunt", "auntie", "queen", "princess", "actress",
        "she", "her",
    ]),
    ("male", [
        "male", "man", "men", "guy", "guys", "boy", "boys", "masculine",
        "gentleman", "gentlemen", "dude", "grandpa", "grandfather", "father",
        "dad", "uncle", "king", "prince", "actor", "he", "him", "his",
    ]),
]

# Order is load-bearing: "child" precedes "young adult" so "young child" →
# child; "middle-aged" precedes "elderly" so elderly's bare "aged" synonym
# can't fire inside the hyphenated "middle-aged" (hyphen is a \b boundary);
# "elderly" precedes "young adult" so grandparent words don't fall through.
_AGE_RULES = [
    ("child", [
        "child", "children", "kid", "kiddo", "toddler", "little boy",
        "little girl", "young boy", "young girl", "small child", "childlike",
    ]),
    ("teenager", ["teenager", "teen", "teenage", "adolescent"]),
    ("middle-aged", [
        "middle-aged", "middle aged", "middle age", "midlife", "forties",
        "fifties", "sixties", "mature",
    ]),
    ("elderly", [
        "elderly", "old man", "old woman", "old lady", "older man",
        "older woman", "elder", "senior", "aged", "grandpa", "grandfather",
        "grandma", "grandmother", "granny", "retired", "seventies",
        "eighties", "nineties", "old",
    ]),
    ("young adult", [
        "young adult", "young woman", "young man", "young lady", "youthful",
        "twenties", "thirties", "college", "young",
    ]),
]

# "very …" rules precede their plain counterparts so "very deep" doesn't stop
# at "deep". Bare "low"/"high" only count next to a voice word (pitch/voice/
# tone/register) to avoid false hits like "high quality" or "low effort".
_PITCH_RULES = [
    ("very low pitch", [
        "very low pitch", "very low-pitched", "very low pitched",
        "very low voice", "very low tone", "very deep", "extremely deep",
        "extremely low", "ultra deep", "booming",
    ]),
    ("very high pitch", [
        "very high pitch", "very high-pitched", "very high pitched",
        "very high voice", "very high tone", "extremely high", "squeaky",
        "shrill", "falsetto", "chipmunk",
    ]),
    ("low pitch", [
        "low pitch", "low-pitched", "low pitched", "low voice", "low tone",
        "low register", "deep", "deeper", "bass", "baritone", "husky",
    ]),
    ("high pitch", [
        "high pitch", "high-pitched", "high pitched", "high voice",
        "high tone", "high register", "soprano",
    ]),
    ("moderate pitch", [
        "moderate pitch", "medium pitch", "medium-pitched", "medium pitched",
        "mid-range", "midrange", "average pitch", "moderate",
    ]),
]

_STYLE_RULES = [
    ("whisper", [
        "whisper", "whispering", "whispered", "whispery", "hushed",
        "breathy", "soft-spoken", "soft spoken",
    ]),
]

# Bare "english" means the language, so only the explicit "english accent"
# phrase maps to british. "chinese" maps to the chinese *accent* (English
# speech with a Chinese accent); actual dialect words live in DIALECT_PINYIN.
_ACCENT_RULES = [
    ("american accent", [
        "american", "america", "usa", "us accent", "midwestern",
        "californian", "new york",
    ]),
    ("british accent", [
        "british", "britain", "english accent", "england", "uk accent",
        "london", "cockney", "posh", "received pronunciation",
    ]),
    ("australian accent", ["australian", "australia", "aussie"]),
    ("canadian accent", ["canadian", "canada"]),
    ("indian accent", ["indian", "india"]),
    ("chinese accent", ["chinese accent", "chinese-accented", "chinese"]),
    ("korean accent", ["korean", "korea"]),
    ("japanese accent", ["japanese", "japan"]),
    ("portuguese accent", ["portuguese", "portugal", "brazilian", "brazil"]),
    ("russian accent", ["russian", "russia"]),
]

_DIALECT_RULES = [
    (token, [pinyin for pinyin, tok in DIALECT_PINYIN.items() if tok == token])
    for token in sorted(_DIALECTS)
]

_RULES = {
    "Gender": _GENDER_RULES,
    "Age": _AGE_RULES,
    "Pitch": _PITCH_RULES,
    "Style": _STYLE_RULES,
    "EnglishAccent": _ACCENT_RULES,
    "ChineseDialect": _DIALECT_RULES,
}

# Import-time guard: every canonical token must be in the engine taxonomy, so
# a taxonomy rename upstream fails loudly here instead of at synthesis time.
for _cat_rules in _RULES.values():
    for _token, _ in _cat_rules:
        assert _token in _VALID, f"describe_voice token not in taxonomy: {_token!r}"
for _tok in DIALECT_PINYIN.values():
    assert _tok in _DIALECTS, f"DIALECT_PINYIN value not a taxonomy dialect: {_tok!r}"


# ── Pattern compilation ───────────────────────────────────────────────────────
def _compile_phrase(phrase: str) -> re.Pattern:
    """Compile a synonym phrase to a regex.

    Latin phrases get word boundaries (so "male" never fires inside "female",
    "old" never inside "bold") and flexible separators (space or hyphen, so
    "middle aged" also matches "middle-aged"). CJK phrases match as plain
    substrings — word boundaries are meaningless without spaces.
    """
    if _ZH_RE.search(phrase):
        return re.compile(re.escape(phrase))
    parts = [re.escape(p) for p in re.split(r"[ -]+", phrase) if p]
    return re.compile(r"\b" + r"[\s\-]+".join(parts) + r"\b")


def _compiled_rules():
    out = {}
    for cat, rules in _RULES.items():
        compiled = []
        for token, phrases in rules:
            pats = list(phrases)
            # Derive the Chinese form of each canonical token from the
            # taxonomy (e.g. "middle-aged" → "中年") — never hardcoded here.
            zh = _EN_TO_ZH.get(token)
            if zh:
                pats.append(zh)
            if token not in pats:
                pats.append(token)  # the canonical token always matches itself
            compiled.append((token, [_compile_phrase(p) for p in pats]))
        out[cat] = compiled
    return out


_COMPILED = _compiled_rules()

# "<N> year(s) old / <N>-year-old / <N> yo" → an age bracket. Runs before the
# keyword rules so the trailing "old" never misfires as elderly.
_AGE_NUM = re.compile(
    r"\b(\d{1,3})(?:[\s\-]*(?:years?|yrs?|yr)[\s\-]*old|[\s\-]*(?:yo|y/o))\b"
)


def _age_token_for(years: int) -> str:
    if years <= 12:
        return "child"
    if years <= 19:
        return "teenager"
    if years <= 39:
        return "young adult"
    if years <= 64:
        return "middle-aged"
    return "elderly"


def _normalize(description: str) -> str:
    text = (description or "").lower()
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    return re.sub(r"[ \t]+", " ", text)


def _match_category(category: str, text: str):
    """Return (token, match) for the first rule with a hit, else None.

    Rule order decides the winning token; within the winning rule the earliest
    occurrence in the text is reported as the matched phrase.
    """
    if category == "Age":
        m = _AGE_NUM.search(text)
        if m:
            return _age_token_for(int(m.group(1))), m
    for token, patterns in _COMPILED[category]:
        best = None
        for pat in patterns:
            m = pat.search(text)
            if m is not None and (best is None or m.start() < best.start()):
                best = m
        if best is not None:
            return token, best
    return None


# Fragment splitter for the "unmatched" report: clause separators (incl. the
# CJK comma/ideographic stop, which CJK descriptions use instead of ASCII).
_FRAGMENT = re.compile(r"[^,;.!?()\n，。；！？、]+")
_HAS_CONTENT = re.compile(r"[\w一-鿿]")


def parse_description(description: str) -> dict:
    """Map a free-text voice description onto the design parameter space.

    Returns a dict with:
      * ``attrs``     — full category → token map (``"Auto"`` where nothing
                        matched); same shape as the Design tab's ``vdStates``.
      * ``instruct``  — validator-safe instruct string built from the matched
                        tokens, in canonical category order (may be ``""``).
      * ``matched``   — list of ``{category, token, phrase}`` for transparency.
      * ``unmatched`` — clause fragments that contributed no attribute, so the
                        UI can show what was ignored instead of failing silently.
    """
    text = _normalize(description)
    attrs = {cat: "Auto" for cat in CATEGORY_ORDER}
    matched = []
    spans = []

    for category in CATEGORY_ORDER:
        hit = _match_category(category, text)
        if hit is None:
            continue
        token, m = hit
        attrs[category] = token
        matched.append({"category": category, "token": token, "phrase": m.group(0)})
        spans.append((m.start(), m.end()))

    # Accents are English-only and dialects Chinese-only in the engine
    # taxonomy; a dialect voice speaks Chinese, so an accent token alongside
    # it is contradictory (the issue-#114 conflict class). Dialect wins.
    if attrs["ChineseDialect"] != "Auto" and attrs["EnglishAccent"] != "Auto":
        dropped = attrs["EnglishAccent"]
        attrs["EnglishAccent"] = "Auto"
        matched = [m for m in matched if not (m["category"] == "EnglishAccent" and m["token"] == dropped)]

    instruct = ", ".join(attrs[c] for c in CATEGORY_ORDER if attrs[c] != "Auto")

    unmatched = []
    for frag in _FRAGMENT.finditer(text):
        if not _HAS_CONTENT.search(frag.group(0)):
            continue
        lo, hi = frag.start(), frag.end()
        if any(s < hi and e > lo for s, e in spans):
            continue
        unmatched.append(frag.group(0).strip())

    return {
        "attrs": attrs,
        "instruct": instruct,
        "matched": matched,
        "unmatched": unmatched,
    }
