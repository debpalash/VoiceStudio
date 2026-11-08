"""Regional language variants offered as their own language choices (#3143).

Brazilian and European Portuguese differ in spelling, vocabulary and number
words, so the pickers list them separately instead of one "Portuguese". The
bundled language vocabulary (``omnivoice.utils.lang_map``) and most engines
know a single Portuguese, so a variant name is resolved here into:

* the base language every engine already understands ("Portuguese" / "pt"),
* the BCP-47 dialect the user asked for ("pt-BR" / "pt-PT").

A plain "Portuguese" or "pt" — every saved setting from before the split —
means Brazilian Portuguese, the owner-set default (2026-10-26).

Pure data, no model imports: the TTS adapters, text normalization,
pronunciation scopes and dub translation all read the same table.
"""
from __future__ import annotations

import re
from typing import Optional

#: Picker name (lowercase) -> (base picker name, dialect).
VARIANT_NAMES: dict[str, tuple[str, str]] = {
    "portuguese (brazil)": ("Portuguese", "pt-BR"),
    "portuguese (portugal)": ("Portuguese", "pt-PT"),
    # Chinese written standards (#3685). The pickers offer "Chinese (Simplified)"
    # and "Chinese (Traditional)" as their own choices, while every engine
    # vocabulary names a single Chinese ("chinese" -> zh). Without this fold the
    # pre-flight gate compares the picker's "cmn-hans" against the engine's
    # "chinese"/"zh" and reports the target as unsupported, so Generate Dub
    # refuses a language the engine renders fine (and finite engines never
    # advertise it at all, since ``with_variant_names`` only adds a variant
    # whose base language is present).
    #
    # Deliberately absent from ``DIALECTS`` below: that table makes
    # ``split_target`` rewrite a regional target into its base, which would
    # collapse "zh-TW" to "zh" and translate Traditional Chinese into
    # Simplified. The script reaches what needs it through ``dialect_for``.
    "chinese (simplified)": ("Chinese", "zh-CN"),
    "chinese (traditional)": ("Chinese", "zh-TW"),
}

#: Base ISO code -> dialect used when the caller named only the language.
DEFAULT_DIALECTS: dict[str, str] = {"pt": "pt-BR"}

#: Every dialect each base language offers as a separate choice.
DIALECTS: dict[str, tuple[str, ...]] = {"pt": ("pt-BR", "pt-PT")}

#: Picker name (lowercase) -> base ISO code, for resolvers keyed by language id.
VARIANT_CODES: dict[str, str] = {
    name: dialect.split("-", 1)[0] for name, (_base, dialect) in VARIANT_NAMES.items()
}

#: Base language names (lowercase) that carry a default dialect.
_BASE_NAMES: dict[str, str] = {"portuguese": "pt", "pt": "pt", "por": "pt"}

_TAG_RE = re.compile(r"^([a-z]{2,3})[-_]([a-z]{2})$")


def _key(language: object) -> str:
    return " ".join(str(language).split()).lower() if isinstance(language, str) else ""


def is_variant(language: object) -> bool:
    """True for a picker name that selects a regional variant."""
    return _key(language) in VARIANT_NAMES


def base_language(language):
    """The language an engine understands: variant names become their base
    picker name ("Portuguese (Portugal)" -> "Portuguese"); anything else is
    returned unchanged, including ``None`` and ISO tags engines already parse.
    A list or tuple (native batch renders) is converted item by item."""
    if isinstance(language, (list, tuple)):
        return type(language)(base_language(item) for item in language)
    variant = VARIANT_NAMES.get(_key(language))
    return variant[0] if variant else language


def canonical_dialect(dialect: object) -> Optional[str]:
    """``"pt_br"`` / ``"PT-br"`` -> ``"pt-BR"``; ``None`` for anything else."""
    match = _TAG_RE.match(_key(dialect))
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2).upper()}"


def dialect_for(language: object) -> Optional[str]:
    """The dialect a request language means, or ``None`` when it has none.

    An explicit variant name or region tag wins; a bare base language
    ("Portuguese", "pt") gets its default dialect. Region tags only count for
    languages that offer variants, so "en-US" stays ``None`` here.
    """
    key = _key(language)
    if not key:
        return None
    if key in VARIANT_NAMES:
        return VARIANT_NAMES[key][1]
    tag = canonical_dialect(key)
    if tag:
        base = tag.split("-", 1)[0]
        return tag if tag in DIALECTS.get(base, ()) else None
    code = _BASE_NAMES.get(key)
    return DEFAULT_DIALECTS.get(code) if code else None


def default_dialect(code: object) -> Optional[str]:
    """Default dialect for a base ISO code ("pt" -> "pt-BR")."""
    return DEFAULT_DIALECTS.get(_key(code))


def split_target(target: object) -> tuple[object, Optional[str]]:
    """Split a translation target that carries its region ("pt-PT") into the
    base code and dialect (``("pt", "pt-PT")``). Other targets pass through
    with no dialect."""
    tag = canonical_dialect(target)
    if tag:
        base = tag.split("-", 1)[0]
        if tag in DIALECTS.get(base, ()):
            return base, tag
    return target, None


def with_variant_names(options: list[str]) -> list[str]:
    """Add the variant picker names wherever their base language is offered.

    ``options`` are lowercase picker names (``language_options`` output); the
    result stays sorted and lowercase.
    """
    present = set(options)
    extra = {name for name, (base, _dialect) in VARIANT_NAMES.items() if base.lower() in present}
    return sorted(present | extra)
