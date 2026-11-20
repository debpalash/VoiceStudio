"""The cloned passage of a long reference starts and ends in a pause."""
import torch

from omnivoice.utils.audio import trim_passage_to_pauses

SR = 24_000


def _speech(seconds: float) -> torch.Tensor:
    t = torch.arange(int(seconds * SR)) / SR
    return (0.5 * torch.sin(2 * torch.pi * 220 * t)).unsqueeze(0)


def _silence(wav: torch.Tensor, start_s: float, end_s: float) -> torch.Tensor:
    wav[:, int(start_s * SR) : int(end_s * SR)] = 0.0
    return wav


def test_mid_word_tail_is_cut_back_to_the_last_pause():
    wav = _silence(_speech(15.0), 14.0, 14.3)  # pause, then a word fragment
    out = trim_passage_to_pauses(wav, SR)
    assert 14.0 <= out.size(-1) / SR <= 14.3


def test_mid_word_head_is_cut_forward_to_the_first_pause():
    wav = _silence(_speech(15.0), 0.6, 0.9)
    out = trim_passage_to_pauses(wav, SR)
    assert 14.1 <= out.size(-1) / SR <= 14.4
    assert torch.equal(out, wav[:, -out.size(-1) :])


def test_passage_without_pauses_is_untouched():
    wav = _speech(15.0)
    assert trim_passage_to_pauses(wav, SR) is wav


def test_quieter_speech_is_not_mistaken_for_a_pause():
    wav = _speech(15.0)
    wav[:, int(14.0 * SR) :] *= 0.5  # softer, still speech
    assert trim_passage_to_pauses(wav, SR) is wav


def test_clip_too_short_to_search_is_untouched():
    wav = _speech(2.0)
    assert trim_passage_to_pauses(wav, SR) is wav


def test_stereo_trims_only_where_every_channel_is_quiet():
    both = torch.cat([_silence(_speech(15.0), 14.0, 14.3)] * 2)
    assert 14.0 <= trim_passage_to_pauses(both, SR).size(-1) / SR <= 14.3

    one_loud = both.clone()
    one_loud[1, int(14.0 * SR) : int(14.3 * SR)] = 0.5  # second channel keeps talking
    assert trim_passage_to_pauses(one_loud, SR) is one_loud


def test_mostly_silent_passage_is_untouched():
    wav = torch.zeros((1, int(15.0 * SR)))
    wav[:, : int(1.0 * SR)] = _speech(1.0)  # median frame energy is 0
    assert trim_passage_to_pauses(wav, SR) is wav


def test_every_long_reference_path_gets_the_pause_aligned_window(monkeypatch, tmp_path):
    """The OmniVoice sidecar and the inline retry take their window from
    ``omnivoice_inline_reference``. It used to hand over the raw 15 s cut, which
    ended in the first sound of the next word, and the clone started each
    output with that sound. Ranking and recall must both give the trimmed cut."""
    import os
    import soundfile as sf
    from services import asr_backend, tts_backend

    # Window 0 (0-15 s): a pause at 14.0-14.3 s, then a word cut at 15 s.
    path = tmp_path / "long.wav"
    sf.write(str(path), _silence(_speech(25.0), 14.0, 14.3).squeeze(0).numpy(), SR)
    reads = []

    def transcribe(p, **_k):
        reads.append(p)
        return "many spoken words here" if sf.info(p).duration > 12 else "few"

    monkeypatch.setattr(asr_backend, "transcribe_reference", transcribe)
    monkeypatch.setattr(tts_backend, "_reference_asr_identity", lambda: "fixed-recognizer")
    tts_backend.clear_clone_prompt_cache()
    try:
        for _ in ("ranked", "recalled"):
            audio, text, owned = tts_backend.omnivoice_inline_reference(str(path), None)
            try:
                assert owned == audio and text == "many spoken words here"
                assert sf.info(audio).duration <= 14.3
            finally:
                os.remove(owned)
        assert len(reads) == 2  # the recalled window is not read again
    finally:
        tts_backend.clear_clone_prompt_cache()


def test_trim_never_lengthens_and_keeps_order():
    wav = _silence(_silence(_speech(15.0), 0.5, 0.8), 14.2, 14.5)
    out = trim_passage_to_pauses(wav, SR)
    assert out.size(-1) < wav.size(-1)
    start = next(i for i in range(0, wav.size(-1) - out.size(-1) + 1, 600)
                 if torch.equal(wav[:, i : i + out.size(-1)], out))
    assert 0.5 * SR <= start <= 0.8 * SR
