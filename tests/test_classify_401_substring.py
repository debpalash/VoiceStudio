"""Three digits in a path are not an authentication failure.

`classify()` matched a bare ``"401"`` substring, and used it to satisfy *both*
halves of the HF-auth condition — so any message containing those digits
anywhere classified as ``HF_AUTH_FAILED`` on its own. Paths, byte counts, job
ids and durations all qualify.

It surfaced in CI when pytest's numbered temp directory reached
``pytest-401``: an audio-save failure came back telling the user to set a
valid ``HF_TOKEN``. That is worse than an unclassified error — it is a
confident wrong instruction, attached to a docs deeplink, in an auto-filed bug
report. And because it depends on a counter that changes between runs, it is
the kind of bug that passes locally forever.

Two independent guards now: the digits must be a standalone token, and they
are no longer sufficient evidence by themselves.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def classify():
    from core.failure import classify as _classify

    return _classify


# ── the digits alone must never classify ──────────────────────────────────

NOT_AUTH = [
    # The exact CI failure.
    "Error opening '/tmp/pytest-of-runner/pytest-401/t0/speech.wav': System error.",
    "wrote 4012 bytes to disk",
    "job 1401 failed to start",
    "sample rate 44100, offset 401",
    "/home/user/Music/401 tracks/out.wav could not be written",
    "took 2401ms",
]


@pytest.mark.parametrize("text", NOT_AUTH)
def test_a_number_containing_401_is_not_an_auth_failure(classify, text):
    assert classify(text) != "HF_AUTH_FAILED"


def test_the_ci_failure_keeps_its_own_class(classify):
    """Not merely 'not HF_AUTH_FAILED' — it must still classify correctly, or
    the fix would trade one wrong answer for no answer."""
    assert classify(
        "Error opening '/tmp/pytest-of-runner/pytest-401/t0/speech.wav': "
        "System error."
    ) in ("", "AUDIO_IO_FAILED")


# ── real auth failures still classify ─────────────────────────────────────

IS_AUTH = [
    "401 Client Error: Unauthorized for url: https://huggingface.co/api/models/x",
    "Invalid credentials in Authorization header (huggingface.co)",
    "huggingface.co returned 401",
    "hf_token is invalid or expired",
    "Unauthorized: your token does not have access to this repo",
]


@pytest.mark.parametrize("text", IS_AUTH)
def test_a_real_auth_failure_still_classifies(classify, text):
    assert classify(text) == "HF_AUTH_FAILED"


# ── the token boundary, independently ─────────────────────────────────────

@pytest.mark.parametrize(
    "text,expected",
    [
        ("huggingface.co status 401", True),
        ("huggingface.co status 4012", False),
        ("huggingface.co status 1401", False),
        ("huggingface.co (401)", True),
        ("huggingface.co HTTP/1.1 401", True),
        # A trailing sentence full stop is punctuation, not a decimal point.
        ("huggingface.co status 401.", True),
        # Digit-only boundaries accepted all of these (CodeRabbit, #1427).
        # Each is a different family, and each needs its own guard:
        # identifier context, hyphenated context, and dotted numerics.
        ("huggingface.co x401y", False),
        ("huggingface.co pytest-401", False),
        ("huggingface.co 401.0", False),
        ("huggingface.co 401.25", False),
        ("huggingface.co v1.401", False),
        ("huggingface.co 401k", False),
        ("huggingface.co run-401-retry", False),
        ("huggingface.co /tmp/pytest-of-runner/pytest-401/speech.wav", False),
        ("huggingface.co clip401.wav", False),
    ],
)
def test_401_is_matched_as_a_whole_number(text, expected):
    from core.failure import _HTTP_401

    assert bool(_HTTP_401.search(text)) is expected


@pytest.mark.parametrize(
    "text",
    [
        # The residual the digit-only boundary left open: an HF marker in the
        # same message meant any of these still reached HF_AUTH_FAILED, because
        # the subject half was satisfied by "huggingface" and the symptom half
        # by the stray digits.
        "huggingface.co upload failed writing /tmp/pytest-401/chunk.bin",
        "huggingface.co cache entry clip401.wav could not be opened",
        "huggingface.co model v1.401 is not available",
    ],
)
def test_an_hf_marker_does_not_make_stray_digits_an_auth_failure(classify, text):
    assert classify(text) != "HF_AUTH_FAILED", (
        "a message that merely mentions Hugging Face and happens to contain "
        "401 elsewhere was given the 'set a valid HF_TOKEN' remedy"
    )
