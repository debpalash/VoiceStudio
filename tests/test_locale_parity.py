"""Locale key parity — all 21 i18n files stay in lockstep with en.json.

Replaces the manual locale sweeps: CLAUDE.md's Localization rule routes every
UI string through ``frontend/src/i18n/locales/*.json``, which only works if
every file parses, carries no keys en.json doesn't have, and preserves en's
``{{placeholder}}`` tokens. Real bug classes this pins down:

* a translation that drops ``{{message}}`` shows users a bare error with the
  detail silently lost (six ``gallery.*`` keys drifted this way in all 20
  translations before this test existed);
* a machine-translation pass that mangles the token itself renders it
  literally in the UI (vi.json shipped 31 strings saying ``_V_0__``, and
  ar.json once translated ``{{n}}`` into Arabic);
* a key added to en.json only falls back to English for every other language.

Missing keys degrade gracefully (i18next falls back to en), so full key parity
is enforced as a RATCHET: each locale's missing-key count may only go down.
When en.json gains keys, add them to all 21 locales in the same change — that
is exactly the house rule this test automates.
"""

import json
import os
import re
import warnings

import pytest

_LOCALES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "frontend", "src", "i18n", "locales",
)
_EN = "en"

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# Corruption tokens a broken machine-translation pass leaves behind in place
# of a real {{placeholder}} — seen in the wild: vi.json's `_V_0__` (31 keys)
# and ar.json's `__الخامس_0__` ("{{n}}" with the V literally translated).
_CORRUPTED_TOKEN = re.compile(r"_V_\d+__|__\w+_\d+__")

# Keys whose value may legitimately be nothing but placeholders + punctuation.
# EMPTY, and worth keeping that way: the one candidate (settings.engine_switched,
# "{{family}} → {{engine}}") turned out to be shipping the same untranslated
# arrow in all 21 files, which is the bug this test exists to catch rather than
# an exception to it (CodeRabbit, #1280). Add an entry only for a string no
# translator could influence, with the reason inline.
_PLACEHOLDER_ONLY_ALLOWLIST: set[str] = set()

# Keys whose translations may deliberately omit en's placeholders.
_PLACEHOLDER_ALLOWLIST = {
    # en: "Switch to {{lang}}?" — each locale bakes its own language name into
    # the prompt (de: "Auf Deutsch umstellen?"), because the string is always
    # shown in the language it offers to switch to. Interpolating an English
    # language name there would be worse, not better.
    "bootstrap.suggest_lang",
}

# Engine brand names that must never appear in a status string covering work a
# *different* engine may be doing. ASR and TTS are both user-selectable
# (Model Catalogue → Models), so "Transcribing with Whisper…" was a lie for anyone on
# Parakeet or a transformers pipeline (#1352). Latin spellings only — several
# locales transliterate ("ウィスパー", "Bisikan", "الهمس"), which no practical
# pattern catches; the keys below are checked in every locale anyway, so the
# common case (translators keeping the brand verbatim, as de/es/fr/nl/pt/ru all
# did) still fails loudly, and en.json — where every one of these enters the
# codebase first — is covered outright.
#
# ASCII-letter boundaries rather than ``\b``: Python's ``\b`` is unicode-aware,
# so ``\bwhisper\b`` does NOT match "Whisperで文字起こし中" — a CJK translation
# that keeps the Latin brand glued to the following character would slip
# straight through (CodeRabbit). Excluding only A-Z either side still rejects
# "whispered" and "barks", which is the point of having a boundary at all.
_ENGINE_BRANDS = re.compile(
    r"(?<![A-Za-z])(whisper|parakeet|demucs|cosyvoice|indextts|supertonic|"
    r"kokoro|piper|xtts|bark|vall-?e|seed-?vc|pocket-?tts)(?![A-Za-z])",
    re.IGNORECASE,
)

# Status/progress strings that describe a pipeline STAGE, not a specific
# implementation of it. Naming an engine here is a correctness bug, not a style
# one: the label is shown while some other engine is running.
#
# Every transcription-stage label, not just the one #1352 reported: they are
# the same string in four places (dub overlay, dub workflow, batch, capture),
# so whatever put an engine name in one would have put it in the others.
_ENGINE_AGNOSTIC_KEYS = (
    "dub.transcribing",
    "dub_workflow.transcribing_audio",
    "dub_workflow.transcription_failed",
    "batch.stage_transcribe",
    "capture.transcribing_label",
    "capture.transcription_failed",
    "demo.dictation_transcribing",
)

# Missing-key ratchet: highest allowed number of en.json keys absent from each
# locale. Counts may only go DOWN — translate keys and tighten the number.
# Never raise one: if this fails after adding en.json keys, add the keys to
# every locale (translated) in the same change instead.
_MISSING_BASELINE = {
    "ar": 493, "de": 493, "es": 493, "fr": 493, "hi": 493, "id": 493,
    "it": 493, "ja": 493, "ko": 0, "nl": 493, "pl": 493, "pt": 493,
    "ru": 493, "sv": 493, "th": 493, "tr": 493, "uk": 493, "vi": 493,
    "zh-CN": 486, "zh-TW": 493,
}

#: Keys every locale must carry regardless of the aggregate ratchet above.
#: The count alone is a weak guarantee — a locale can translate one new key
#: while dropping another and the total never moves. These are strings a user
#: on a default path actually reads, so they get pinned by name.
#: capture.copied is the Wayland clipboard-delivery status, and clipboard
#: delivery IS the Wayland default, so leaving it English broke that path for
#: every non-English Linux user (#1610 review).
_REQUIRED_IN_EVERY_LOCALE = (
    "capture.copied",
    "capture.inserted",
    "capture.pasted",
)


def _locale_files():
    return sorted(f for f in os.listdir(_LOCALES_DIR) if f.endswith(".json"))


def _no_duplicates_hook(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate key {k!r}")
        seen[k] = v
    return seen


def _load(name):
    path = os.path.join(_LOCALES_DIR, f"{name}.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh, object_pairs_hook=_no_duplicates_hook)


def _flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


_LOCALES = [f[:-5] for f in _locale_files()]
_OTHERS = [loc for loc in _LOCALES if loc != _EN]

_OPENAPI_KEYS = {
    "settings.openapi",
    "openapi.title",
    "openapi.loading",
    "openapi.unreachable_title",
    "openapi.unreachable_body",
    "openapi.retry",
    "openapi.copy_url",
    "openapi.copy_url_aria",
    "openapi.copied",
    "openapi.copy_failed",
    "openapi.open_raw",
    "openapi.open_raw_aria",
}


def test_locale_inventory_matches_baseline():
    """Every locale is ratcheted; a new locale must be added to the baseline
    (and fully translated), a removed one must be dropped from it."""
    assert _EN in _LOCALES, f"en.json missing from {_LOCALES_DIR}"
    assert set(_OTHERS) == set(_MISSING_BASELINE), (
        "Locale files and _MISSING_BASELINE disagree: "
        f"unlisted={sorted(set(_OTHERS) - set(_MISSING_BASELINE))} "
        f"stale={sorted(set(_MISSING_BASELINE) - set(_OTHERS))}"
    )


@pytest.mark.parametrize("locale", _OTHERS)
def test_openapi_ui_is_translated_in_every_locale(locale):
    translated = _flatten(_load(locale))
    missing = sorted(_OPENAPI_KEYS - translated.keys())
    assert not missing, f"{locale}.json is missing VoiceStudio API strings: {missing}"


@pytest.mark.parametrize("locale", _LOCALES)
def test_locale_parses_without_duplicate_keys(locale):
    data = _load(locale)  # raises on invalid JSON or duplicate keys
    assert isinstance(data, dict) and data, f"{locale}.json must be a non-empty object"


@pytest.mark.parametrize("locale", _OTHERS)
def test_no_keys_beyond_en(locale):
    """A key that only exists in a translation is dead weight (nothing renders
    it) and usually means a rename that missed en.json."""
    extra = sorted(set(_flatten(_load(locale))) - set(_flatten(_load(_EN))))
    assert not extra, (
        f"{locale}.json has {len(extra)} key(s) that do not exist in en.json "
        f"(en.json is the source of truth — rename or remove them): {extra[:20]}"
    )


@pytest.mark.parametrize("locale", _OTHERS)
def test_missing_keys_ratchet(locale):
    missing = sorted(set(_flatten(_load(_EN))) - set(_flatten(_load(locale))))
    allowed = _MISSING_BASELINE[locale]
    assert len(missing) <= allowed, (
        f"{locale}.json is missing {len(missing)} en.json keys — the ratchet "
        f"allows at most {allowed}. New en.json keys must land in all 21 "
        f"locales (translated) in the same change (CLAUDE.md, Localization). "
        f"Newly missing keys include: {missing[:20]}"
    )
    if len(missing) < allowed:
        # An improvement must never fail CI (CodeRabbit review, #1198) — but
        # the gain should be locked in, so nudge loudly without blocking.
        warnings.warn(
            f"{locale}.json now misses only {len(missing)} keys (baseline "
            f"{allowed}) — tighten _MISSING_BASELINE['{locale}'] to "
            f"{len(missing)} so the ratchet holds the gain.",
            stacklevel=1,
        )


@pytest.mark.parametrize("locale", _OTHERS)
def test_placeholders_match_en(locale):
    """For every shared key, the translation must use exactly en's
    {{placeholders}} — a dropped one loses runtime data on screen, an invented
    one renders literally."""
    en = _flatten(_load(_EN))
    loc = _flatten(_load(locale))
    problems = []
    for key in sorted(set(en) & set(loc)):
        if not (isinstance(en[key], str) and isinstance(loc[key], str)):
            continue
        want = set(_PLACEHOLDER.findall(en[key]))
        got = set(_PLACEHOLDER.findall(loc[key]))
        invented = got - want
        dropped = want - got
        if key in _PLACEHOLDER_ALLOWLIST:
            invented = set()
            dropped = set()
        # i18next plural forms: "one line" / singular phrasings idiomatically
        # omit the count in many languages — allow {{count}} to be dropped in
        # explicit singular/zero forms only.
        if key.rsplit(".", 1)[-1].endswith(("_one", "_zero")):
            dropped -= {"count"}
        if invented or dropped:
            problems.append(
                f"  {key}: en={en[key]!r} vs {locale}={loc[key]!r}"
                + (f" (missing {sorted(dropped)})" if dropped else "")
                + (f" (not in en: {sorted(invented)})" if invented else "")
            )
    assert not problems, (
        f"{locale}.json placeholder drift against en.json "
        f"({len(problems)} key(s)):\n" + "\n".join(problems[:25])
    )


@pytest.mark.parametrize("locale", _LOCALES)
def test_dub_history_has_every_plural_form(locale):
    """Keep the full i18next plural key set available in every locale."""
    history = _load(locale).get("history", {})
    required = {
        "dub_meta_zero",
        "dub_meta_one",
        "dub_meta_two",
        "dub_meta_few",
        "dub_meta_many",
        "dub_meta_other",
    }
    missing = sorted(required - history.keys())
    assert not missing, f"{locale}.json is missing Dub history plural forms: {missing}"

    invalid = sorted(
        key
        for key in required
        if "{{count}}" not in history[key] or "{{duration}}" not in history[key]
    )
    assert not invalid, f"{locale}.json has invalid Dub history plural forms: {invalid}"


@pytest.mark.parametrize("locale", _LOCALES)
def test_no_placeholder_only_values(locale):
    """A value made of nothing but {{placeholders}} and punctuation is not a
    translation — it is a missing string.

    #1280 shipped ``engines.selectWithCaveat`` as ``"{{engine}}: {{reason}}"``
    in all 21 files. The review read that as 20 untranslated locales, but
    en.json said the same thing: the English string had never been written, so
    every "translation" faithfully copied a non-sentence. Users in all 21
    languages would have seen a bare ``omnivoice: <English backend text>``.

    Parity tests cannot catch this — the key is present everywhere and the
    placeholders match perfectly. Only the absence of prose gives it away, and
    en.json is checked too because that is where this one started.
    """
    bad = []
    for key, value in sorted(_flatten(_load(locale)).items()):
        if not isinstance(value, str) or key in _PLACEHOLDER_ONLY_ALLOWLIST:
            continue
        if not _PLACEHOLDER.search(value):
            continue
        # \w is unicode-aware: CJK, Thai, Devanagari and Arabic all count as
        # prose, so a real translation never trips this.
        if not re.search(r"\w", _PLACEHOLDER.sub("", value)):
            bad.append(f"  {key}: {value!r}")
    assert not bad, (
        f"{locale}.json has {len(bad)} placeholder-only value(s) — write the "
        f"sentence around the placeholders (in en.json first, then translate "
        f"it), or allowlist the key in _PLACEHOLDER_ONLY_ALLOWLIST with a "
        f"reason:\n" + "\n".join(bad[:25])
    )


@pytest.mark.parametrize("locale", _LOCALES)
def test_no_corrupted_placeholder_tokens(locale):
    """Guard the whole class of the vi.json incident: a translation pass that
    rewrites `{{count}}` into `_V_0__` (or similar) ships the garbage token
    straight to the UI, even on keys whose en value has no placeholder."""
    bad = [
        f"  {key}: {value!r}"
        for key, value in sorted(_flatten(_load(locale)).items())
        if isinstance(value, str) and _CORRUPTED_TOKEN.search(value)
    ]
    assert not bad, (
        f"{locale}.json contains corrupted placeholder tokens "
        f"(restore the real {{{{name}}}} from en.json):\n" + "\n".join(bad[:25])
    )


@pytest.mark.parametrize("locale", _LOCALES)
def test_engine_agnostic_labels_name_no_engine(locale):
    """A stage label must not name the engine that happens to implement it.

    The dub overlay said "Transcribing with Whisper…" in all 21 languages while
    ASR is a Model Catalogue → Models choice, so anyone on Parakeet or a transformers
    pipeline was told the wrong engine was running — and a user debugging a slow
    or failing transcription would go read Whisper's docs (#1352, thanks
    @paoloantinori!). The same trap is one line away for any future stage label,
    which is why this is a list to extend rather than a one-off assertion.

    Deliberately checked in EVERY locale, not just en: the fix for #1352 landed
    in en first and 5 translations kept the old engine name for a while, which
    is exactly the drift a parity test cannot see (the key is present, the
    placeholders match — only the brand name gives it away).
    """
    flat = _flatten(_load(locale))
    bad = []
    for key in _ENGINE_AGNOSTIC_KEYS:
        value = flat.get(key)
        if not isinstance(value, str):
            continue  # absent here; the missing-key ratchet owns that case
        hit = _ENGINE_BRANDS.search(value)
        if hit:
            bad.append(f"  {key}: {value!r} names {hit.group(0)!r}")
    assert not bad, (
        f"{locale}.json names a specific engine in a stage label that other "
        f"engines also run — describe the STAGE, not the implementation "
        f"(en: 'Transcribing audio…'):\n" + "\n".join(bad)
    )


def test_engine_brand_matcher_catches_the_forms_that_actually_ship():
    """The matcher itself, not the current locale contents.

    Every assertion here is a string a translator could plausibly write, and the
    check is only worth having if it survives them. The CJK case is the one that
    motivated the ASCII-letter boundaries: Python's ``\\b`` is unicode-aware, so
    ``\\bwhisper\\b`` does not match a brand name glued to a following kana
    (CodeRabbit).
    """
    caught = (
        "Transcribing with Whisper\u2026",      # the #1352 string
        "Whisper\u3067\u6587\u5b57\u8d77\u3053\u3057\u4e2d",  # Latin brand + kana, no ASCII boundary
        "Transkrypcja (whisper)",                # punctuation either side
        "\u0442\u0440\u0430\u043d\u0441\u043a\u0440\u0438\u043f\u0446\u0438\u044f Whisper",  # Cyrillic + Latin brand
        "Bark, then transcribe",                 # short brand, still a brand
    )
    for value in caught:
        assert _ENGINE_BRANDS.search(value), f"matcher missed an engine name in {value!r}"

    not_caught = (
        "whispered instructions",   # substring of an ordinary word
        "The dog barks",            # ditto, and 'bark' is the risky short one
        "Transcribing audio\u2026",     # the corrected en string
    )
    for value in not_caught:
        assert not _ENGINE_BRANDS.search(value), f"matcher false-positived on {value!r}"


def test_transliterated_brands_are_a_known_gap():
    """Documented limit, asserted so it cannot be mistaken for coverage.

    Several locales transliterate rather than keep the Latin spelling
    (\u30a6\u30a3\u30b9\u30d1\u30fc, Bisikan, \u0627\u0644\u0647\u0645\u0633), and no practical pattern catches those
    without a per-language brand table that would rot. Those five were fixed by
    hand in #1352; if this ever starts passing because such a table was added,
    delete this test rather than weakening the one above.
    """
    assert not _ENGINE_BRANDS.search("\u30a6\u30a3\u30b9\u30d1\u30fc\u3067\u6587\u5b57\u8d77\u3053\u3057\u4e2d")


def _lookup(tree, dotted):
    node = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


@pytest.mark.parametrize("locale", sorted(_MISSING_BASELINE))
@pytest.mark.parametrize("key", _REQUIRED_IN_EVERY_LOCALE)
def test_every_locale_carries_the_user_facing_dictation_status(locale, key):
    """Named-key parity, not just the aggregate count.

    The ratchet in _MISSING_BASELINE measures totals, so a locale can gain one
    translation and lose another without the number moving. These keys are
    read by users on default paths, so they are asserted individually.
    """
    value = _lookup(_load(locale), key)
    english = _lookup(_load("en"), key)
    assert isinstance(value, str) and value.strip(), (
        f"{locale}.json is missing {key!r} — users on that locale see the raw key "
        f"or the English string"
    )
    assert value != english, (
        f"{locale}.json copies the English {key!r} verbatim ({english!r}); "
        f"translate it or the ratchet is measuring nothing"
    )
