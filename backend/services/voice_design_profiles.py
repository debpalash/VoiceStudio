"""Read saved Voice Design recipes without weakening OmniVoice's tag validation.

The profile's legacy `instruct` always holds sanitized OmniVoice tags. A
versioned recipe (when present) is the source of the complete natural-language
description for free-form engines; older profiles have no recipe and continue
using their tags.
"""
from __future__ import annotations

import json

from omnivoice.utils.voice_design import heal_design_instruct, instruct_from_vd_states


def _field(row, name: str, default=None):
    """Read a sqlite row or mapping key while tolerating legacy schemas."""
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return default


def read_recipe(row) -> dict | None:
    """Parse a saved native Voice Design recipe; omit malformed/legacy data."""
    raw = _field(row, "design_recipe")
    if not raw:
        return None
    try:
        recipe = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(recipe, dict):
        return None
    if not isinstance(recipe.get("description"), str):
        return None
    if not isinstance(recipe.get("picks"), dict):
        return None
    return recipe


def recipe_engine(row) -> str:
    """Return the stored authoring engine or legacy OmniVoice default."""
    recipe = read_recipe(row)
    engine = recipe.get("engine") if recipe else None
    return engine if isinstance(engine, str) and engine else "omnivoice"


def profile_instruct(row, engine_id: str | None = None) -> str:
    """Choose saved instructions for the receiving engine and profile type.

    Tag-only engines always get sanitized OmniVoice taxonomy. For free-form
    engines, clone profiles keep their original instruction text (including
    prose imported from legacy voice bundles), while design profiles use
    their saved native recipe. Legacy designs without a recipe keep their
    validated tags. Omitting the engine retains the original tag-only default.
    """
    tags = heal_design_instruct(
        _field(row, "instruct"), _field(row, "vd_states")
    )
    if not engine_id:
        return tags
    from services.tts_backend import get_backend_class

    cls = get_backend_class(engine_id)
    if getattr(cls, "instruct_vocabulary", "tags") != "freeform":
        return tags

    if _field(row, "kind") != "design":
        # Imported clone profiles may contain genuine style prose; applying
        # OmniVoice's strict tag whitelist here silently changes their voice.
        return _field(row, "instruct") or ""

    recipe = read_recipe(row)
    if recipe is None:
        return tags

    desc = recipe["description"].strip()
    picked = recipe["picks"]
    extras = instruct_from_vd_states(picked)
    return ", ".join(x for x in (desc, extras) if x)
