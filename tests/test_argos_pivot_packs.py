"""Argos pairs with no direct pack translate through English.

The Argos index is almost entirely ``xx → en`` and ``en → xx``; Argos itself
chains installed packs, so Spanish → French works once es → en and en → fr
are installed. The pack check and installer must agree with that, or a
non-English source dubbed into another non-English language can never start.
"""
from __future__ import annotations

import sys
import types

import pytest


class _Pack:
    def __init__(self, from_code: str, to_code: str):
        self.from_code = from_code
        self.to_code = to_code

    def download(self):
        return self


@pytest.fixture
def argos(monkeypatch):
    installed: list[tuple[str, str]] = []
    index = [_Pack(a, b) for a, b in [("es", "en"), ("en", "fr"), ("en", "es"), ("pt", "es")]]

    pkg = types.ModuleType("argostranslate.package")
    pkg.get_installed_packages = lambda: [
        types.SimpleNamespace(from_code=a, to_code=b) for a, b in installed
    ]
    pkg.update_package_index = lambda: None
    pkg.get_available_packages = lambda: index
    pkg.install_from_path = lambda pack: installed.append((pack.from_code, pack.to_code))
    root = types.ModuleType("argostranslate")
    root.package = pkg
    monkeypatch.setitem(sys.modules, "argostranslate", root)
    monkeypatch.setitem(sys.modules, "argostranslate.package", pkg)
    monkeypatch.delenv("OMNIVOICE_CACHE_DIR", raising=False)
    return installed


def _ready(status: dict) -> dict:
    return {p["target_lang"]: p["installed"] for p in status["pairs"]}


def test_pair_via_english_counts_as_installed(argos):
    from services import translation_engines

    argos.extend([("es", "en"), ("en", "fr")])

    status = translation_engines.argos_pack_status("es", ["fr", "de"])

    assert _ready(status) == {"fr": True, "de": False}


def test_install_routes_a_missing_pair_through_english(argos):
    from services import translation_engines

    status = translation_engines.install_argos_packs("es", ["fr"])

    assert argos == [("es", "en"), ("en", "fr")]
    assert _ready(status) == {"fr": True}


def test_install_prefers_a_direct_pack(argos):
    from services import translation_engines

    translation_engines.install_argos_packs("pt", ["es"])

    assert argos == [("pt", "es")]


def test_install_still_reports_a_pair_argos_cannot_reach(argos):
    from services import translation_engines

    with pytest.raises(ValueError, match="es → de"):
        translation_engines.install_argos_packs("es", ["de"])


@pytest.mark.parametrize("existing,available", [
    (("es", "en"), ("en", "fr")),
    (("en", "fr"), ("es", "en")),
])
def test_install_reuses_pivot_leg_removed_from_index(argos, monkeypatch, existing, available):
    """An installed pack remains usable when it is no longer indexed."""
    from services import translation_engines
    import argostranslate.package

    argos.append(existing)
    downloads = []
    pack = _Pack(*available)
    monkeypatch.setattr(pack, "download", lambda: downloads.append(available) or pack)
    monkeypatch.setattr(argostranslate.package, "get_available_packages", lambda: [pack])

    status = translation_engines.install_argos_packs("es", ["fr"])

    assert argos == [existing, available]
    assert downloads == [available]
    assert _ready(status) == {"fr": True}
