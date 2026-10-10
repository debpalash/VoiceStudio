"""Licence acceptance at the ASR, dictation, diarisation and translation choke points (#2689).

Every gate resolves the concrete model it is about to use: an unaccepted gated
model is refused before weights load, a commercial sibling of the same engine
is never blocked by it, and an accepted model proceeds. Offline, no ML deps.
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from services import asr_backend as ab
from services import model_acceptance as ma
from services import settings_store

# Real acceptance state: tests/conftest.py otherwise reads every model as accepted.
pytestmark = pytest.mark.model_licence_gate

_MODULES = {
    "ab": "services.asr_backend",
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

GATED_MLX = "mlx-community/whisper-large-v3-turbo"        # unknown → gated
COMMERCIAL_MLX = "mlx-community/whisper-large-v3-mlx"      # MIT → open
GATED_SHERPA = "sherpa-whisper-tiny"                       # unknown → gated
COMMERCIAL_SHERPA = "sherpa-zipformer-en-20m"              # Apache → open
SORTFORMER = "audio-cpp/audio.cpp-gguf"                    # non-commercial


@pytest.fixture
def store(monkeypatch):
    data: dict[str, str] = {}
    monkeypatch.setattr(settings_store, "get_text", lambda key, default=None: data.get(key, default))
    monkeypatch.setattr(settings_store, "set_text", lambda key, value: data.__setitem__(key, value))
    monkeypatch.setattr(settings_store, "get_license_accepted", lambda engine: False)
    return data


def _accept(repo_id: str) -> None:
    ma.accept(repo_id, ma.status(repo_id)["fingerprint"])


class _Loaded(Exception):
    """Raised by a stub ensure_loaded: the gate let the load through."""


def _stub_load(monkeypatch, backend):
    def ensure_loaded():
        raise _Loaded(backend._model_name)
    monkeypatch.setattr(backend, "ensure_loaded", ensure_loaded)
    return backend


# ── Concrete repo resolution ────────────────────────────────────────────────

def test_backend_repo_resolution_names_the_concrete_model(tmp_path):
    snapshot = "/cache/hub/models--Systran--faster-whisper-small/snapshots/abc"
    assert ab.backend_model_repos(ab.FasterWhisperBackend(model_name=snapshot)) == [
        "Systran/faster-whisper-small"
    ]
    assert ab.backend_model_repos(ab.FasterWhisperBackend(model_name="medium")) == [
        "Systran/faster-whisper-medium"
    ]
    # A user's own local model directory has no registry identity.
    assert ab.backend_model_repos(ab.FasterWhisperBackend(model_name=str(tmp_path))) == []
    assert ab.backend_model_repos(ab.MLXWhisperBackend(model_name=GATED_MLX)) == [GATED_MLX]
    assert ab.backend_model_repos(ab.SherpaDictationBackend(model_id=GATED_SHERPA)) == [
        "csukuangfj/sherpa-onnx-whisper-tiny"
    ]


# ── load_active_asr_backend (dub / batch / transcribe / OpenAI / QC) ────────

def test_offline_asr_refuses_unaccepted_model_before_loading(store, monkeypatch):
    backend = _stub_load(monkeypatch, ab.MLXWhisperBackend(model_name=GATED_MLX))
    monkeypatch.setattr(ab, "get_active_asr_backend", lambda **_: backend)
    with pytest.raises(ma.ModelLicenceNotAccepted) as err:
        ab.load_active_asr_backend()
    assert err.value.detail()["code"] == "model_licence_required"
    assert [m["repo_id"] for m in err.value.detail()["models"]] == [GATED_MLX]

    _accept(GATED_MLX)
    with pytest.raises(_Loaded):
        ab.load_active_asr_backend()


def test_offline_asr_commercial_sibling_is_not_blocked(store, monkeypatch):
    backend = _stub_load(monkeypatch, ab.MLXWhisperBackend(model_name=COMMERCIAL_MLX))
    monkeypatch.setattr(ab, "get_active_asr_backend", lambda **_: backend)
    with pytest.raises(_Loaded):
        ab.load_active_asr_backend()


def test_offline_asr_licence_error_never_falls_through_to_another_engine(store, monkeypatch):
    picks = iter([
        _stub_load(monkeypatch, ab.MLXWhisperBackend(model_name=GATED_MLX)),
        _stub_load(monkeypatch, ab.MLXWhisperBackend(model_name=COMMERCIAL_MLX)),
    ])
    monkeypatch.setattr(ab, "get_active_asr_backend", lambda **_: next(picks))
    with pytest.raises(ma.ModelLicenceNotAccepted):
        ab.load_active_asr_backend()


# ── Capture / dictation ─────────────────────────────────────────────────────

@pytest.fixture
def capture_whisper(monkeypatch):
    """Capture picker resolving to the MLX Turbo tier, no sherpa, fresh singleton."""
    monkeypatch.setattr(ab, "_capture_backend", None)
    monkeypatch.setattr(ab, "_capture_backend_key", None)
    monkeypatch.setattr(ab, "dictation_model_id", lambda: None)
    monkeypatch.setattr(ab, "_capture_prefers_parakeet", lambda: False)
    monkeypatch.setattr(ab.MLXWhisperBackend, "is_available", classmethod(lambda cls: (True, "")))


def test_capture_refuses_unaccepted_model_and_rechecks_the_warm_singleton(store, capture_whisper):
    with pytest.raises(ma.ModelLicenceNotAccepted):
        ab.get_capture_asr_backend()
    _accept(GATED_MLX)
    backend = ab.get_capture_asr_backend()
    assert backend._model_name == GATED_MLX
    ma.revoke(GATED_MLX)
    # Same cached singleton, but revocation applies on the next handout.
    with pytest.raises(ma.ModelLicenceNotAccepted):
        ab.get_capture_asr_backend()


def test_capture_commercial_sibling_is_not_blocked(store, capture_whisper, monkeypatch):
    monkeypatch.setattr(ab, "_MLX_MODEL_TURBO", COMMERCIAL_MLX)
    assert ab.get_capture_asr_backend()._model_name == COMMERCIAL_MLX


@pytest.fixture
def no_sherpa_build(monkeypatch):
    built: list[str] = []

    class Recorder:
        def __init__(self, model_id):
            built.append(model_id)
            self.model_id = model_id

    monkeypatch.setattr(ab, "_capture_backend", None)
    monkeypatch.setattr(ab, "_capture_backend_key", None)
    monkeypatch.setattr(ab, "SherpaDictationBackend", Recorder)
    return built


def test_sherpa_dictation_refuses_unaccepted_model_before_building(store, no_sherpa_build):
    with pytest.raises(ma.ModelLicenceNotAccepted):
        ab.get_sherpa_dictation_backend(GATED_SHERPA)
    assert no_sherpa_build == []
    _accept("csukuangfj/sherpa-onnx-whisper-tiny")
    assert ab.get_sherpa_dictation_backend(GATED_SHERPA).model_id == GATED_SHERPA


def test_sherpa_dictation_commercial_sibling_is_not_blocked(store, no_sherpa_build):
    assert ab.get_sherpa_dictation_backend(COMMERCIAL_SHERPA).model_id == COMMERCIAL_SHERPA


def test_live_dictation_preflight_returns_typed_detail(store, monkeypatch):
    from api.routers import capture_ws
    from services import sherpa_dictation

    monkeypatch.setattr(ab.SherpaDictationBackend, "is_available", classmethod(lambda cls: (True, "")))
    detail = capture_ws._dictation_licence_error(sherpa_dictation.get_spec(GATED_SHERPA))
    assert detail["code"] == "model_licence_required"
    assert detail["models"][0]["repo_id"] == "csukuangfj/sherpa-onnx-whisper-tiny"
    assert capture_ws._dictation_licence_error(sherpa_dictation.get_spec(COMMERCIAL_SHERPA)) is None


# ── Reference transcription (voice cloning) ─────────────────────────────────

def test_reference_transcription_skips_unaccepted_candidate(store, monkeypatch):
    calls: list[str] = []

    def candidate(backend, text):
        monkeypatch.setattr(
            backend, "transcribe",
            lambda *_a, **_k: calls.append(backend._model_name) or {"text": text},
        )
        return backend

    gated = candidate(ab.MLXWhisperBackend(model_name=GATED_MLX), "gated")
    open_ = candidate(ab.MLXWhisperBackend(model_name=COMMERCIAL_MLX), "open")
    assert ab._try_reference_candidates([gated, open_], "clip.wav", release_after=True) == "open"
    assert calls == [COMMERCIAL_MLX]


def test_reference_fallback_never_picks_an_unaccepted_dictation_model(store, monkeypatch):
    from services import sherpa_dictation

    monkeypatch.setattr(ab.FasterWhisperBackend, "is_available", classmethod(lambda cls: (False, "")))
    monkeypatch.setattr(sherpa_dictation, "is_installed", lambda spec: True)
    monkeypatch.setattr(ab, "get_sherpa_dictation_backend", lambda mid: mid)
    # The largest installed model is the gated parakeet v3; v2 (CC-BY) is next.
    assert ab._installed_reference_fallbacks([]) == ["sherpa-parakeet-tdt-v2"]
    _accept("csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8")
    assert ab._installed_reference_fallbacks([]) == ["sherpa-parakeet-tdt-v3"]


# ── Faster-Whisper selection ────────────────────────────────────────────────

def test_faster_whisper_selection_asks_for_unaccepted_terms(store, monkeypatch):
    from core import prefs

    saved: dict = {}
    monkeypatch.setattr(prefs, "is_env_shadowed", lambda key: False)
    monkeypatch.setattr(prefs, "set_", lambda key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(ab, "_ISOLATED_INSTANCES", {})
    monkeypatch.setenv("ASR_MODEL_FASTER", "")
    with pytest.raises(ma.ModelLicenceNotAccepted):
        ab.select_faster_whisper_model("someone/unreviewed-ct2")  # no record → gated
    assert saved == {}
    ab.select_faster_whisper_model("Systran/faster-whisper-small")
    assert saved == {"asr_model_faster": "Systran/faster-whisper-small"}


# ── Diarisation ─────────────────────────────────────────────────────────────

def test_diarisation_gates_only_the_selected_model(store, monkeypatch):
    from services import diarization_runtime as dr

    monkeypatch.delenv("OMNIVOICE_DIARIZATION_MODEL", raising=False)
    monkeypatch.setattr(dr, "selected_backend", lambda: dr.SORTFORMER)
    with pytest.raises(ma.ModelLicenceNotAccepted):
        dr.ensure_selected_accepted()
    monkeypatch.setattr(dr, "selected_backend", lambda: dr.PYANNOTE)
    dr.ensure_selected_accepted()  # MIT pyannote: never blocked by Sortformer
    monkeypatch.setattr(dr, "selected_backend", lambda: dr.SORTFORMER)
    _accept(SORTFORMER)
    dr.ensure_selected_accepted()


def test_native_sortformer_refuses_before_touching_the_model(store, monkeypatch):
    import types

    from services import diarization_native
    from services import diarization_runtime as dr

    # The audio.cpp bootstrap pulls in the TTS stack; only its name is needed.
    monkeypatch.setitem(
        sys.modules, "engines.audiocpp.bootstrap",
        types.SimpleNamespace(resolve_server_binary=lambda: None),
    )
    monkeypatch.delenv("OMNIVOICE_DIARIZATION_MODEL", raising=False)
    touched: list[bool] = []

    def model_path():
        touched.append(True)
        raise FileNotFoundError

    monkeypatch.setattr(dr, "sortformer_model_path", model_path)
    with pytest.raises(ma.ModelLicenceNotAccepted):
        diarization_native.NativeSortformer()
    assert touched == []
    _accept(SORTFORMER)
    with pytest.raises(FileNotFoundError):
        diarization_native.NativeSortformer()
    assert touched == [True]


# ── NLLB translation and router propagation ─────────────────────────────────
# NLLB, capture_ws, openai_compat and dub_core are exercised behaviourally in
# test_model_licence_entrypoint_gates.py; the remaining routers are only
# checked structurally here (they import torch/soundfile at module level).

def _source(rel: str) -> str:
    return (ROOT / "backend" / rel).read_text(encoding="utf-8")


def test_nllb_is_gated_in_the_registry(store):
    assert ma.status("facebook/nllb-200-distilled-600M")["required"] is True


@pytest.mark.parametrize("rel", [
    "api/routers/dub_export.py",
    "api/routers/capture.py",
    "api/routers/batch.py",
])
def test_routers_surface_the_typed_licence_error(rel):
    tree = ast.parse(_source(rel))
    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "ModelLicenceNotAccepted" in names, f"{rel} swallows the licence error"


@pytest.mark.parametrize("name", [
    "/models/whisper", "./whisper", "../whisper", "~/whisper", r"C:\models\whisper", r"models\whisper", "D:/models/w",
])
def test_local_model_paths_are_recognised_by_syntax_not_the_filesystem(name):
    assert ab._licence_repo(name) is None


def test_org_name_is_a_registry_identity():
    assert ab._licence_repo("someone/unreviewed-ct2") == "someone/unreviewed-ct2"
