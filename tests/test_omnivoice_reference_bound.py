"""Long cloning references must fail before unbounded tokenizer allocation (#1578)."""

from types import SimpleNamespace

import pytest
import soundfile as sf
import torch

class _Tokenizer:
    config = SimpleNamespace(hop_length=320)
    device = "cpu"

    def __init__(self, reject=True):
        self.reject = reject
        self.seen_samples = None

    def encode(self, audio):
        self.seen_samples = audio.shape[-1]
        self.seen_peak = float(audio.abs().max())
        if self.reject:
            raise AssertionError("an unsafe reference reached the audio tokenizer")
        return SimpleNamespace(audio_codes=torch.zeros((1, 1, 1), dtype=torch.long))


def _model(*, reject_tokenization=True):
    from omnivoice.models.omnivoice import OmniVoice

    model = OmniVoice.__new__(OmniVoice)
    model.sampling_rate = 24_000
    model.audio_tokenizer = _Tokenizer(reject=reject_tokenization)
    return model


def test_supplied_transcript_rejects_long_tensor_before_tokenization():
    audio = torch.full((1, 21 * 24_000), 0.1)

    with pytest.raises(ValueError, match=r"\[clone_ref_too_long\].*at most 20 seconds"):
        _model().create_voice_clone_prompt(
            (audio, 24_000),
            ref_text="Transcript supplied by the user.",
            preprocess_prompt=True,
        )


def test_supplied_transcript_rejects_long_file_before_tokenization(tmp_path):
    path = tmp_path / "long-reference.wav"
    sf.write(path, torch.full((21 * 24_000,), 0.1).numpy(), 24_000)

    with pytest.raises(ValueError, match=r"\[clone_ref_too_long\].*at most 20 seconds"):
        _model().create_voice_clone_prompt(
            str(path),
            ref_text="Transcript supplied by the user.",
            preprocess_prompt=True,
        )


def test_missing_transcript_is_safely_trimmed_even_when_preprocessing_is_disabled():
    model = _model(reject_tokenization=False)
    model._asr_pipe = object()
    model.transcribe = lambda _audio: "Automatically aligned transcript."
    audio = torch.full((1, 21 * 24_000), 0.1)

    model.create_voice_clone_prompt(
        (audio, 24_000),
        ref_text=None,
        preprocess_prompt=False,
    )

    assert model.audio_tokenizer.seen_samples <= 15 * 24_000


def test_missing_transcript_still_has_a_hard_bound_when_silence_split_cannot_trim():
    model = _model(reject_tokenization=False)
    model._asr_pipe = object()
    model.transcribe = lambda _audio: "Automatically aligned transcript."
    audio = torch.full((1, 21 * 24_000), 0.1)

    model.create_voice_clone_prompt((audio, 24_000), ref_text=None)

    assert model.audio_tokenizer.seen_samples <= 15 * 24_000


def test_hard_bound_keeps_late_speech_instead_of_cropping_only_silence(monkeypatch):
    model = _model(reject_tokenization=False)
    model._asr_pipe = object()
    model.transcribe = lambda _audio: "Automatically aligned transcript."
    monkeypatch.setattr(
        "omnivoice.models.omnivoice.remove_silence_safe",
        lambda audio, *_args, **_kwargs: audio,
    )
    audio = torch.zeros((1, 21 * 24_000))
    audio[:, 16 * 24_000 :] = 0.1

    model.create_voice_clone_prompt((audio, 24_000), ref_text=None)

    assert model.audio_tokenizer.seen_samples <= 15 * 24_000
    assert model.audio_tokenizer.seen_peak > 0


def test_hard_bound_prefers_speech_over_distant_transient(monkeypatch):
    model = _model(reject_tokenization=False)
    model._asr_pipe = object()
    model.transcribe = lambda _audio: "Automatically aligned transcript."
    monkeypatch.setattr(
        "omnivoice.models.omnivoice.remove_silence_safe",
        lambda audio, *_args, **_kwargs: audio,
    )
    audio = torch.zeros((1, 31 * 24_000))
    audio[:, 1 * 24_000] = 1.0
    audio[:, 26 * 24_000 :] = 0.01

    model.create_voice_clone_prompt((audio, 24_000), ref_text=None)

    assert model.audio_tokenizer.seen_samples <= 15 * 24_000
    # Quiet references are RMS-normalized before cropping. The speech peak is
    # therefore non-zero but remains far below the isolated transient.
    assert 0 < model.audio_tokenizer.seen_peak < 1


def test_real_silence_splitter_does_not_keep_silent_prefix(monkeypatch):
    model = _model(reject_tokenization=False)
    model._asr_pipe = object()
    model.transcribe = lambda _audio: "Automatically aligned transcript."
    monkeypatch.setattr(
        "omnivoice.models.omnivoice.remove_silence_safe",
        lambda audio, *_args, **_kwargs: audio,
    )
    audio = torch.zeros((1, 21 * 24_000))
    audio[:, 16 * 24_000 :] = 0.1

    model.create_voice_clone_prompt((audio, 24_000), ref_text=None)

    assert model.audio_tokenizer.seen_samples <= 15 * 24_000
    assert model.audio_tokenizer.seen_peak > 0


def test_real_silence_splitter_ignores_transient_before_late_speech(monkeypatch):
    model = _model(reject_tokenization=False)
    model._asr_pipe = object()
    model.transcribe = lambda _audio: "Automatically aligned transcript."
    monkeypatch.setattr(
        "omnivoice.models.omnivoice.remove_silence_safe",
        lambda audio, *_args, **_kwargs: audio,
    )
    audio = torch.zeros((1, 21 * 24_000))
    audio[:, 1 * 24_000] = 1.0
    audio[:, 16 * 24_000 :] = 0.01

    model.create_voice_clone_prompt((audio, 24_000), ref_text=None)

    assert model.audio_tokenizer.seen_samples <= 15 * 24_000
    assert 0 < model.audio_tokenizer.seen_peak < 1


def test_speech_aware_bound_prefers_quiet_voice_over_loud_non_speech(monkeypatch):
    model = _model(reject_tokenization=False)
    model._asr_pipe = object()
    model.transcribe = lambda candidate: (
        "Spoken words." if float(candidate[0].abs().mean()) < 0.05 else ""
    )
    monkeypatch.setattr(
        "omnivoice.models.omnivoice.remove_silence_safe",
        lambda audio, *_args, **_kwargs: audio,
    )
    audio = torch.full((1, 30 * 24_000), 0.4)
    audio[:, 15 * 24_000 :] = 0.02

    prompt = model.create_voice_clone_prompt((audio, 24_000), ref_text=None)

    assert prompt.ref_text == "Spoken words."
    assert model.audio_tokenizer.seen_peak < 0.1


def test_selected_passage_rms_undoes_input_gain(monkeypatch):
    model = _model(reject_tokenization=False)
    model._asr_pipe = object()
    model.transcribe = lambda candidate: (
        "Selected speech." if float(candidate[0].abs().mean()) > 0.1 else ""
    )
    monkeypatch.setattr(
        "omnivoice.models.omnivoice.remove_silence_safe",
        lambda audio, *_args, **_kwargs: audio,
    )
    audio = torch.full((1, 30 * 24_000), 0.01)
    audio[:, 15 * 24_000 :] = 0.02

    prompt = model.create_voice_clone_prompt((audio, 24_000), ref_text=None)

    assert prompt.ref_text == "Selected speech."
    assert prompt.ref_rms == pytest.approx(0.02)


def test_far_late_speech_at_auto_select_limit_survives_with_five_asr_calls(monkeypatch):
    model = _model(reject_tokenization=False)
    model.sampling_rate = 100
    model._asr_pipe = object()
    calls = []

    def transcribe(candidate):
        calls.append(candidate[0])
        return "Late speech." if float(candidate[0][0, -1]) > 0 else ""

    model.transcribe = transcribe
    monkeypatch.setattr(
        "omnivoice.models.omnivoice.remove_silence_safe",
        lambda audio, *_args, **_kwargs: audio,
    )
    audio = torch.zeros((1, 75 * 100))
    audio[:, -5 * 100 :] = 0.02

    prompt = model.create_voice_clone_prompt((audio, 100), ref_text=None)

    assert prompt.ref_text == "Late speech."
    assert len(calls) == 5


def test_sixteen_second_reference_uses_bounded_selection_without_preprocessing():
    model = _model(reject_tokenization=False)
    model.sampling_rate = 100
    model._asr_pipe = object()
    lengths = []

    def transcribe(candidate):
        lengths.append(candidate[0].size(-1))
        return "Speech."

    model.transcribe = transcribe
    audio = torch.full((1, 16 * 100), 0.1)

    model.create_voice_clone_prompt(
        (audio, 100), ref_text=None, preprocess_prompt=False
    )

    assert lengths == [1500, 100]


def test_auto_select_rejects_one_sample_over_limit_before_asr():
    model = _model(reject_tokenization=False)
    model.sampling_rate = 100
    model._asr_pipe = object()
    calls = []
    model.transcribe = lambda candidate: calls.append(candidate) or "Speech."
    audio = torch.full((1, 75 * 100 + 1), 0.1)

    with pytest.raises(
        ValueError, match=r"\[clone_ref_too_long\].*at most 75 seconds"
    ) as caught:
        model.create_voice_clone_prompt((audio, 100), ref_text=None)

    # Over 20 s a typed transcript is refused, so it must not be suggested.
    assert "matching transcript" not in str(caught.value)
    assert calls == []


def test_auto_select_tiles_final_partial_window_without_gaps(monkeypatch):
    model = _model(reject_tokenization=False)
    model.sampling_rate = 100
    model._asr_pipe = object()
    lengths = []

    def transcribe(candidate):
        lengths.append(candidate[0].size(-1))
        return "Speech."

    model.transcribe = transcribe
    monkeypatch.setattr(
        "omnivoice.models.omnivoice.remove_silence_safe",
        lambda audio, *_args, **_kwargs: audio,
    )
    audio = torch.full((1, 61 * 100), 0.1)

    model.create_voice_clone_prompt((audio, 100), ref_text=None)

    assert lengths == [1500, 1500, 1500, 1500, 100]


def test_auto_select_rejects_punctuation_only_transcripts_before_tokenizer():
    model = _model(reject_tokenization=True)
    model.sampling_rate = 100
    model._asr_pipe = object()
    model.transcribe = lambda _candidate: "...?!"
    audio = torch.full((1, 21 * 100), 0.1)

    with pytest.raises(
        ValueError, match=r"\[clone_ref_no_speech\].*could not find spoken words"
    ) as caught:
        model.create_voice_clone_prompt((audio, 100), ref_text=None)
    assert "matching transcript" not in str(caught.value)


def test_auto_select_no_speech_within_transcript_limit_still_offers_transcript():
    model = _model(reject_tokenization=True)
    model.sampling_rate = 100
    model._asr_pipe = object()
    model.transcribe = lambda _candidate: "...?!"
    audio = torch.full((1, 18 * 100), 0.1)

    with pytest.raises(ValueError, match=r"\[clone_ref_no_speech\].*matching transcript"):
        model.create_voice_clone_prompt((audio, 100), ref_text=None)


_REAL_SPEECH = (
    "Bonjour à tous, aujourd'hui je vais vous montrer comment préparer une tarte "
    "aux pommes avec une pâte brisée maison, des pommes bien mûres et un peu de "
    "cannelle pour relever le goût."
)
_LOOP = "Thank you. " * 148


@pytest.mark.parametrize("loop", [_LOOP, "Thank you. " * 4, "Thank you. " * 4 + "Okay."])
def test_auto_select_skips_a_whisper_repetition_loop_on_a_silent_window(monkeypatch, loop):
    # #2836: Whisper loops "Thank you." over a near-silent window; the loop is
    # longer than any real passage and used to win the selection.
    model = _model(reject_tokenization=False)
    model.sampling_rate = 100
    model._asr_pipe = object()
    model.transcribe = lambda candidate: (
        "Hi there." if float(candidate[0].abs().mean()) > 0.01 else loop
    )
    monkeypatch.setattr(
        "omnivoice.models.omnivoice.remove_silence_safe",
        lambda audio, *_args, **_kwargs: audio,
    )
    audio = torch.full((1, 30 * 100), 0.05)
    audio[:, 15 * 100 :] = 0.001

    prompt = model.create_voice_clone_prompt((audio, 100), ref_text=None)

    assert prompt.ref_text == "Hi there."


@pytest.mark.parametrize("speech", [_REAL_SPEECH, "今日はとても良い天気ですね。公園を散歩しましょう。"])
def test_auto_select_keeps_real_speech_against_a_shorter_window(monkeypatch, speech):
    model = _model(reject_tokenization=False)
    model.sampling_rate = 100
    model._asr_pipe = object()
    model.transcribe = lambda candidate: (
        speech if float(candidate[0].abs().mean()) > 0.01 else "Wow."
    )
    monkeypatch.setattr(
        "omnivoice.models.omnivoice.remove_silence_safe",
        lambda audio, *_args, **_kwargs: audio,
    )
    audio = torch.full((1, 30 * 100), 0.001)
    audio[:, 15 * 100 :] = 0.05

    prompt = model.create_voice_clone_prompt((audio, 100), ref_text=None)

    # add_punctuation may append a final mark (。) to an unpunctuated CJK end.
    assert prompt.ref_text.startswith(speech)


@pytest.mark.parametrize("loop", [_LOOP, "Thank you. " * 4])
def test_auto_select_with_only_repetition_loops_reports_no_speech(loop):
    model = _model(reject_tokenization=True)
    model.sampling_rate = 100
    model._asr_pipe = object()
    model.transcribe = lambda _candidate: loop
    audio = torch.full((1, 30 * 100), 0.001)

    with pytest.raises(ValueError, match=r"\[clone_ref_no_speech\]"):
        model.create_voice_clone_prompt((audio, 100), ref_text=None)


def test_auto_select_preserves_short_genuine_repeated_speech(monkeypatch):
    model = _model(reject_tokenization=False)
    model.sampling_rate = 100
    model._asr_pipe = object()
    model.transcribe = lambda _candidate: "Testing one two. " * 3
    monkeypatch.setattr("omnivoice.models.omnivoice.remove_silence_safe", lambda audio, *_args, **_kwargs: audio)
    audio = torch.full((1, 30 * 100), 0.05)
    prompt = model.create_voice_clone_prompt((audio, 100), ref_text=None)
    assert prompt.ref_text == "Testing one two. Testing one two. Testing one two."


@pytest.mark.parametrize("amplitude", [0.05, 0.0001])
def test_auto_select_keeps_repeated_speech_over_quiet_short_text(monkeypatch, amplitude):
    model = _model(reject_tokenization=False)
    model.sampling_rate = 100
    model._asr_pipe = object()
    speech = "I can do it, I can, I can do it."
    model.transcribe = lambda candidate: speech if float(candidate[0].std()) > 0.01 else "Wow."
    monkeypatch.setattr("omnivoice.models.omnivoice.remove_silence_safe", lambda audio, *_args, **_kwargs: audio)
    waveform = torch.sin(torch.arange(1500) * 0.2).unsqueeze(0) * amplitude
    audio = torch.cat([waveform, torch.full_like(waveform, amplitude / 100)], dim=-1)
    prompt = model.create_voice_clone_prompt((audio, 100), ref_text=None)
    assert prompt.ref_text == speech


def test_auto_select_keeps_only_spoken_passage_beside_louder_non_speech(monkeypatch):
    model = _model(reject_tokenization=False)
    model.sampling_rate = 100
    model._asr_pipe = object()
    speech = "Testing one two. Testing one two. Testing one two."
    model.transcribe = lambda candidate: speech if float(candidate[0].std()) > 0.0001 else ""
    monkeypatch.setattr("omnivoice.models.omnivoice.remove_silence_safe", lambda audio, *_args, **_kwargs: audio)
    waveform = torch.sin(torch.arange(1500) * 0.2).unsqueeze(0) * 0.001
    audio = torch.cat([waveform, torch.full_like(waveform, 0.5)], dim=-1)
    assert model.create_voice_clone_prompt((audio, 100), ref_text=None).ref_text == speech


def test_selected_passage_is_retranscribed_after_edge_trim(monkeypatch):
    """A passage cut back to its pauses gets a transcript of exactly that audio."""
    model = _model(reject_tokenization=False)
    model.sampling_rate = 100
    model._asr_pipe = object()
    lengths = []

    def transcribe(candidate):
        lengths.append(candidate[0].size(-1))
        return "Speech."

    model.transcribe = transcribe
    monkeypatch.setattr(
        "omnivoice.models.omnivoice.remove_silence_safe",
        lambda audio, *_args, **_kwargs: audio,
    )
    audio = torch.full((1, 16 * 100), 0.1)
    audio[:, 1400:1430] = 0.0  # pause 1 s before the first window's fixed cut

    model.create_voice_clone_prompt((audio, 100), ref_text=None)

    assert lengths[:2] == [1500, 100]
    assert len(lengths) == 3 and 1400 <= lengths[2] <= 1430


def test_trim_transcription_failure_keeps_the_untrimmed_passage(monkeypatch):
    """A recognizer that raises on the shorter trimmed window must not abort
    cloning when the untrimmed passage already has a transcript."""
    model = _model(reject_tokenization=False)
    model.sampling_rate = 100
    model._asr_pipe = object()

    calls = []

    def transcribe(candidate):
        calls.append(candidate[0].size(-1))
        if len(calls) == 3:  # two ranking windows, then the trimmed re-read
            raise RuntimeError("recognizer failed on the trimmed window")
        return "Speech."

    model.transcribe = transcribe
    monkeypatch.setattr(
        "omnivoice.models.omnivoice.remove_silence_safe",
        lambda audio, *_args, **_kwargs: audio,
    )
    audio = torch.full((1, 16 * 100), 0.1)
    audio[:, 1400:1430] = 0.0

    prompt = model.create_voice_clone_prompt((audio, 100), ref_text=None)

    assert len(calls) == 3  # the failing trimmed read really happened
    assert prompt.ref_text == "Speech."


def test_trim_that_leaves_no_words_keeps_the_untrimmed_passage(monkeypatch):
    """A usable passage must not become a "no speech" error because its
    trimmed copy transcribes empty."""
    model = _model(reject_tokenization=False)
    model.sampling_rate = 100
    model._asr_pipe = object()
    lengths = []

    def transcribe(candidate):
        lengths.append(candidate[0].size(-1))
        return "Speech." if candidate[0].size(-1) >= 1500 else ""

    model.transcribe = transcribe
    monkeypatch.setattr(
        "omnivoice.models.omnivoice.remove_silence_safe",
        lambda audio, *_args, **_kwargs: audio,
    )
    audio = torch.full((1, 16 * 100), 0.1)
    audio[:, 1400:1430] = 0.0

    prompt = model.create_voice_clone_prompt((audio, 100), ref_text=None)

    assert prompt.ref_text == "Speech."
    assert len(lengths) == 3 and lengths[2] < 1500  # the trim was tried, then dropped
