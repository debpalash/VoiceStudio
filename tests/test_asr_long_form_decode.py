"""Long-form Whisper decoding must not condition on the previous window.

faster-whisper and MLX Whisper feed each 30 s window the previous window's text
by default, so one hallucinated window repeats through the rest of a long file.
Every sequential Whisper decode path (in-process faster-whisper, the isolated
faster-whisper sidecar, MLX Whisper) must turn that off. Per-request options
still pass through.
"""
import importlib.util
import os
import sys
import types
from types import SimpleNamespace

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


class _FakeModel:
    def __init__(self):
        self.kwargs = None

    def transcribe(self, _audio, **kwargs):
        self.kwargs = kwargs
        seg = SimpleNamespace(start=0.0, end=1.0, text="hello", words=None)
        return iter([seg]), SimpleNamespace(language="en", language_probability=1.0, duration=1.0)


def test_faster_whisper_decodes_windows_independently(monkeypatch):
    from services import asr_backend as ab

    backend = ab.FasterWhisperBackend(model_name="small")
    fake = _FakeModel()
    backend._model = fake
    monkeypatch.setattr(backend, "_ensure_model", lambda: None)
    backend.transcribe("clip.wav", word_timestamps=False, language="de")
    assert fake.kwargs["condition_on_previous_text"] is False
    assert fake.kwargs["language"] == "de"


def test_mlx_whisper_decodes_windows_independently(monkeypatch):
    from services import asr_backend as ab

    captured = {}
    fake_mlx = types.ModuleType("mlx_whisper")

    def _transcribe(audio, **kwargs):
        captured.update(kwargs)
        return {"text": "hallo", "segments": [], "language": "de"}

    fake_mlx.transcribe = _transcribe
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx)
    monkeypatch.setattr(ab, "_decode_audio_16k_mono", lambda _path: [0.0, 0.0, 0.0])
    monkeypatch.setattr(ab, "_local_model_source", lambda name: name)

    ab.MLXWhisperBackend().transcribe("clip.wav", word_timestamps=False)
    assert captured["condition_on_previous_text"] is False


def test_isolated_sidecar_decodes_windows_independently(monkeypatch):
    path = os.path.join(
        os.path.dirname(__file__), "..", "backend", "engines", "_asr_sidecar", "main.py"
    )
    spec = importlib.util.spec_from_file_location("_asr_sidecar_main_t", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fake = _FakeModel()
    monkeypatch.setattr(mod, "_get_model", lambda: fake)
    out = mod._transcribe("clip.wav", False, {"beam_size": 2}, {"language": "fr"})
    assert out["text"] == "hello"
    assert fake.kwargs["condition_on_previous_text"] is False
    assert fake.kwargs["beam_size"] == 2 and fake.kwargs["language"] == "fr"
