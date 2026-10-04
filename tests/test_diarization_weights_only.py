"""Diarization must register torch safe-globals before loading (issue #270).

PyTorch 2.6+ defaults `torch.load` to `weights_only=True`, whose secure
unpickler rejects the pyannote checkpoint's metadata globals
(`torch_version.TorchVersion`, omegaconf nodes, …). `get_diarization_pipeline`
must register the same allowlist the WhisperX VAD load uses, before calling
`Pipeline.from_pretrained`, or diarization breaks even with the license
accepted.
"""
import sys
import types
from unittest.mock import Mock

import pytest


@pytest.fixture
def reset_diar(monkeypatch):
    import services.model_manager as mm
    monkeypatch.setattr(mm, "_diar_pipeline", None, raising=False)
    yield mm
    monkeypatch.setattr(mm, "_diar_pipeline", None, raising=False)


@pytest.mark.parametrize("token", ["hf_test", None])
def test_loads_pyannote_after_registering_safe_globals(reset_diar, monkeypatch, token):
    mm = reset_diar
    order = []

    monkeypatch.setattr(
        "services.pyannote_audio_compat.ensure_pyannote_audio_compat",
        lambda: order.append("audio"),
    )

    monkeypatch.setattr(
        "services.token_resolver.resolve",
        lambda: types.SimpleNamespace(token=token, source="app", user="u") if token else None,
    )

    # Spy on the shared allowlister; must run BEFORE from_pretrained.
    from services import asr_backend as ab
    monkeypatch.setattr(
        ab.WhisperXBackend, "_allow_vad_pickle_globals",
        staticmethod(lambda: order.append("allow")),
    )

    fake_pipe = object()

    def _from_pretrained(*args, **kwargs):
        assert kwargs["use_auth_token"] == (token or False)
        order.append("load")
        return fake_pipe

    fake_mod = types.ModuleType("pyannote.audio")
    fake_mod.Pipeline = types.SimpleNamespace(from_pretrained=_from_pretrained)
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake_mod)

    from contextlib import contextmanager
    @contextmanager
    def local_config():
        yield "local-config.yaml"
    monkeypatch.setattr("services.diarization_local.local_pipeline_config", local_config)

    # CPU device → no .to() call on the fake pipe.
    monkeypatch.setattr(mm, "get_best_device", lambda: "cpu")

    result = mm.get_diarization_pipeline()

    assert result is fake_pipe
    assert order == ["audio", "allow", "load"], f"compatibility must precede load, got {order}"


def test_no_token_short_circuits_without_loading(reset_diar, monkeypatch):
    mm = reset_diar
    monkeypatch.setattr("services.token_resolver.resolve", lambda: None)
    pipe, err = mm.get_diarization_pipeline(return_error=True)
    assert pipe is None
    assert err == mm.DIARIZATION_ERR_NO_TOKEN


@pytest.mark.parametrize("token", ["hf_test", None])
def test_missing_bundle_is_checked_before_loading_the_runtime(reset_diar, monkeypatch, token):
    manager = reset_diar
    monkeypatch.setattr(
        "services.token_resolver.resolve",
        lambda: types.SimpleNamespace(token=token) if token else None,
    )

    def missing_bundle():
        raise FileNotFoundError("Diarization model is not installed")

    monkeypatch.setattr("services.diarization_local.local_pipeline_config", missing_bundle)
    runtime_loader = Mock(side_effect=RuntimeError("Runtime must not initialize"))
    monkeypatch.setattr(manager, "_lazy_torch", runtime_loader)
    audio_compat = Mock(side_effect=RuntimeError("Audio compatibility must not initialize"))
    monkeypatch.setattr("services.pyannote_audio_compat.ensure_pyannote_audio_compat", audio_compat)

    pipeline, error = manager.get_diarization_pipeline(return_error=True)

    assert pipeline is None
    assert error == (manager.DIARIZATION_ERR_MISSING if token else manager.DIARIZATION_ERR_NO_TOKEN)
    runtime_loader.assert_not_called()
    audio_compat.assert_not_called()
