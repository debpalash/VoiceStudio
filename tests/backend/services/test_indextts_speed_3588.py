"""#3588: IndexTTS accepted the public ``speed`` and silently dropped it.

``speed`` now becomes the duration it implies (2.5 factor + legacy tokens),
explicit duration controls take precedence, and ``/ws/tts`` forwards a
validated ``duration_factor``.
"""
from __future__ import annotations

import pytest

TEXT = "abcdefghijklmno"  # fifteen English characters: one natural second


@pytest.fixture
def forwarded(monkeypatch):
    from services.subprocess_backend import SubprocessBackend
    from services.tts_backend import IndexTTS2Backend

    sent: list[dict] = []

    def capture(self, text, **kw):
        sent.append(kw)
        return "audio"

    monkeypatch.setattr(SubprocessBackend, "generate", capture)
    backend = IndexTTS2Backend.__new__(IndexTTS2Backend)
    monkeypatch.setattr(IndexTTS2Backend, "_check_language", lambda self, language: None)

    def run(**kw):
        sent.clear()
        backend.generate(TEXT, ref_audio="/tmp/ref.wav", language="en", **kw)
        return sent[0]

    return run


def test_speed_reaches_the_engine_as_native_duration(forwarded):
    payload = forwarded(speed=1.25)
    assert payload["duration_factor"] == pytest.approx(0.8)
    assert payload["target_tokens"] == int(0.8 * 21)
    assert "speed" not in payload


def test_normal_or_invalid_speed_adds_no_duration_control(forwarded):
    for speed in (1.0, None, 0, -2, "fast", float("nan")):
        payload = forwarded(speed=speed)
        assert "duration_factor" not in payload
        assert "target_tokens" not in payload


def test_explicit_duration_controls_win_over_speed(forwarded):
    assert forwarded(speed=1.4, duration_factor=1.5)["duration_factor"] == 1.5
    payload = forwarded(speed=1.4, duration=2.0)
    assert payload["duration_factor"] == pytest.approx(2.0)
    assert payload["target_tokens"] == 42


def test_speed_keeps_the_emotion_controls(forwarded):
    payload = forwarded(speed=2.0, emo_text="calm", emo_alpha=0.5)
    assert payload["duration_factor"] == pytest.approx(0.5)
    assert payload["emo_text"] == "calm"
    assert payload["emo_alpha"] == 0.5


def test_stream_forwards_a_validated_duration_factor():
    from api.routers.tts_stream import build_stream_kwargs

    kw = build_stream_kwargs({"speed": 1.4, "duration_factor": 0.7})
    assert kw["speed"] == 1.4
    assert kw["duration_factor"] == 0.7
    assert "duration_factor" not in build_stream_kwargs({"speed": 1.0})
    for bad in (0.1, 3, "0.7", True, float("inf")):
        with pytest.raises(ValueError, match="duration_factor"):
            build_stream_kwargs({"duration_factor": bad})
