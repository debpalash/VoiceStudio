"""Preview inference is finite before DSP and provenance marking."""
import asyncio
import pytest
import torch

@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf"), 0.0], ids=["nan", "posinf", "neginf", "finite"])
@pytest.mark.parametrize("own_mastering", [False, True])
def test_preview_sanitizes_before_dsp(monkeypatch, invalid, own_mastering):
    import api.routers.dub_generate as dg
    from services.audio_dsp import normalize_audio
    class Engine:
        sample_rate = 24000
        applies_own_mastering = own_mastering
        def generate(self, **kwargs):
            audio = torch.full((1, 24001), 0.25)
            audio[0, -1] = invalid
            return audio
    async def resolve(**kwargs):
        return Engine()
    async def run(fn, **kwargs):
        return fn()
    seen, mastering = [], []
    def master(audio, **kwargs):
        mastering.append(audio.clone())
        assert torch.isfinite(audio).all()
        return audio
    def mark(audio, sample_rate, **kwargs):
        seen.append(audio.clone())
        return audio
    def save(buf, audio, sample_rate, **kwargs):
        buf.write(b"controlled WAV seam")
    monkeypatch.setattr(dg, "_get_job", lambda _: {"speaker_clones": {}})
    monkeypatch.setattr(dg, "resolve_generation_backend", resolve)
    monkeypatch.setattr(dg, "run_on_gpu_pool_guarded", run)
    monkeypatch.setattr(dg, "apply_mastering", master)
    monkeypatch.setattr(dg, "normalize_audio", normalize_audio)
    monkeypatch.setattr(dg, "mark_synthetic", mark)
    monkeypatch.setattr(dg, "_safe_torchaudio_save", save)
    monkeypatch.setattr("services.text_normalization.normalize_for_tts", lambda text, lang: text)
    monkeypatch.setattr("services.model_manager.generate_timeout_s", lambda *a, **kw: 30)
    response = asyncio.run(dg.preview_segment("test", dg.SegmentPreviewRequest(text="Hello", language="Auto")))
    assert response.body == b"controlled WAV seam"
    assert len(mastering) == (0 if own_mastering else 1)
    assert len(seen) == 1
    assert seen[0].shape == (1, 24001)
    assert torch.isfinite(seen[0]).all()
    assert seen[0][0, -1].item() == 0.0
    assert seen[0][0, 12000].item() == pytest.approx(10 ** (-2 / 20))
