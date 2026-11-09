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


def _window_file(tmp_path, wav):
    import soundfile as sf

    path = tmp_path / "window.wav"
    sf.write(str(path), wav.squeeze(0).numpy(), SR)
    return str(path)


def test_backend_selected_window_is_trimmed_and_reread(monkeypatch, tmp_path):
    """A window the backend picks arrives WITH a transcript, so the model never
    trims it: the backend must, and re-read exactly the audio it will encode."""
    import os
    import soundfile as sf
    from services import asr_backend, tts_backend

    path = _window_file(tmp_path, _silence(_speech(15.0), 14.0, 14.3))
    reads = []
    monkeypatch.setattr(asr_backend, "transcribe_reference", lambda p, **_k: reads.append(p) or "Trimmed words.")

    out, text, owned = tts_backend._trim_selected_passage(path, "Words cut mid-wor")
    try:
        assert owned == out != path
        assert text == "Trimmed words." and reads == [out]
        assert sf.info(out).duration < sf.info(path).duration
    finally:
        os.remove(owned)


def test_backend_window_without_pauses_is_left_alone(monkeypatch, tmp_path):
    from services import asr_backend, tts_backend

    def must_not_run(*_a, **_k):
        raise AssertionError("a window with nothing to trim must not be re-read")

    path = _window_file(tmp_path, _speech(15.0))
    monkeypatch.setattr(asr_backend, "transcribe_reference", must_not_run)
    assert tts_backend._trim_selected_passage(path, "Whole window.") == (path, "Whole window.", None)


def test_backend_window_keeps_original_when_the_trimmed_copy_has_no_words(monkeypatch, tmp_path):
    import os
    from services import asr_backend, tts_backend

    path = _window_file(tmp_path, _silence(_speech(15.0), 14.0, 14.3))
    tried = []
    monkeypatch.setattr(asr_backend, "transcribe_reference", lambda p, **_k: tried.append(p) or "")
    assert tts_backend._trim_selected_passage(path, "Original.") == (path, "Original.", None)
    assert tried and not os.path.exists(tried[0])  # the temp copy was cleaned up


def test_trim_never_lengthens_and_keeps_order():
    wav = _silence(_silence(_speech(15.0), 0.5, 0.8), 14.2, 14.5)
    out = trim_passage_to_pauses(wav, SR)
    assert out.size(-1) < wav.size(-1)
    start = next(i for i in range(0, wav.size(-1) - out.size(-1) + 1, 600)
                 if torch.equal(wav[:, i : i + out.size(-1)], out))
    assert 0.5 * SR <= start <= 0.8 * SR
