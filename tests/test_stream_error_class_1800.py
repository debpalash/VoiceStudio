"""#1800 — an unclassified streaming failure must carry its exception class.

Every engine failure the taxonomy cannot classify renders the same floor
message, "Generation failed. Check the selected engine and try again." The
auto bug reporter puts that message and a stack of minified bundle frames into
the issue, so roughly a dozen separate faults arrived as byte-identical,
untriageable reports. The exception's TYPE NAME is what separates them.

It is the type name only. No substring of the exception message is copied, so
the response-safety contract in tests/test_response_safety.py still holds.
"""
import pytest

from core.public_errors import stream_failure, stream_generation_failure


def test_unclassified_failure_carries_its_exception_class():
    payload = stream_generation_failure(RuntimeError("CUDA error: device-side assert"))
    assert payload["code"] == "generation_failed"
    assert payload["error_class"] == "RuntimeError"


def test_two_unrelated_failures_are_distinguishable():
    # The whole point: these used to be the same report. Both must be failures
    # the taxonomy still cannot classify — that is the condition #1800 is about.
    # (`MemoryError` used to stand in here and no longer can: #2462 made it a
    # classified class, so it is no longer an example of the unclassified case.
    # Its own test covers it.) These two are still unclassified and still differ
    # only by type name.
    a = stream_generation_failure(RuntimeError("boom"))
    b = stream_generation_failure(ValueError("tensor shape mismatch"))
    assert a.get("docs_topic") is None and b.get("docs_topic") is None
    assert a["detail"] == b["detail"]
    assert a["error_class"] != b["error_class"]


def test_the_exception_message_is_never_copied():
    secret = "C:/Users/someone/private-voice-sample.wav"
    payload = stream_generation_failure(FileNotFoundError(secret))
    assert payload["error_class"] == "FileNotFoundError"
    for value in payload.values():
        assert secret not in str(value)
        assert "someone" not in str(value)


def test_a_non_exception_gets_no_class():
    # dict(...) of the plain floor payload, unchanged — nothing to name.
    payload = stream_generation_failure("not an exception")
    assert "error_class" not in payload
    assert payload["detail"] == stream_failure("generation_failed")["detail"]


@pytest.mark.parametrize("exc", [RuntimeError("x"), ValueError("y"), OSError("z")])
def test_class_is_present_alongside_a_classified_hint(exc):
    # Enrichment and the class name are independent: adding one must not drop
    # the other, whichever branch the taxonomy takes.
    payload = stream_generation_failure(exc)
    assert payload["error_class"] == type(exc).__name__
    assert payload["detail"]
