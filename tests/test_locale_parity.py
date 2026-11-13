"""Locale key parity — every i18n file stays in lockstep with en.json.

``electron/src/renderer/src/i18n/locales`` is the catalog the app loads at
runtime. It has full key parity with en.json (no missing keys, no orphans),
parses without duplicate keys, and preserves en's ``{{placeholder}}`` tokens.
Real bug classes this pins down:

* a translation that drops ``{{message}}`` shows users a bare error with the
  detail silently lost (six ``gallery.*`` keys drifted this way in all 20
  translations before this test existed);
* a machine-translation pass that mangles the token itself renders it
  literally in the UI (vi.json shipped 31 strings saying ``_V_0__``, and
  ar.json once translated ``{{n}}`` into Arabic);
* a key added to en.json only falls back to English for every other language.

When en.json gains keys, add them to every locale in the same change — that
is CLAUDE.md's Localization rule, and full parity is enforced below.
"""

import json
import os
import re

import pytest

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
# (the engine's Weights list in Model Catalogue), so "Transcribing with Whisper…" was a lie for anyone on
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

#: Keys every locale must carry regardless of the aggregate ratchet above.
#: The count alone is a weak guarantee — a locale can translate one new key
#: while dropping another and the total never moves. These are strings a user
#: on a default path actually reads, so they get pinned by name.
#: capture.copied is the Wayland clipboard-delivery status, and clipboard
#: delivery IS the Wayland default, so leaving it English broke that path for
#: every non-English Linux user (#1610 review).
_REQUIRED_IN_EVERY_LOCALE = (
    "common.error",
    "player.pause",
    "player.play",
    "settings.hf_source_app_label",
    "settings.hf_source_cli_label",
    "settings.hf_source_env_label",
)


def _no_duplicates_hook(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate key {k!r}")
        seen[k] = v
    return seen


def _flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


_OPENAPI_KEYS = {
    "openapi.title",
    "openapi.unreachable_title",
    "openapi.unreachable_body",
    "openapi.copy_url",
    "openapi.copied",
    "openapi.copy_failed",
    "openapi.open_raw",
}


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


def test_locale_objects_have_no_duplicate_keys():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for directory in (root / "electron/src/renderer/src/i18n/locales",):
        for path in directory.glob("*.json"):
            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    assert key not in result, f"{path.name}: duplicate locale key {key}"
                    result[key] = value
                return result
            json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


# ── Runtime catalog (electron/src/renderer/src/i18n/locales) ─────────────
# The catalog the app actually loads gets the same integrity checks, and full
# parity rather than a ratchet: missing keys and orphans (zh-CN once carried
# four player.* keys English never defined) both fail.
_RENDERER_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "electron", "src", "renderer", "src", "i18n", "locales",
)
_RENDERER_LOCALES = sorted(f[:-5] for f in os.listdir(_RENDERER_DIR) if f.endswith(".json"))
_CLDR_PLURAL = re.compile(r"^(.*)_(?:zero|one|two|few|many|other)$")


def _renderer(name):
    with open(os.path.join(_RENDERER_DIR, f"{name}.json"), encoding="utf-8") as fh:
        return _flatten(json.load(fh, object_pairs_hook=_no_duplicates_hook))


def _english_key(key, en):
    """The en.json key a translation answers to; CLDR plural forms English
    lacks (ar ``_few``, ru ``_many``) map to the English ``_other`` form."""
    if key in en:
        return key
    match = _CLDR_PLURAL.match(key)
    if match and f"{match.group(1)}_other" in en:
        return f"{match.group(1)}_other"
    return None


def test_renderer_catalog_has_the_locales_the_app_offers():
    index = os.path.join(os.path.dirname(_RENDERER_DIR), "index.ts")
    with open(index, encoding="utf-8") as fh:
        offered = re.search(r"SUPPORTED_LOCALES = \[(.*?)\] as const", fh.read(), re.S).group(1)
    assert _RENDERER_LOCALES == sorted(re.findall(r"'([^']+)'", offered))


@pytest.mark.parametrize("locale", [loc for loc in _RENDERER_LOCALES if loc != _EN])
def test_renderer_locale_has_full_parity(locale):
    en, loc = _renderer(_EN), _renderer(locale)
    missing = sorted(set(en) - set(loc))
    orphans = sorted(k for k in loc if _english_key(k, en) is None)
    assert not missing, f"{locale}.json lacks {len(missing)} en.json keys: {missing[:20]}"
    assert not orphans, f"{locale}.json has keys en.json never defines: {orphans[:20]}"


@pytest.mark.parametrize("locale", _RENDERER_LOCALES)
def test_renderer_values_keep_their_placeholders(locale):
    en, loc = _renderer(_EN), _renderer(locale)
    problems = []
    for key, value in sorted(loc.items()):
        english = _english_key(key, en)
        if not isinstance(value, str) or english is None:
            continue
        want = set(_PLACEHOLDER.findall(en[english]))
        got = set(_PLACEHOLDER.findall(value))
        dropped = want - got
        if key.endswith(("_zero", "_one", "_two")):
            dropped -= {"count"}
        if key in _PLACEHOLDER_ALLOWLIST:
            dropped = got = want = set()
        if dropped or got - want:
            problems.append(f"  {key}: en={en[english]!r} vs {locale}={value!r}")
        if _CORRUPTED_TOKEN.search(value):
            problems.append(f"  {key}: corrupted token in {value!r}")
        if _PLACEHOLDER.search(value) and not re.search(r"\w", _PLACEHOLDER.sub("", value)):
            problems.append(f"  {key}: placeholder-only value {value!r}")
        if key in _ENGINE_AGNOSTIC_KEYS and _ENGINE_BRANDS.search(value):
            problems.append(f"  {key}: stage label names an engine: {value!r}")
    assert not problems, f"{locale}.json:\n" + "\n".join(problems[:25])


@pytest.mark.parametrize("locale", [loc for loc in _RENDERER_LOCALES if loc != _EN])
def test_openapi_ui_is_translated_in_every_locale(locale):
    translated = _renderer(locale)
    missing = sorted(_OPENAPI_KEYS - translated.keys())
    assert not missing, f"{locale}.json is missing VoiceStudio API strings: {missing}"


_PLAN_KEYS = tuple(
    f"segment.{name}"
    for name in (
        "plan_apply", "plan_apply_title", "plan_impossible",
        "plan_impossible_title", "plan_tight", "plan_tight_title",
    )
)


@pytest.mark.parametrize("locale", [loc for loc in _RENDERER_LOCALES if loc != _EN])
@pytest.mark.parametrize("key", _REQUIRED_IN_EVERY_LOCALE + _PLAN_KEYS)
def test_every_locale_translates_the_user_facing_status_keys(locale, key):
    """Named-key parity on top of full key parity: these strings sit on default
    paths, so a locale that copies the English text verbatim fails here even
    though the key exists and the placeholders match."""
    value, english = _renderer(locale).get(key), _renderer(_EN)[key]
    assert isinstance(value, str) and value.strip(), f"{locale}.json is missing {key!r}"
    # Standard model acronyms and product/CLI names are shared across locales.
    assert key in {"settings.hf_source_cli_label"} or value != english, (
        f"{locale}.json copies the English {key!r} verbatim ({english!r}); translate it"
    )
