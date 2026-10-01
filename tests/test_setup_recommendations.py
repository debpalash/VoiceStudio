"""Tests for GET /setup/recommendations — the per-platform curated preset.

The preset is data-driven from ``curated_on`` in ``backend/config/models.yaml``
(TTS-only-required change): only the TTS model is required, and each host
family gets its own curated ASR/TTS picks. These tests pin the resolution
logic per family by mocking ``_current_platform_tags`` — no hardware or
network needed.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from main import app
    return TestClient(app)


def _recommend(client, tags: list[str]) -> dict:
    from api.routers.setup import models as setup_models
    with patch.object(setup_models, "_current_platform_tags", return_value=tags):
        r = client.get("/setup/recommendations")
    assert r.status_code == 200
    return r.json()


def _ids(payload: dict) -> set[str]:
    return {m["repo_id"] for m in payload["models"]}


def test_required_models_is_tts_only():
    from api.routers.setup.models import REQUIRED_MODELS
    assert [rid for rid, _ in REQUIRED_MODELS] == ["k2-fsa/OmniVoice"]


def test_platform_tags_follow_selected_worker_not_control_plane(monkeypatch):
    from api.routers.setup import models as setup_models

    monkeypatch.setattr(setup_models, "_target_host", lambda: {
        "os": "linux", "arch": "x86_64", "gpus": [{"backend": "cuda"}],
    })
    assert setup_models._current_platform_tags() == ["linux", "linux-x86_64", "cuda"]


def test_recommendations_use_selected_worker_install_state(monkeypatch):
    from api.routers.setup import models as setup_models

    monkeypatch.setattr(
        setup_models,
        "_target_repo_inventory",
        lambda: ("gpu2", {"k2-fsa/OmniVoice"}),
    )
    monkeypatch.setattr(
        setup_models,
        "_current_platform_tags",
        lambda: ["linux", "linux-x86_64", "cuda"],
    )

    payload = setup_models.recommendations()

    assert payload["target"] == "gpu2"
    required = next(row for row in payload["models"] if row["repo_id"] == "k2-fsa/OmniVoice")
    assert required["installed"] is True


def test_target_repo_inventory_uses_connected_worker_contract(monkeypatch):
    from api.routers.setup import models as setup_models

    live = SimpleNamespace(
        worker_id="gpu2",
        record=SimpleNamespace(
            capabilities=[{"downloaded": True, "repo_ids": ["org/model"]}],
        ),
    )
    monkeypatch.setattr(setup_models, "_target_worker", lambda: live)

    assert setup_models._target_repo_inventory() == ("gpu2", {"org/model"})


def test_mac_arm_curates_mlx_whisper_not_ct2(client):
    ids = _ids(_recommend(client, ["darwin", "darwin-arm64"]))
    assert "k2-fsa/OmniVoice" in ids
    assert "mlx-community/whisper-large-v3-mlx" in ids
    assert "mlx-community/whisper-large-v3-turbo" in ids
    # The CT2 build stays available in the full catalog but is not the
    # Apple Silicon curated pick — MLX is Metal-accelerated, CT2 is CPU-only there.
    assert "Systran/faster-whisper-large-v3" not in ids
    assert "csukuangfj/sherpa-onnx-whisper-tiny" in ids
    assert "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8" not in ids


def test_cuda_curates_ct2_whisper_and_turbo(client):
    ids = _ids(_recommend(client, ["linux", "linux-x86_64", "cuda"]))
    assert "k2-fsa/OmniVoice" in ids
    assert "Systran/faster-whisper-large-v3" in ids
    assert "deepdml/faster-whisper-large-v3-turbo-ct2" in ids
    assert "csukuangfj/sherpa-onnx-whisper-tiny" in ids
    assert "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8" not in ids
    # MLX models never resolve off Apple Silicon.
    assert not any(rid.startswith("mlx-community/") for rid in ids)


def test_rocm_curates_pytorch_whisper_gpu_path(client):
    # ROCm hosts report both cuda (torch compat) and rocm tags.
    ids = _ids(_recommend(client, ["linux", "linux-x86_64", "cuda", "rocm"]))
    assert "openai/whisper-large-v3" in ids, (
        "PyTorch whisper is the ROCm GPU route (CTranslate2 has no ROCm backend)"
    )
    assert "Systran/faster-whisper-large-v3" in ids  # curated_on lists rocm explicitly
    # Curation ignores the compat 'cuda' tag on ROCm hosts: curated_on:[cuda]
    # means NVIDIA-tuned — entries that want the AMD preset list 'rocm'
    # explicitly. The CT2 turbo build is cuda/cpu-curated only.
    assert "deepdml/faster-whisper-large-v3-turbo-ct2" not in ids


def test_cpu_only_curates_ct2_and_whisper_tiny(client):
    payload = _recommend(client, ["win32", "win32-AMD64", "cpu"])
    ids = _ids(payload)
    assert "Systran/faster-whisper-large-v3" in ids
    assert "deepdml/faster-whisper-large-v3-turbo-ct2" in ids
    assert "csukuangfj/sherpa-onnx-whisper-tiny" in ids
    assert "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8" not in ids
    assert "openai/whisper-large-v3" not in ids  # 3.1 GB PyTorch build: GPU hosts only


def test_only_tts_entries_are_marked_required(client):
    payload = _recommend(client, ["linux", "linux-x86_64", "cpu"])
    required = [m for m in payload["models"] if m["required"]]
    assert [m["repo_id"] for m in required] == ["k2-fsa/OmniVoice"]
    # ASR picks are present but optional — the wizard must not gate on them.
    assert any(m["role"] == "ASR" and not m["required"] for m in payload["models"])


def test_models_endpoint_exposes_curated_flag(client):
    r = client.get("/models")
    assert r.status_code == 200
    models = r.json()["models"]
    assert all("curated" in m for m in models)
    # The required TTS model is always curated.
    tts = next(m for m in models if m["repo_id"] == "k2-fsa/OmniVoice")
    assert tts["curated"] is True


def test_cpu_preset_lists_the_light_whisper_before_the_heavy_ones(client):
    models = _recommend(client, ["linux", "linux-x86_64", "cpu"])["models"]
    asr = [m["repo_id"] for m in models if m["role"] == "ASR"]
    small = asr.index("Systran/faster-whisper-small")
    for heavy in ("Systran/faster-whisper-large-v3", "deepdml/faster-whisper-large-v3-turbo-ct2"):
        assert small < asr.index(heavy), asr
    # The required TTS entry still leads the whole preset.
    assert models[0]["repo_id"] == "k2-fsa/OmniVoice"
