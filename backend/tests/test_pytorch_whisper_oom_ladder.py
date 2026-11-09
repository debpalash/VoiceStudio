"""PyTorch Whisper: CUDA OOM steps down the batch size, then finishes on CPU."""
from __future__ import annotations

import numpy as np
import soundfile as sf
import pytest

from services import asr_backend as ab


class _Model:
    def __init__(self):
        self.moved = None

    def to(self, **kw):
        self.moved = kw
        return self


class FakePipe:
    """Callable pipeline whose behaviour depends on batch size and device."""

    def __init__(self, device="cuda:0", fail_above=None, error=None):
        self.device = device
        self.fail_above = fail_above  # OOM when batch_size > this (on cuda)
        self.error = error
        self.model = _Model()
        self.processor = object()
        self.calls = []

    def __call__(self, inputs, **kw):
        self.calls.append((str(self.device), kw["batch_size"], kw))
        if self.error is not None:
            raise self.error
        if str(self.device).startswith("cuda") and (
            self.fail_above is not None and kw["batch_size"] > self.fail_above
        ):
            raise RuntimeError("CUDA out of memory. Tried to allocate 2 GiB")
        return {"text": "hello", "chunks": []}


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(sf, "read",
                        lambda p, dtype="float32": (np.zeros(1600, dtype=np.float32), 16000))

    def make(pipe):
        be = ab.PyTorchWhisperBackend(asr_pipe=pipe)
        return be

    return make


def _batches(pipe):
    return [b for _d, b, _k in pipe.calls]


def test_oom_steps_down_and_still_returns_text(backend):
    pipe = FakePipe(fail_above=4)
    out = backend(pipe).transcribe("x.wav", word_timestamps=False)
    assert out["text"] == "hello"
    assert _batches(pipe) == [16, 4]


def test_word_timestamp_ladder_is_smaller(backend):
    pipe = FakePipe(fail_above=2)
    backend(pipe).transcribe("x.wav", word_timestamps=True)
    assert _batches(pipe) == [8, 2]
    assert pipe.calls[0][2]["return_timestamps"] == "word"
    assert pipe.calls[0][2]["chunk_length_s"] == 15


def test_exhausted_ladder_falls_back_to_cpu(backend):
    pipe = FakePipe(fail_above=0)
    be = backend(pipe)
    out = be.transcribe("x.wav", word_timestamps=False)
    assert out["text"] == "hello"
    assert _batches(pipe) == [16, 4, 1, 2]
    assert pipe.calls[-1][0] == "cpu"
    assert pipe.model.moved is not None


def test_cpu_fallback_keeps_injected_model_and_processor(backend):
    pipe = FakePipe(fail_above=0)
    model, processor = pipe.model, pipe.processor
    be = backend(pipe)
    be.transcribe("x.wav")
    assert be._pipe is pipe
    assert pipe.model is model and pipe.processor is processor


def test_non_oom_errors_are_not_retried(backend):
    pipe = FakePipe(error=ValueError("bad audio"))
    with pytest.raises(ValueError, match="bad audio"):
        backend(pipe).transcribe("x.wav")
    assert _batches(pipe) == [8]


def test_cpu_pipeline_uses_small_batch(backend):
    pipe = FakePipe(device="cpu")
    backend(pipe).transcribe("x.wav", word_timestamps=False)
    assert _batches(pipe) == [2]


def test_oom_classifier():
    assert ab.PyTorchWhisperBackend._is_oom(RuntimeError("CUDA Out Of Memory"))
    assert not ab.PyTorchWhisperBackend._is_oom(RuntimeError("nope"))


@pytest.mark.parametrize(
    "oom",
    [
        RuntimeError("level_zero backend failed with error: 39 (UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY)"),
        RuntimeError("XPU out of memory. Tried to allocate 1.50 GiB"),
    ],
)
def test_intel_xpu_pipeline_gets_the_same_ladder_and_release(backend, monkeypatch, oom):
    """get_best_device() picks XPU on Intel Arc. Its OOMs must step the batch
    down and release the failed batch exactly like CUDA, not fail the chunk."""
    import services.model_manager as mm

    class _XpuPipe(FakePipe):
        def __call__(self, inputs, **kw):
            self.calls.append((str(self.device), kw["batch_size"], kw))
            if kw["batch_size"] > 2:
                raise oom
            return {"text": "ok", "chunks": []}

    released = []
    monkeypatch.setattr(mm, "release_after_oom", released.append)
    pipe = _XpuPipe(device="xpu:0")
    out = backend(pipe).transcribe("x.wav", word_timestamps=True)
    assert out["text"] == "ok"
    assert _batches(pipe) == [8, 2]
    assert released == [oom]


@pytest.mark.parametrize("device", ["cuda:0", "xpu:0"])
def test_cpu_fallback_releases_the_gpu_it_left(monkeypatch, device):
    import services.model_manager as mm

    released = []
    monkeypatch.setattr(mm, "release_device_cache", lambda **kw: released.append(kw))
    ab.PyTorchWhisperBackend(asr_pipe=FakePipe(device=device))._rebuild_on_cpu()
    assert released == [{"device": device}]
