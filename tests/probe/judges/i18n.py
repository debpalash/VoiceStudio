"""i18n judges — enforce the localization hard rule structurally.

Localization is a CLAUDE.md hard rule: all user-facing text goes through the i18n
layer. These judges check the locale files for *structural* correctness:
  - every locale file is valid JSON (blocking)
  - no locale has ORPHAN keys absent from the reference locale — those are dead
    or mistyped keys that will silently fall back and never render (advisory)
  - translation coverage per locale (advisory — incomplete translations fall back
    to the reference and are tolerated, so this reports a trend, never gates)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..spec import JudgeResult


def _flatten(obj: dict, prefix: str = "") -> dict:
    out: dict = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _load_locales(locales_dir: str) -> tuple[dict[str, dict], list[str]]:
    """Return ({locale_name: flattened_keys}, [bad_files])."""
    locales, bad = {}, []
    for path in sorted(Path(locales_dir).glob("*.json")):
        try:
            locales[path.stem] = _flatten(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            bad.append(path.name)
    return locales, bad


def locale_valid_json(locales_dir: str) -> JudgeResult:
    locales, bad = _load_locales(locales_dir)
    if not locales and not bad:
        # No locale files found at all — the directory is empty or missing.
        # Treat as a hard failure so a missing/empty locales dir can't silently
        # pass a blocking gate.
        return JudgeResult(
            name="locale_valid_json",
            passed=False,
            measured=0,
            detail=f"no locale JSON files found in {locales_dir!r}",
        )
    return JudgeResult(
        name="locale_valid_json",
        passed=not bad,
        measured=len(locales),
        detail=f"{len(locales)} locale files parse" if not bad else f"invalid JSON: {bad}",
    )


_CLDR_PLURAL = re.compile(r"^(.*)_(?:zero|one|two|few|many|other)$")


def _answers_to_reference(key: str, ref: set) -> bool:
    """A key is not an orphan when the reference has it, or when it is a CLDR
    plural form the reference lacks (ar ``_few``, ru ``_many``) whose
    ``_other`` form the reference defines."""
    if key in ref:
        return True
    match = _CLDR_PLURAL.match(key)
    return bool(match and f"{match.group(1)}_other" in ref)


def locale_no_orphan_keys(locales_dir: str, reference: str = "en") -> JudgeResult:
    locales, _ = _load_locales(locales_dir)
    ref = set(locales.get(reference, {}))
    if not ref:
        return JudgeResult(name="locale_no_orphan_keys", passed=False,
                           detail=f"reference locale {reference!r} not found/empty")
    offenders = {}
    for name, keys in locales.items():
        if name == reference:
            continue
        orphans = {key for key in keys if not _answers_to_reference(key, ref)}
        if orphans:
            offenders[name] = sorted(orphans)[:5]
    ok = not offenders
    return JudgeResult(
        name="locale_no_orphan_keys",
        passed=ok,
        measured=len(offenders),
        detail=f"no orphan keys across {len(locales)} locales"
        if ok else f"orphan keys (absent from {reference}): {offenders}",
    )


def locale_coverage(locales_dir: str, reference: str = "en", min_pct: float = 0.0) -> JudgeResult:
    """Advisory: lowest translation coverage across locales. Never gates —
    untranslated keys fall back to the reference."""
    locales, _ = _load_locales(locales_dir)
    ref = set(locales.get(reference, {}))
    if not ref:
        return JudgeResult(name="locale_coverage", passed=None, detail="no reference locale")
    worst, worst_name = 1.0, reference
    for name, keys in locales.items():
        if name == reference:
            continue
        pct = len(set(keys) & ref) / len(ref)
        if pct < worst:
            worst, worst_name = pct, name
    return JudgeResult(
        name="locale_coverage",
        passed=worst >= min_pct,
        measured=round(worst, 3),
        detail=f"lowest coverage: {worst_name} at {worst:.0%} of {len(ref)} reference keys",
    )
