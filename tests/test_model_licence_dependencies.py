"""Catalogue rows carry every licence their engines gate on (#3855).

OmniVoice's catalogue row offered only its own licence, but generation gates
on every registry row whose ``engines`` include ``omnivoice`` — both
``k2-fsa/OmniVoice`` and ``eustlb/higgs-audio-v2-tokenizer``. Accepting from
the catalogue therefore still left ``POST /generate`` returning 403
``model_licence_required`` for the tokenizer, which has no row of its own.

The row now also carries ``license_dependencies``: the acceptance states of
the other gated repos its engines need, so the dialog can list and clear all
of them and the "Accept licence to use" badge stays until none are pending.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

pytestmark = pytest.mark.model_licence_gate  # real enforcement, see conftest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

OMNI = "k2-fsa/OmniVoice"
TOKENIZER = "eustlb/higgs-audio-v2-tokenizer"


@pytest.fixture
def models():
    return importlib.import_module("api.routers.setup.models")


@pytest.fixture
def ma():
    return importlib.import_module("services.model_acceptance")


@pytest.fixture
def store(monkeypatch):
    settings_store = importlib.import_module("services.settings_store")
    data: dict[str, str] = {}
    monkeypatch.setattr(settings_store, "get_text", lambda key, default=None: data.get(key, default))
    monkeypatch.setattr(settings_store, "set_text", lambda key, value: data.__setitem__(key, value))
    monkeypatch.setattr(settings_store, "get_license_accepted", lambda engine: False)
    return data


def test_omnivoice_row_lists_the_tokenizer_licence(models):
    omni = models.get_model_catalog().get(OMNI)
    deps = models._model_licence_dependencies(omni, models.model_key(omni))
    ids = [d["repo_id"] for d in deps]
    assert TOKENIZER in ids
    # The row never lists itself, and only gated repos are offered.
    assert OMNI not in ids
    assert all(d["required"] for d in deps)


def test_rows_without_extra_gated_repos_list_no_dependencies(models):
    for model in models.KNOWN_MODELS:
        key = models.model_key(model)
        deps = models._model_licence_dependencies(model, key)
        assert key not in [d["repo_id"] for d in deps]
        assert model["repo_id"] not in [d["repo_id"] for d in deps]


def test_list_models_payload_carries_license_dependencies(models, monkeypatch):
    monkeypatch.setattr(models, "_scan_cache_on_disk", lambda: {})
    monkeypatch.setattr(models, "invalidate_cache", lambda: None)
    rows = {row["repo_id"]: row for row in models.list_models()["models"]}
    assert all("license_dependencies" in row for row in rows.values())
    omni = rows[OMNI]
    assert TOKENIZER in [d["repo_id"] for d in omni["license_dependencies"]]


def test_accepting_only_the_rows_own_licence_still_blocks_the_engine(ma, store):
    with pytest.raises(ma.ModelLicenceNotAccepted):
        ma.ensure_engine_accepted("omnivoice")
    ma.accept(OMNI, ma.status(OMNI)["fingerprint"])
    with pytest.raises(ma.ModelLicenceNotAccepted) as excinfo:
        ma.ensure_engine_accepted("omnivoice")
    assert [m["repo_id"] for m in excinfo.value.models] == [TOKENIZER]


def test_accepting_every_dependency_clears_the_engine_gate(ma, store):
    for repo_id in (OMNI, TOKENIZER):
        ma.accept(repo_id, ma.status(repo_id)["fingerprint"])
    ma.ensure_engine_accepted("omnivoice")
    assert models_pending(ma) == []


def models_pending(ma) -> list[str]:
    return [
        repo_id
        for repo_id in ma.repos_for_engine("omnivoice")
        if ma.status(repo_id)["required"] and not ma.status(repo_id)["accepted"]
    ]
