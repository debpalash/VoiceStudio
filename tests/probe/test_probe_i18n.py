"""i18n — locale judges (synthetic) + the localization spec against the real
locale files. Orphan-key + coverage findings are advisory (reported, not gating).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import spec as probe_spec
from .judges import i18n as I

_SPEC = os.path.join(os.path.dirname(__file__), "specs", "i18n_parity.probe.yaml")
_LOCALES = str(Path(__file__).resolve().parents[2] / "electron" / "src" / "shared" / "i18n" / "locales")


def _write_locales(tmp_path, mapping):
    for name, obj in mapping.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(obj), encoding="utf-8")
    return str(tmp_path)


def test_valid_json_and_orphans_synthetic(tmp_path):
    d = _write_locales(tmp_path, {
        "en": {"a": 1, "b": {"c": 2}},
        "fr": {"a": 1, "b": {"c": 2}},            # clean
        "de": {"a": 1, "b": {"c": 2}, "x": 9},    # orphan key 'x'
    })
    assert I.locale_valid_json(d).passed is True
    res = I.locale_no_orphan_keys(d, "en")
    assert res.passed is False and "de" in res.detail


def test_invalid_json_fails(tmp_path):
    (tmp_path / "en.json").write_text('{"a":1}', encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert I.locale_valid_json(str(tmp_path)).passed is False


def test_coverage_reports_lowest(tmp_path):
    d = _write_locales(tmp_path, {
        "en": {"a": 1, "b": 2, "c": 3, "d": 4},
        "fr": {"a": 1, "b": 2},  # 50%
    })
    res = I.locale_coverage(d, "en")
    assert res.measured == 0.5 and "fr" in res.detail


def test_real_locales_spec(probe_report):
    """Blocking: all real locale files are valid JSON. Orphan-key/coverage run as
    advisory. The orphans were cleaned up — dead `gallery.cat_*` keys (renamed to
    `archetypes.use_*`) removed from all non-en locales, and the used-but-missing
    `bootstrap.lines` added to en — so the orphan-key judge now passes and guards
    against regressions."""
    spec = probe_spec.load_spec(_SPEC)
    results = probe_spec.run_judges(spec, {"locales_dir": _LOCALES})
    probe_report.record(spec, results)
    assert probe_spec.blocking_failures(results) == []
    # Orphan-key judge now passes: no non-en locale carries a key absent from en.
    assert any(r.name == "locale_no_orphan_keys" and r.passed for r in results)
