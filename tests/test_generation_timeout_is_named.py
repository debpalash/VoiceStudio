"""A generate that ran out of time must say so, not "an error we don't recognize".

Reported in #1368 (macOS M3 Max, MPS, indextts2):

    RuntimeError: TTS engine stopped mid-generation with an error OmniVoice
    doesn't recognize. Retry once; if it keeps failing, please report it with
    the full trace. Underlying error: TimeoutError:

Note what follows the last colon: nothing. `TimeoutError` is routinely raised
with an empty message, so the user was asked to report a trace that says
nothing, about the one failure mode whose cause is entirely known.

The classification chain (#880/#919) already refuses to blame VRAM for network
and config failures. A deadline is the same kind of thing — a known class with
a specific remedy — and it was falling through to the catch-all. Worse, had it
carried an OOM-ish word it would have hit the memory branch and told the user
to press Flush for memory they never ran out of, which is precisely the class
bug #880 fixed.

Ordering matters here and is asserted: timeout is checked BEFORE oom, and
network keeps ownership of "read timed out" (a dying download, which it
explains better than a generic deadline would).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import os
import sys

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


@pytest.fixture
def gen():
    """Resolve at call time — sibling suites reload/purge ``services.*``."""
    return importlib.import_module("api.routers.generation")


# ── recognised as a timeout ───────────────────────────────────────────────

def test_a_bare_timeout_error_is_recognised(gen):
    """The reported case, message and all: there isn't one."""
    assert gen._is_timeout_failure(TimeoutError()) is True


def test_asyncio_timeout_is_recognised(gen):
    assert gen._is_timeout_failure(asyncio.TimeoutError()) is True


def test_a_futures_timeout_is_recognised(gen):
    """What a pool job that overran its deadline raises."""
    assert gen._is_timeout_failure(concurrent.futures.TimeoutError()) is True


def test_the_gpu_pool_timeout_class_is_recognised(gen):
    """`GpuJobTimeoutError` is the project's own wrapper and is not a
    TimeoutError subclass, so it has to be matched by name."""
    class GpuJobTimeoutError(Exception):
        pass

    assert gen._is_timeout_failure(GpuJobTimeoutError("job exceeded 300.0s")) is True


def test_a_timeout_wrapped_in_another_exception_is_found(gen):
    """Engines wrap the original error, which is why the whole chain is
    walked rather than just the outermost type."""
    try:
        try:
            raise TimeoutError()
        except TimeoutError as inner:
            raise RuntimeError("engine call failed") from inner
    except RuntimeError as e:
        assert gen._is_timeout_failure(e) is True


def test_a_stringified_timeout_is_recognised(gen):
    """Sidecars stringify the child's error into the parent's message, so the
    type is gone by the time it reaches here."""
    assert gen._is_timeout_failure(RuntimeError("sidecar timed out after 600s")) is True


# ── NOT a timeout ─────────────────────────────────────────────────────────

def test_a_download_read_timeout_stays_with_the_network_branch(gen):
    """"read timed out" is a dying model download. The network branch names
    that and tells the user to check their connection / mirror, which is
    strictly more useful than a generic "it took too long"."""
    e = RuntimeError("HTTPSConnectionPool: Read timed out. (read timeout=10)")
    assert gen._is_timeout_failure(e) is False
    assert gen._is_network_failure(e) is True


def test_an_oom_is_not_a_timeout(gen):
    e = RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
    assert gen._is_timeout_failure(e) is False
    assert gen._is_oom_failure(e) is True


def test_an_ordinary_failure_is_not_a_timeout(gen):
    assert gen._is_timeout_failure(RuntimeError("tensor shape mismatch")) is False
    assert gen._is_timeout_failure(ValueError("bad speaker id")) is False


def test_a_config_failure_is_not_a_timeout(gen):
    assert gen._is_timeout_failure(
        RuntimeError("OMNIVOICE_SHERPA_MODEL not set. Point it to the model dir")
    ) is False


# ── the message the user actually gets ────────────────────────────────────

def _reraise(gen, exc):
    """Run the shared classifier and return the RuntimeError it produces."""
    with pytest.raises(RuntimeError) as caught:
        gen._oom_friendly_reraise(exc)
    return str(caught.value)


def test_the_message_names_the_time_limit_not_a_mystery(gen):
    msg = _reraise(gen, TimeoutError())
    assert "time limit" in msg
    assert "doesn't recognize" not in msg, (
        "a deadline is not an unrecognized error — this is the #1368 report"
    )


def test_the_message_does_not_blame_memory(gen):
    """The #880 class bug, in its newest disguise: never send someone to Flush
    for a failure that has nothing to do with memory."""
    msg = _reraise(gen, TimeoutError())
    assert "Flush" not in msg or "won't help" in msg
    assert "ran out of memory" not in msg


def test_the_message_explains_the_first_run_download_case(gen):
    """The most likely cause on a fresh install, and the one where "retry" is
    genuinely the right advice — the download resumes (#1367)."""
    msg = _reraise(gen, TimeoutError())
    assert "download" in msg.lower()
    assert "retry" in msg.lower()


def test_the_message_names_the_override(gen):
    """Someone on slow CPU-only hardware needs a way through, not just an
    explanation."""
    msg = _reraise(gen, TimeoutError())
    assert "OMNIVOICE_GENERATE_TIMEOUT_S" in msg
    assert "OMNIVOICE_CPU_GENERATE_TIMEOUT_S" in msg


def test_a_gguf_timeout_names_the_gguf_and_shared_knobs(gen):
    """A GGUF wedge used to tip only OMNIVOICE_GENERATE_TIMEOUT_S, which does
    not move the binary watchdog or the CPU Settings row."""
    msg = _reraise(
        gen,
        RuntimeError(
            "GGUF subprocess timed out after 600s (T-04-06; raise "
            "OMNIVOICE_GGUF_GENERATE_TIMEOUT_S if this host is genuinely "
            "that slow)"
        ),
    )
    assert "OMNIVOICE_GGUF_GENERATE_TIMEOUT_S" in msg
    assert "OMNIVOICE_CPU_GENERATE_TIMEOUT_S" in msg


def test_an_empty_timeout_message_does_not_end_the_sentence_in_a_colon(gen):
    """The literal reported output ended `Underlying error: TimeoutError:` —
    a sentence that stops mid-thought. Whatever we append, the useful content
    has to come before it."""
    msg = _reraise(gen, TimeoutError())
    assert not msg.rstrip().endswith(":"), msg
    assert len(msg) > 120, "the message carries no information beyond the type"


def test_oom_still_gets_the_flush_hint(gen):
    """The timeout branch runs first, so pin that it did not swallow the case
    the OOM branch exists for."""
    msg = _reraise(gen, RuntimeError("CUDA out of memory. Tried to allocate 2 GiB"))
    assert "Flush" in msg and "memory" in msg


def test_an_unrecognized_error_still_says_so(gen):
    """The catch-all must keep its job for genuinely unknown failures."""
    assert "doesn't recognize" in _reraise(gen, RuntimeError("tensor shape mismatch"))


def test_a_timeout_that_does_carry_a_message_keeps_it(gen):
    """Dropping the tail is only right when it is empty. A sidecar that says
    *which* deadline it blew is the most useful line in the report."""
    msg = _reraise(gen, TimeoutError("sidecar recv exceeded 600s"))
    assert "sidecar recv exceeded 600s" in msg
    assert "Underlying error:" in msg
