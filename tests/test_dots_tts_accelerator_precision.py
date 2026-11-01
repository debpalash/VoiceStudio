"""DOTS picks CUDA/CPU internally; another accelerator must not imply bf16."""
import io
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize('family', [None, 'cuda', 'xpu', 'npu', 'mps'])
def test_precision_matches_runtime_device(monkeypatch, family):
    from engines.dots_tts import main

    accelerator = Mock(return_value=SimpleNamespace(type=family) if family else None)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(
        accelerator=SimpleNamespace(current_accelerator=accelerator),
        cuda=SimpleNamespace(is_available=lambda: family == 'cuda'),
    ))
    loader = Mock()
    monkeypatch.setitem(sys.modules, 'dots_tts.runtime', SimpleNamespace(DotsTtsRuntime=loader))
    monkeypatch.setattr(main, '_runtime', None)
    monkeypatch.delenv('OMNIVOICE_DOTS_TTS_PRECISION', raising=False)
    main._load_runtime(io.BytesIO())
    assert loader.from_pretrained.call_args.kwargs['precision'] == ('bfloat16' if family == 'cuda' else 'float32')


@pytest.mark.parametrize("override", [None, "float16"])
def test_precision_probe_failure_uses_safe_default_or_explicit_override(monkeypatch, override):
    from engines.dots_tts import main

    probe = Mock(side_effect=RuntimeError("CUDA driver unavailable"))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=probe)))
    loader = Mock()
    monkeypatch.setitem(sys.modules, "dots_tts.runtime", SimpleNamespace(DotsTtsRuntime=loader))
    monkeypatch.setattr(main, "_runtime", None)
    if override is None:
        monkeypatch.delenv("OMNIVOICE_DOTS_TTS_PRECISION", raising=False)
    else:
        monkeypatch.setenv("OMNIVOICE_DOTS_TTS_PRECISION", override)
    main._load_runtime(io.BytesIO())
    assert loader.from_pretrained.call_args.kwargs["precision"] == (override or "float32")

@pytest.mark.parametrize("override", [None, "bfloat16", " BF16 "])
def test_rdna2_never_selects_bf16(monkeypatch, override):
    from engines.dots_tts import main

    fake = SimpleNamespace(
        version=SimpleNamespace(hip="7.15"),
        cuda=SimpleNamespace(is_available=lambda: True,
                             get_device_properties=lambda i: SimpleNamespace(gcnArchName="gfx1031")),
    )
    monkeypatch.setitem(sys.modules, "torch", fake)
    loader = Mock()
    monkeypatch.setitem(sys.modules, "dots_tts.runtime", SimpleNamespace(DotsTtsRuntime=loader))
    monkeypatch.setattr(main, "_runtime", None)
    if override:
        monkeypatch.setenv("OMNIVOICE_DOTS_TTS_PRECISION", override)
    else:
        monkeypatch.delenv("OMNIVOICE_DOTS_TTS_PRECISION", raising=False)
    main._load_runtime(io.BytesIO())
    assert loader.from_pretrained.call_args.kwargs["precision"] == "float32"
