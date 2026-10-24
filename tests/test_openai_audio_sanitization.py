"""Exercise maintained route helpers without importing model engines."""
import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
import torch


def _helper(path, name):
    source = Path(__file__).resolve().parents[1] / path
    node = next(n for n in ast.parse(source.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("own_mastering", [False, True])
def test_openai_sanitizes_before_mastering_and_peak_normalization(monkeypatch, invalid, own_mastering):
    from services import audio_dsp

    original = torch.tensor([[.5, .25, invalid]])
    seen = []
    def mastering(audio, sample_rate):
        assert bool(torch.isfinite(audio).all())
        seen.append(audio.shape)
        return audio
    monkeypatch.setattr(audio_dsp, "apply_mastering", mastering)
    watermark = ModuleType("services.watermark")
    watermark.mark_synthetic = lambda audio, sr, **kw: audio
    monkeypatch.setitem(sys.modules, "services.watermark", watermark)
    backend = SimpleNamespace(generate=lambda *a, **kw: original,
                              sample_rate=24000, applies_own_mastering=own_mastering)
    run = _helper("backend/api/routers/openai_compat.py", "_run_tts")
    result, sr = run(backend, "hello", {})
    assert result.shape == original.shape
    assert bool(torch.isfinite(result).all())
    assert result[0, -1] == 0
    assert result.abs().max().item() == pytest.approx(10 ** (-2 / 20))
    assert not bool(torch.isfinite(original).all())
    assert seen == ([] if own_mastering else [original.shape])
    assert sr == 24000


def test_shared_sanitizer_and_generation_wrapper_preserve_validation_contract(monkeypatch):
    from services.audio_dsp import sanitize_audio

    wrapper = _helper("backend/api/routers/generation.py", "_sanitize_audio")
    finite = torch.tensor([.5])
    assert sanitize_audio(finite) is finite
    assert wrapper(finite) is finite
    assert sanitize_audio("non-tensor") == "non-tensor"
    def fail(*args):
        raise ValueError("owned validation failure")
    monkeypatch.setattr(torch, "isfinite", fail)
    for helper in (sanitize_audio, wrapper):
        with pytest.raises(RuntimeError, match="Generated audio could not be validated"):
            helper(finite)
