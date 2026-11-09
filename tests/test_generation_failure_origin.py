"""Generation failures log where they were raised, never what they said.

#2895/#2684/#2666/#2626/#2622/#2580 were six reports of the identical floor
message and an identical log line (``class=RuntimeError``), so nothing in them
could separate an engine bug from a GPU that another program had filled.
"""
import logging
import sys

sys.path.insert(0, "backend")

from core import failure  # noqa: E402
from core import public_errors  # noqa: E402


def _raise_deep():
    try:
        raise KeyError("/home/alice/secret-voice.wav")
    except KeyError as inner:
        raise RuntimeError("token sk-abc123 at /home/alice/x.wav") from inner


def test_origin_names_frame_and_cause_but_no_message():
    try:
        _raise_deep()
    except RuntimeError as e:
        origin = public_errors.exception_origin(e)
    assert origin.startswith("at=")
    assert "_raise_deep" in origin and "caused_by=KeyError" in origin
    for leaked in ("alice", "sk-abc123", "secret-voice"):
        assert leaked not in origin


def test_origin_never_raises_on_non_exceptions():
    assert public_errors.exception_origin("boom") == ""


def test_public_failure_log_carries_origin(caplog):
    log = logging.getLogger("t")
    try:
        _raise_deep()
    except RuntimeError as e:
        with caplog.at_level(logging.ERROR, logger="t"):
            public_errors.public_failure(log, "Boom", e, response="x")
    assert "_raise_deep" in caplog.text and "alice" not in caplog.text


def test_gpu_oom_hint_offers_another_gpu():
    assert "different one in Settings" in failure._HINTS["GPU_OOM"]
