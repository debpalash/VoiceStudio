from __future__ import annotations

import pytest


@pytest.fixture
def backend_module():
    from services import tts_backend

    return tts_backend


@pytest.fixture
def non_cloning_mlx(monkeypatch, backend_module):
    monkeypatch.setenv("OMNIVOICE_MLX_AUDIO_MODEL", "kokoro")
    monkeypatch.setattr(
        backend_module.MLXAudioBackend,
        "_ensure_loaded",
        lambda _self: pytest.fail("availability text must not load model weights"),
    )
    return backend_module.MLXAudioBackend()


def test_mlx_guidance_identifies_current_and_compatible_models(backend_module, non_cloning_mlx):
    explanation = backend_module.cloning_unavailable_detail(
        "mlx-audio", non_cloning_mlx, "character matching"
    )
    evidence = {
        "compatible_label": backend_module._MLX_AUDIO_MODEL_LABELS["csm"],
        "catalog": "Model Catalogue",
        "selected_repository": backend_module.MLXAudioBackend.CURATED_MODELS["kokoro"],
        "purpose": "character matching",
    }
    for value in evidence.values():
        assert value in explanation
    missing_engines = [
        engine for engine in backend_module.cloning_capable_engine_ids() if engine not in explanation
    ]
    assert missing_engines == []
    assert "doesn't support voice cloning" not in explanation


def test_engine_level_limitation_keeps_generic_advice(backend_module):
    kitten = backend_module.KittenTTSBackend.__new__(backend_module.KittenTTSBackend)
    explanation = backend_module.cloning_unavailable_detail("kittentts", kitten, "narration")
    assert "doesn't support voice cloning" in explanation
    assert backend_module._MLX_AUDIO_MODEL_LABELS["csm"] not in explanation


def test_declared_model_capabilities_match_curated_catalog(backend_module):
    curated = backend_module.MLXAudioBackend.CURATED_MODELS
    supported = set(backend_module.MLXAudioBackend.cloning_model_keys)
    assert supported
    assert supported <= curated.keys()
    assert supported <= backend_module._MLX_AUDIO_MODEL_LABELS.keys()

    observed = {}
    for alias, repository in curated.items():
        instance = backend_module.MLXAudioBackend.__new__(backend_module.MLXAudioBackend)
        instance._model_id = repository
        observed[alias] = instance.supports_cloning
    assert {alias for alias, enabled in observed.items() if enabled} == supported
    assert set(backend_module.MLXAudioBackend.cloning_model_labels()) == {
        backend_module._MLX_AUDIO_MODEL_LABELS[alias] for alias in supported
    }


def test_only_model_dependent_backends_declare_model_keys(backend_module):
    fixed_backends = [
        cls
        for cls in dict.values(backend_module._REGISTRY)
        if isinstance(getattr(cls, "supports_cloning", True), bool)
    ]
    assert all(not getattr(cls, "cloning_model_keys", ()) for cls in fixed_backends)
    assert "mlx-audio" not in backend_module.cloning_capable_engine_ids()
