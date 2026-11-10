"""Exercise the CUDA adapter contract with an in-memory streaming runtime."""
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
import torch


def test_qwen_cuda_caches_reference_and_owns_streamed_audio(tmp_path):
    from services.qwen_cuda import QwenCUDABackend

    reference = tmp_path / "reference.wav"
    sf.write(reference, np.zeros((480, 2), dtype=np.float32), 24000)
    prompts = []
    calls = []

    def create_prompt(*, ref_audio, ref_text):
        samples, rate = ref_audio
        assert samples.shape == (480,)
        assert rate == 24000
        prompts.append(ref_text)
        return ref_text

    def generate_stream(**kw):
        calls.append(kw)
        # The runtime can reuse the same NumPy buffer for consecutive chunks.
        buffer = np.array([0.1, 0.2], dtype=np.float32)
        yield buffer, 24000, {}
        buffer[:] = [0.3, 0.4]
        yield buffer, 24000, {}

    engine = QwenCUDABackend()
    engine._model = SimpleNamespace(
        model=SimpleNamespace(create_voice_clone_prompt=create_prompt),
        generate_voice_clone_streaming=generate_stream,
    )
    kwargs = dict(ref_audio=reference, ref_text="Reference", language="en")
    first = engine.generate("First.", **kwargs)
    chunks = list(engine.stream_audio("Second.", **kwargs))

    torch.testing.assert_close(first, torch.tensor([[0.1, 0.2, 0.3, 0.4]]))
    torch.testing.assert_close(chunks[0], torch.tensor([[0.1, 0.2]]))
    assert prompts == ["Reference"]
    assert [call["text"] for call in calls] == ["First.", "Second."]
    assert all("max_new_tokens" not in call for call in calls)
    assert all(call["voice_clone_prompt"] == "Reference" and call["language"] == "English"
               for call in calls)


def test_qwen_cuda_reports_missing_or_unreadable_reference(tmp_path):
    from services.qwen_cuda import QwenCUDABackend
    from services.tts_backend import TTSInputError

    engine = QwenCUDABackend()
    with pytest.raises(TTSInputError, match="reference and transcript"):
        next(engine.stream_audio("Hello."))

    reference = tmp_path / "reference.wav"
    with pytest.raises(TTSInputError, match="missing or unreadable"):
        next(engine.stream_audio("Hello.", ref_audio=reference, ref_text="Reference"))

    reference.write_bytes(b"invalid audio")
    engine._model = SimpleNamespace()
    with pytest.raises(TTSInputError, match="could not be decoded"):
        next(engine.stream_audio("Hello.", ref_audio=reference, ref_text="Reference"))


def test_qwen_cuda_ready_does_not_wait_for_generation_lock():
    from services.qwen_cuda import QwenCUDABackend

    engine = QwenCUDABackend()
    engine._model = SimpleNamespace()

    class HeldByStream:
        def __enter__(self):
            pytest.fail("Warm readiness must not enter the generation lock")

    engine._lock = HeldByStream()
    engine.ensure_ready()


def test_qwen_cuda_buffered_generation_retries_while_native_stream_holds_engine(tmp_path):
    from services.qwen_cuda import QwenCUDABackend

    from services.model_manager import GpuPoolBusyError

    reference = tmp_path / "reference.wav"
    sf.write(reference, np.zeros(480, dtype=np.float32), 24000)

    def generate_stream(**kw):
        yield np.ones(2, dtype=np.float32), 24000, {}

    engine = QwenCUDABackend()
    engine._model = SimpleNamespace(
        model=SimpleNamespace(create_voice_clone_prompt=lambda **kwargs: None),
        generate_voice_clone_streaming=generate_stream,
    )
    kwargs = dict(ref_audio=reference, ref_text="Reference")
    stream = engine.stream_audio("First.", **kwargs)
    try:
        next(stream)
        with pytest.raises(GpuPoolBusyError) as busy:
            engine.generate("Competing buffered request.", **kwargs)
        assert busy.value.retry_after == 1
    finally:
        stream.close()
    torch.testing.assert_close(engine.generate("After disconnect.", **kwargs), torch.ones(1, 2))


def _loaded_engine():
    from services.qwen_cuda import QwenCUDABackend

    def generate_stream(**kw):
        for value in (1.0, 2.0, 3.0):
            yield np.full(2, value, dtype=np.float32), 24000, {}

    engine = QwenCUDABackend()
    engine._model = SimpleNamespace(
        model=SimpleNamespace(create_voice_clone_prompt=lambda **kwargs: None),
        generate_voice_clone_streaming=generate_stream,
    )
    return engine


def test_qwen_cuda_second_native_stream_is_busy_without_blocking(tmp_path):
    from services.model_manager import GpuPoolBusyError

    reference = tmp_path / "reference.wav"
    sf.write(reference, np.zeros(480, dtype=np.float32), 24000)
    engine = _loaded_engine()
    kwargs = dict(ref_audio=reference, ref_text="Reference")
    active = engine.stream_audio("First.", **kwargs)
    try:
        next(active)
        # A blocking acquire would hang here; the busy error must be immediate.
        with pytest.raises(GpuPoolBusyError) as busy:
            next(engine.stream_audio("Second.", **kwargs))
        assert busy.value.retry_after == 1
    finally:
        active.close()


def test_qwen_cuda_releases_the_lock_when_the_client_cancels_mid_stream(tmp_path):
    reference = tmp_path / "reference.wav"
    sf.write(reference, np.zeros(480, dtype=np.float32), 24000)
    engine = _loaded_engine()
    kwargs = dict(ref_audio=reference, ref_text="Reference")
    stream = engine.stream_audio("Cancelled.", **kwargs)
    next(stream)
    assert engine._lock.locked()
    stream.close()  # What a disconnect does to the abandoned generator.
    assert not engine._lock.locked()
    assert len(list(engine.stream_audio("Next.", **kwargs))) == 3
