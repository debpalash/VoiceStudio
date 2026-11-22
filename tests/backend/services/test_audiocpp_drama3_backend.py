"""Unit tests for the audio.cpp Fish Audio Drama 3 backend (#3129)."""
from __future__ import annotations
import sys
import pytest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from engines.audiocpp import DRAMA3_ENGINE_ID, AudioCPPDrama3Backend  # noqa: E402
from engines.audiocpp.bootstrap import BREEZE, DOTS, DRAMA3, DRAMA3_HF_MODEL_REPO, DRAMA3_HF_MODEL_REVISION, ModelPackage  # noqa: E402
from services.tts_backend import _ENGINE_DOCS, _INSTALL_HINTS, _LAZY_REGISTRY, get_backend_class  # noqa: E402


def test_drama3_package_spec_is_isolated_and_pinned():
    assert isinstance(DRAMA3, ModelPackage)
    assert DRAMA3.family == "fish_drama3"
    assert DRAMA3.repo_id == DRAMA3_HF_MODEL_REPO == "Jmica/Drama-3-GGUF"
    assert len(DRAMA3.revision) == 40
    assert DRAMA3.revision == DRAMA3_HF_MODEL_REVISION
    assert DRAMA3.default_package.endswith(".gguf")
    assert DRAMA3.min_vram_gb >= 20.0
    assert DRAMA3.workdir not in {BREEZE.workdir, DOTS.workdir}
    assert DRAMA3.default_port not in {BREEZE.default_port, DOTS.default_port}


def test_drama3_registered_in_tts_backend_registry():
    assert DRAMA3_ENGINE_ID == "audiocpp-drama3"
    assert _LAZY_REGISTRY[DRAMA3_ENGINE_ID] == ("engines.audiocpp", "AudioCPPDrama3Backend")
    assert DRAMA3_ENGINE_ID in _INSTALL_HINTS
    assert DRAMA3_ENGINE_ID in _ENGINE_DOCS
    assert get_backend_class(DRAMA3_ENGINE_ID) is AudioCPPDrama3Backend


def test_drama3_class_attributes_and_multilingual_surface():
    assert AudioCPPDrama3Backend.id == "audiocpp-drama3"
    assert AudioCPPDrama3Backend.supports_voice_design is False
    assert AudioCPPDrama3Backend.supports_num_step is False
    assert AudioCPPDrama3Backend.supports_guidance_scale is False
    assert AudioCPPDrama3Backend().supported_languages == ["multi"]
    assert AudioCPPDrama3Backend().sample_rate == 44100


def test_drama3_rejects_legacy_runtime(tmp_path, monkeypatch):
    fake_bin = tmp_path / "audiocpp_server"; fake_bin.write_text("", encoding="utf-8")
    fake_model = tmp_path / DRAMA3.default_package; fake_model.write_text("", encoding="utf-8")
    monkeypatch.setattr("engines.audiocpp.bootstrap.resolve_server_binary", lambda: fake_bin)
    monkeypatch.setattr("engines.audiocpp.bootstrap.resolve_model_file", lambda pkg=None: fake_model)
    monkeypatch.setattr("engines.audiocpp.bootstrap.runtime_targets", lambda: ("vulkan", "cpu"))
    monkeypatch.setattr("engines.audiocpp.bootstrap.legacy_runtime_version", lambda _b: "v0.7.4")
    ok, reason = AudioCPPDrama3Backend.is_available()
    assert ok is False and "v0.9.4" in reason and "v0.7.4" in reason


def test_drama3_accepts_current_runtime(tmp_path, monkeypatch):
    fake_bin = tmp_path / "audiocpp_server"; fake_bin.write_text("", encoding="utf-8")
    fake_model = tmp_path / DRAMA3.default_package; fake_model.write_text("", encoding="utf-8")
    monkeypatch.setattr("engines.audiocpp.bootstrap.resolve_server_binary", lambda: fake_bin)
    monkeypatch.setattr("engines.audiocpp.bootstrap.resolve_model_file", lambda pkg=None: fake_model)
    monkeypatch.setattr("engines.audiocpp.bootstrap.runtime_targets", lambda: ("vulkan", "cpu"))
    monkeypatch.setattr("engines.audiocpp.bootstrap.legacy_runtime_version", lambda _b: None)
    ok, reason = AudioCPPDrama3Backend.is_available()
    assert ok is True and reason == "ready"


def test_drama3_request_options_ignores_separate_instructions():
    instruct, guidance = AudioCPPDrama3Backend()._request_options(
        {"instruct": "angry", "description": "deep voice", "guidance_scale": 2.5}, ref_audio=None,
    )
    assert instruct is None and guidance == 1.0


@pytest.mark.model_licence_gate
def test_drama3_first_use_licence_gate_blocks_until_accepted(monkeypatch):
    from services import model_acceptance, settings_store

    store: dict[str, str] = {}
    monkeypatch.setattr(settings_store, "get_text", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(settings_store, "set_text", lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(settings_store, "get_license_accepted", lambda engine: False)

    st = model_acceptance.status("Jmica/Drama-3-GGUF")
    assert st["required"] is True
    assert st["accepted"] is False

    with pytest.raises(model_acceptance.ModelLicenceNotAccepted):
        model_acceptance.ensure_engine_accepted("audiocpp-drama3")

    model_acceptance.accept("Jmica/Drama-3-GGUF", st["fingerprint"])
    model_acceptance.ensure_engine_accepted("audiocpp-drama3")
