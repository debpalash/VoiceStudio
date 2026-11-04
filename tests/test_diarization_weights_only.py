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

import pytest


@pytest.fixture
def reset_diar(monkeypatch):
    import services.model_manager as mm
    monkeypatch.setattr(mm, "_diar_pipeline", None, raising=False)
    monkeypatch.setattr(
        "core.device_caps.detect_host_caps", lambda: types.SimpleNamespace(family="cpu"),
    )
    monkeypatch.setattr("services.diarization_runtime.selected_backend", lambda: "pyannote")
    yield mm
    monkeypatch.setattr(mm, "_diar_pipeline", None, raising=False)


def test_loads_pyannote_after_registering_safe_globals(reset_diar, monkeypatch):
    mm = reset_diar
    order = []

    # Token present (App source).
    monkeypatch.setattr(
        "services.token_resolver.resolve",
        lambda: types.SimpleNamespace(token="hf_test", source="app", user="u"),
    )

    # Spy on the shared allowlister; must run BEFORE from_pretrained.
    from services import asr_backend as ab
    monkeypatch.setattr(
        ab.WhisperXBackend, "_allow_vad_pickle_globals",
        staticmethod(lambda: order.append("allow")),
    )

    fake_pipe = object()

    def _from_pretrained(*a, **k):
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
    assert order == ["allow", "load"], f"allowlist must precede load, got {order}"


def test_no_token_short_circuits_without_loading(reset_diar, monkeypatch):
    mm = reset_diar
    monkeypatch.setattr("services.token_resolver.resolve", lambda: None)
    pipe, err = mm.get_diarization_pipeline(return_error=True)
    assert pipe is None
    assert err == mm.DIARIZATION_ERR_NO_TOKEN
