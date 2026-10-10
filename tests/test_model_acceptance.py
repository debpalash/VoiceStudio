"""Per-model licence acceptance: gated categories, fingerprints, legacy carry-over."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from services import model_acceptance as ma
from services import settings_store

pytestmark = pytest.mark.model_licence_gate  # real enforcement, see conftest

_MODULES = {
    "ma": "services.model_acceptance",
    "settings_store": "services.settings_store",
}


@pytest.fixture(autouse=True)
def _current_backend_modules():
    """Rebind to the modules the code under test resolves right now.

    Other suites replace backend modules in ``sys.modules`` (see
    tests/backend_module_state.py); a collection-time import can then be a twin
    of what the app code uses, so an exception class or a patched settings
    store would belong to a different copy.
    """
    for attr, name in _MODULES.items():
        globals()[attr] = importlib.import_module(name)


@pytest.fixture
def store(monkeypatch):
    data: dict[str, str] = {}
    monkeypatch.setattr(settings_store, "get_text", lambda key, default=None: data.get(key, default))
    monkeypatch.setattr(settings_store, "set_text", lambda key, value: data.__setitem__(key, value))
    monkeypatch.setattr(
        settings_store, "get_license_accepted",
        lambda engine: data.get(f"{engine}_license_accepted") == "1",
    )
    return data


@pytest.mark.parametrize(("repo_id", "required"), [
    ("k2-fsa/OmniVoice", True),            # non-commercial
    ("Supertone/supertonic-3", True),      # conditions
    ("iic/SenseVoiceSmall", True),         # unknown
    ("nobody/not-in-registry", True),      # missing record fails closed
    ("Systran/faster-whisper-large-v3", False),  # MIT
])
def test_only_commercial_category_models_skip_acceptance(store, repo_id, required):
    state = ma.status(repo_id)
    assert state["required"] is required
    assert state["accepted"] is (not required)


def test_accept_binds_to_the_exact_terms_shown(store):
    state = ma.status("k2-fsa/OmniVoice")
    with pytest.raises(ValueError, match="terms_changed"):
        ma.accept("k2-fsa/OmniVoice", "v2:" + "0" * 64)
    assert ma.accept("k2-fsa/OmniVoice", state["fingerprint"])["accepted"] is True
    ma.ensure_accepted(["k2-fsa/OmniVoice"])


def test_changed_terms_ask_again(store, monkeypatch):
    ma.accept("k2-fsa/OmniVoice", ma.status("k2-fsa/OmniVoice")["fingerprint"])
    original = ma._disclosure
    monkeypatch.setattr(ma, "_disclosure", lambda r: {**original(r), "license": "CC-BY-NC-4.0 v2"})
    assert ma.status("k2-fsa/OmniVoice")["accepted"] is False


def test_revoke_blocks_again(store):
    ma.accept("k2-fsa/OmniVoice", ma.status("k2-fsa/OmniVoice")["fingerprint"])
    assert ma.revoke("k2-fsa/OmniVoice")["accepted"] is False
    with pytest.raises(ma.ModelLicenceNotAccepted):
        ma.ensure_accepted(["k2-fsa/OmniVoice"])


def test_error_lists_every_unaccepted_model_with_structured_detail(store):
    with pytest.raises(ma.ModelLicenceNotAccepted) as err:
        ma.ensure_engine_accepted("omnivoice")
    detail = err.value.detail()
    assert detail["code"] == "model_licence_required"
    assert {m["repo_id"] for m in detail["models"]} == {"k2-fsa/OmniVoice", "eustlb/higgs-audio-v2-tokenizer"}
    assert all(m["fingerprint"].startswith("v2:") for m in detail["models"])


def test_engine_identity_selects_the_concrete_model(store):
    # A commercial-category mlx-audio model needs no acceptance even though
    # other models of the same engine do.
    ma.ensure_engine_accepted("mlx-audio", "mlx-community/Kokoro-82M-bf16")
    with pytest.raises(ma.ModelLicenceNotAccepted):
        ma.ensure_engine_accepted("mlx-audio", "mlx-community/Llama-OuteTTS-1.0-1B-4bit")


def test_existing_supertonic_acceptance_carries_over_until_revoked(store):
    store["supertonic3_license_accepted"] = "1"
    assert ma.status("Supertone/supertonic-3")["accepted"] is True
    ma.revoke("Supertone/supertonic-3")
    assert ma.status("Supertone/supertonic-3")["accepted"] is False


def test_unreadable_settings_fail_closed(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("db locked")
    monkeypatch.setattr(settings_store, "get_text", boom)
    monkeypatch.setattr(settings_store, "get_license_accepted", boom)
    assert ma.status("k2-fsa/OmniVoice")["accepted"] is False


def test_unreadable_registry_fails_closed(store, monkeypatch):
    def boom():
        raise OSError("registry missing")
    monkeypatch.setattr(ma, "_registry", boom)
    with pytest.raises(ma.ModelLicenceNotAccepted) as err:
        ma.ensure_engine_accepted("omnivoice")
    assert err.value.reason == "registry_unreadable"
    assert "unreadable" in str(err.value)


def test_local_model_folder_is_not_a_registry_model(store):
    ma.ensure_engine_accepted("mlx-audio", "/Users/me/models/kokoro")
    assert ma.repos_for_engine("mlx-audio", "~/models/kokoro") == []


def test_unreadable_store_is_not_treated_as_absent(store, monkeypatch):
    """A revoked Supertonic-3 must not fall back to its legacy acceptance."""
    store["supertonic3_license_accepted"] = "1"
    def boom(*_a, **_k):
        raise OSError("db locked")
    monkeypatch.setattr(settings_store, "get_text", boom)
    assert ma.status("Supertone/supertonic-3")["accepted"] is False


def test_new_acceptance_syncs_the_supertonic_engine_flag(store):
    fp = ma.status("Supertone/supertonic-3")["fingerprint"]
    ma.accept("Supertone/supertonic-3", fp)
    assert store["supertonic3_license_accepted"] == "1"
    ma.revoke("Supertone/supertonic-3")
    assert store["supertonic3_license_accepted"] == "0"


def test_legacy_engine_toggle_syncs_the_new_record(store):
    ma.sync_from_engine("supertonic3", True)
    assert ma.status("Supertone/supertonic-3")["accepted"] is True
    ma.sync_from_engine("supertonic3", False)
    assert ma.status("Supertone/supertonic-3")["accepted"] is False
    ma.sync_from_engine("pockettts", True)  # unrelated engines are untouched


def _info_with(monkeypatch, **changes):
    original = ma._disclosure
    monkeypatch.setattr(ma, "_disclosure", lambda r: {**original(r), **changes})


def test_accept_and_withdraw_store_time_hash_and_history(store):
    fp = ma.status("k2-fsa/OmniVoice")["fingerprint"]
    ma.accept("k2-fsa/OmniVoice", fp)
    ma.revoke("k2-fsa/OmniVoice")
    ma.accept("k2-fsa/OmniVoice", fp)
    details = ma.details("k2-fsa/OmniVoice")
    state = details["acceptance"]
    assert state["state"] == "accepted"
    assert state["accepted_at"] and state["last_action"]["fingerprint"] == fp
    assert [h["action"] for h in details["history"]] == ["accepted", "withdrawn", "accepted"]
    assert all(h["at"].endswith("+00:00") and h["fingerprint"].startswith("v2:") for h in details["history"])
    assert details["info"]["license"] == state["license"]


def test_withdrawn_is_reported_as_withdrawn(store):
    ma.accept("k2-fsa/OmniVoice", ma.status("k2-fsa/OmniVoice")["fingerprint"])
    assert ma.revoke("k2-fsa/OmniVoice")["state"] == "withdrawn"


@pytest.mark.parametrize("field", ["notes", "credit", "evidence_url", "runtime_revision"])
def test_any_visible_terms_change_asks_again_and_names_it(store, monkeypatch, field):
    ma.accept("k2-fsa/OmniVoice", ma.status("k2-fsa/OmniVoice")["fingerprint"])
    _info_with(monkeypatch, **{field: "updated by a later registry"})
    state = ma.status("k2-fsa/OmniVoice")
    assert state["state"] == "terms_updated" and state["accepted"] is False
    assert state["changed_fields"] == [field]
    assert state["accepted_at"]


def test_licence_document_change_asks_again(store, monkeypatch):
    ma.accept("k2-fsa/OmniVoice", ma.status("k2-fsa/OmniVoice")["fingerprint"])
    monkeypatch.setattr(ma, "_documents", lambda repo_id: [["terms", "f" * 64]])
    assert ma.status("k2-fsa/OmniVoice")["changed_fields"] == ["documents"]


def test_a_recheck_date_alone_does_not_ask_again(store, monkeypatch):
    ma.accept("k2-fsa/OmniVoice", ma.status("k2-fsa/OmniVoice")["fingerprint"])
    _info_with(monkeypatch, evidence_checked_at="2099-01-01")
    assert ma.status("k2-fsa/OmniVoice")["state"] == "accepted"


def test_earlier_plain_string_records_still_read(store):
    store[ma._key("k2-fsa/OmniVoice")] = "v1:" + "a" * 64
    state = ma.status("k2-fsa/OmniVoice")
    assert state["state"] == "terms_updated" and state["changed_fields"] == []
    store[ma._key("k2-fsa/OmniVoice")] = "revoked"
    assert ma.status("k2-fsa/OmniVoice")["state"] == "withdrawn"


def test_history_is_bounded(store):
    fp = ma.status("k2-fsa/OmniVoice")["fingerprint"]
    for _ in range(15):
        ma.accept("k2-fsa/OmniVoice", fp)
        ma.revoke("k2-fsa/OmniVoice")
    assert len(ma.details("k2-fsa/OmniVoice")["history"]) == 20
