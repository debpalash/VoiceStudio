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


# ── Perf: CPU threads + CUDA batched decode with sequential fallback ─────────

def _cuda_backend(monkeypatch, pipe):
    from services import asr_backend as ab

    backend = ab.FasterWhisperBackend(model_name="small")
    seq = _FakeModel()
    backend._model = seq
    backend._device = "cuda"
    backend._batched_pipe = pipe
    backend._batched_for = seq
    monkeypatch.setattr(backend, "_ensure_model", lambda: None)
    return backend, seq


def test_cuda_uses_batched_pipeline(monkeypatch):
    pipe = _FakeModel()
    backend, seq = _cuda_backend(monkeypatch, pipe)
    backend.transcribe("clip.wav", word_timestamps=True)
    assert pipe.kwargs["batch_size"] >= 8
    assert pipe.kwargs["vad_filter"] is True and pipe.kwargs["word_timestamps"] is True
    assert seq.kwargs is None


def test_batched_failure_falls_back_to_sequential(monkeypatch):
    class _Boom(_FakeModel):
        def transcribe(self, *_a, **_k):
            raise RuntimeError("batched exploded")

    backend, seq = _cuda_backend(monkeypatch, _Boom())
    out = backend.transcribe("clip.wav", word_timestamps=False)
    assert out["segments"][0]["text"] == "hello"
    assert seq.kwargs is not None and "batch_size" not in seq.kwargs
    assert backend._batched is False  # not retried on every chunk


def test_batched_kill_switch_and_non_cuda(monkeypatch):
    pipe = _FakeModel()
    backend, seq = _cuda_backend(monkeypatch, pipe)
    monkeypatch.setenv("OMNIVOICE_ASR_BATCHED", "0")
    backend.transcribe("clip.wav", word_timestamps=False)
    assert pipe.kwargs is None and seq.kwargs is not None
    monkeypatch.delenv("OMNIVOICE_ASR_BATCHED")
    backend2, seq2 = _cuda_backend(monkeypatch, pipe)
    backend2._device = "cpu"
    backend2.transcribe("clip.wav", word_timestamps=False)
    assert pipe.kwargs is None and seq2.kwargs is not None


def test_cpu_threads_capped():
    from services import asr_backend as ab

    kw = ab._whisper_thread_kwargs("cpu")
    assert 1 <= kw["cpu_threads"] <= 8
    assert ab._whisper_thread_kwargs("cuda") == {}
