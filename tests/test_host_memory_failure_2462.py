"""#2462 — a generation that fails because the HOST ran out of memory must say so.

The report is a bare floor message on a machine with no GPU:

    Generation failed. Check the selected engine and try again.
    Backend error class: RuntimeError
    StreamingPreviewError: Generation failed. Check the selected engine and try again.
    … Compute device: cpu … RAM: 7.9 GB

The cause is structural, not incidental. `_GPU_OOM_SIGNATURES` names only DEVICE
allocators — CUDA, MPS, HIP, "out of memory on device" — so on a CPU-only host
the taxonomy has no entry for the one memory failure that can happen there.
torch raises it as a bare ``RuntimeError`` from its CPU allocator, classify()
returns "" and the streaming frame can only say "check the selected engine",
which sends the reporter to an engine that is fine. #2177 closed exactly this
gap for the unsupported-GPU class; this is the other half — the machine, not
the card.

Two things must not move: a real device OOM keeps GPU_OOM and its VRAM remedy,
and a genuinely unknown failure keeps the byte-identical floor message (#1943's
"confidently wrong remedy" is worse than no remedy).

App modules are resolved inside the `diagnosis` fixture, not at import time —
see the note there.
"""
import pytest


@pytest.fixture
def diagnosis():
    """Resolve the app modules when the test RUNS, never at collection time.

    An app module bound at collection holds a reference to the module object
    that was importable then. Anything that replaces an entry in ``sys.modules``
    afterwards (the isolation fixtures, the ``monkeypatch.setitem`` reload
    guards other suites use) leaves that reference pointing at the stale copy,
    and the test then asserts against code the process is no longer running.
    Looking the modules up per test cannot go stale.
    """
    import importlib
    from types import SimpleNamespace

    failure = importlib.import_module("core.failure")
    public = importlib.import_module("core.public_errors")
    journal = importlib.import_module("core.error_journal")
    return SimpleNamespace(
        classify=failure.classify,
        context_free=failure._CONTEXT_FREE_HINT_CLASSES,
        hints=failure._HINTS,
        host_oom_signatures=failure._HOST_OOM_SIGNATURES,
        is_host_oom=failure.is_host_oom,
        public_exception_response=public.public_exception_response,
        stream_generation_failure=public.stream_generation_failure,
        journal_classify=journal.classify_exception,
        journal_rules=journal._CLASS_RULES,
    )


# The report's own failure: torch's CPU allocator on a 7.9 GB, GPU-less machine.
TORCH_CPU_ALLOCATOR = (
    "DefaultCPUAllocator: not enough memory: you tried to allocate 2147483648 bytes."
)
WINDOWS_WINERROR_8 = "[WinError 8] Not enough memory to continue the execution of the program"
BAD_ALLOC = "std::bad_alloc"
C_ALLOC_FAIL = "[enforce fail at alloc_cpu.cpp:75] . Can't allocate memory: you tried to allocate..."

# `generation._oom_friendly_reraise` replaces the allocator's own wording with
# this prose, so by the time the streaming frame sees the failure the signature
# that identified it is gone — only the `from e` cause still carries the type.
ROUTER_OOM_PROSE = (
    "TTS engine stopped mid-generation. This usually means it ran out of memory. "
    "Try the Flush button to reload the model, then regenerate. Underlying error: "
)

# Signatures that must NOT be claimed. Each is a real phrasing a dependency or
# the OS produces for something that has nothing to do with RAM — the #1943
# shape. A hint that names the wrong cause is the failure mode of this module.
NOT_HOST_OOM = [
    "No space left on device",                       # a disk, not RAM
    "[WinError 1455] The paging file is too small for this operation to complete",
    "The read operation timed out",
    "Connection reset by peer",
    "CUDA out of memory. Tried to allocate 2.00 GiB",  # the device class
]


# ── the reported failure ────────────────────────────────────────────────────


def test_a_cpu_host_that_runs_out_of_memory_is_named_instead_of_the_floor_message(diagnosis):
    sgf = diagnosis.stream_generation_failure
    payload = sgf(RuntimeError(TORCH_CPU_ALLOCATOR))

    assert payload["docs_topic"] == "HOST_MEMORY_EXHAUSTED"
    assert payload["hint"]
    assert payload["docs_url"].endswith("#generation-failure-diagnosis")
    # The floor message is still the lead; the hint is appended to it, exactly
    # as the other context-free classes render.
    assert payload["detail"].startswith(sgf(RuntimeError("boom"))["detail"])
    assert payload["detail"] != sgf(RuntimeError("boom"))["detail"]


def test_the_remedy_is_about_system_memory_not_vram(diagnosis):
    """A CPU-only reporter must not be told to free VRAM or to choose CPU —
    that is what GPU_OOM's hint says, and both halves of it are wrong here."""
    hint = diagnosis.stream_generation_failure(RuntimeError(TORCH_CPU_ALLOCATOR))["hint"]

    assert "memory" in hint.lower()
    assert "GPU-heavy" not in hint
    assert "choose CPU" not in hint
    # Something the user can actually do on a laptop with no discrete GPU.
    assert "Flush models" in hint


def test_it_stays_retryable_rather_than_terminal(diagnosis):
    """Unlike #2177's build mismatch, host RAM frees up: closing a browser tab
    genuinely repairs it, so the floor message's "try again" is still true."""
    payload = diagnosis.stream_generation_failure(RuntimeError(TORCH_CPU_ALLOCATOR))

    assert payload.get("terminal", False) is False
    assert payload["retryable"] is True


def test_the_exception_class_still_rides_along(diagnosis):
    # #1800's guarantee must survive the new branch.
    payload = diagnosis.stream_generation_failure(RuntimeError(TORCH_CPU_ALLOCATOR))
    assert payload["error_class"] == "RuntimeError"


@pytest.mark.parametrize("message", [
    TORCH_CPU_ALLOCATOR,
    WINDOWS_WINERROR_8,
    BAD_ALLOC,
    C_ALLOC_FAIL,
    "[Errno 12] Cannot allocate memory",
])
def test_every_host_oom_spelling_reaches_the_user(message, diagnosis):
    payload = diagnosis.stream_generation_failure(RuntimeError(message))
    assert payload["docs_topic"] == "HOST_MEMORY_EXHAUSTED"
    assert payload["hint"]


# ── a failure that carries no signature to match on ──────────────────────────
# Both of these are the SAME bug #2462 reports, reached two ways. The class is
# correct, but every way of REACHING it here loses the evidence a message
# matcher needs, so a message-only classifier answers "" and the user is back
# to the floor message.


def test_a_bare_memory_error_is_named_despite_having_no_message(diagnosis):
    """`str(MemoryError())` is the empty string — the failure names nothing at
    all in text, and the type is the only evidence that exists."""
    assert str(MemoryError()) == ""

    payload = diagnosis.stream_generation_failure(MemoryError())
    assert payload["docs_topic"] == "HOST_MEMORY_EXHAUSTED"
    assert payload["hint"]
    assert payload["retryable"] is True


def test_an_oom_the_router_re_wrapped_is_still_named(diagnosis):
    """`generation._oom_friendly_reraise` already decided this IS an OOM, then
    re-raised it as its own prose — which carries none of the allocator
    signatures, so the streaming frame classified it to nothing. The original
    survives only as the `from e` cause."""
    wrapped = RuntimeError(ROUTER_OOM_PROSE)
    wrapped.__cause__ = MemoryError()

    # Proof the prose alone is not enough: the message path cannot see it.
    assert diagnosis.classify(ROUTER_OOM_PROSE) == ""

    payload = diagnosis.stream_generation_failure(wrapped)
    assert payload["docs_topic"] == "HOST_MEMORY_EXHAUSTED"
    assert payload["hint"]


def test_a_chained_host_oom_is_named_through_several_wrappers(diagnosis):
    """Engines wrap the original error, sometimes twice before the router sees
    it — the chain is the only place the allocator wording survives."""
    try:
        try:
            raise MemoryError()
        except MemoryError as inner:
            raise RuntimeError("voice-clone prompt precompute failed") from inner
    except RuntimeError as wrapped:
        again = RuntimeError(ROUTER_OOM_PROSE)
        again.__cause__ = wrapped

    assert diagnosis.stream_generation_failure(again)["docs_topic"] == (
        "HOST_MEMORY_EXHAUSTED"
    )


# ── the other memory classes must be untouched ──────────────────────────────


def test_a_device_oom_keeps_its_own_class_and_vram_remedy(diagnosis):
    payload = diagnosis.stream_generation_failure(
        RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
    )

    assert payload["docs_topic"] == "GPU_OOM"
    assert payload["retryable"] is True
    assert "GPU-heavy" in payload["hint"]


def test_a_device_oom_nested_in_a_host_oom_keeps_the_device_class(diagnosis):
    """GPU-first precedence must hold on the exception path too: an exception
    that says both is still a GPU problem, and telling its user to close
    memory-heavy apps instead of freeing VRAM would be the wrong remedy."""
    both = RuntimeError(ROUTER_OOM_PROSE)
    both.__cause__ = RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")

    assert diagnosis.classify(both) == "GPU_OOM"
    assert diagnosis.stream_generation_failure(both)["docs_topic"] == "GPU_OOM"


def test_torch_device_oom_is_not_misfiled_as_host_ram(diagnosis):
    """`OutOfMemoryError` ends in the same letters as `MemoryError`, so matching
    the latter by substring would claim every device OOM whose message isn't one
    of the four spellings `_GPU_OOM_SIGNATURES` knows — and then advise its user
    to free system RAM on a machine whose GPU was the problem."""
    device_oom = type("OutOfMemoryError", (RuntimeError,), {})(
        "custom allocator refused an 8 GiB block"
    )

    assert diagnosis.is_host_oom(device_oom) is False
    assert diagnosis.classify(device_oom) != "HOST_MEMORY_EXHAUSTED"
    assert diagnosis.journal_classify(device_oom) != "HOST_MEMORY_EXHAUSTED"


# ── no private data escapes ─────────────────────────────────────────────────


def test_no_exception_text_is_ever_copied(diagnosis):
    """Constitution I — this surface reports topics, not messages. The reporter's
    paths and sizes must not ride along in the hint."""
    secret = "C:/Users/someone/private-voice-sample.wav"
    payload = diagnosis.stream_generation_failure(
        RuntimeError(f"{TORCH_CPU_ALLOCATOR} (while reading {secret})")
    )

    assert payload["docs_topic"] == "HOST_MEMORY_EXHAUSTED"
    for value in payload.values():
        assert secret not in str(value)
        assert "someone" not in str(value)
    # Not even the allocation size the reporter's machine printed.
    assert "2147483648" not in str(payload)


def test_classifying_an_exception_never_copies_its_text_either(diagnosis):
    """The chain walk is new surface, so it gets the same guarantee: classifying
    an EXCEPTION must not start leaking the text the message path withholds."""
    secret = "C:/Users/someone/private-clip.wav"
    payload = diagnosis.public_exception_response(
        RuntimeError(f"{TORCH_CPU_ALLOCATOR} (reading {secret})"),
        fallback="Internal error.",
    )

    assert payload.get("docs_topic") == "HOST_MEMORY_EXHAUSTED"
    assert secret not in str(payload)
    assert "someone" not in str(payload)


# ── taxonomy invariants ─────────────────────────────────────────────────────


@pytest.mark.parametrize("message", [
    TORCH_CPU_ALLOCATOR,
    WINDOWS_WINERROR_8,
    BAD_ALLOC,
    C_ALLOC_FAIL,
])
def test_the_class_classifies_on_its_own(message, diagnosis):
    assert diagnosis.classify(message) == "HOST_MEMORY_EXHAUSTED"


def test_every_context_free_class_has_a_hint_to_give(diagnosis):
    missing = {t for t in diagnosis.context_free if not diagnosis.hints.get(t)}
    assert not missing, f"allowlisted with no hint text: {sorted(missing)}"


def test_the_helper_walks_wrappers_the_way_its_device_twin_does(diagnosis):
    """Same contract as is_gpu_oom: a failure re-raised behind a wrapper is the
    same failure, and the reason string alone would miss it."""
    is_host_oom = diagnosis.is_host_oom
    try:
        try:
            raise RuntimeError(TORCH_CPU_ALLOCATOR)
        except RuntimeError as inner:
            raise RuntimeError("voice-clone prompt precompute failed") from inner
    except RuntimeError as wrapped:
        assert is_host_oom(wrapped)

    outer = RuntimeError("generation failed")
    outer.__context__ = RuntimeError(BAD_ALLOC)
    assert is_host_oom(outer)

    assert is_host_oom(MemoryError())  # the type name is unambiguous
    assert not is_host_oom(RuntimeError("model load failed"))
    assert not is_host_oom("CUDA out of memory")


def test_the_journal_names_the_class_instead_of_unknown(diagnosis):
    """A streaming failure reaches the journal, not the 500 handler. The reporter's
    report could only say `RuntimeError` because the journal filed it UNKNOWN."""
    journal = diagnosis.journal_classify

    assert journal(RuntimeError(TORCH_CPU_ALLOCATOR)) == "HOST_MEMORY_EXHAUSTED"
    # The device class is not stolen by it.
    assert journal(RuntimeError("CUDA out of memory. Tried to allocate 2.5 GiB")) == "GPU_OOM"


def test_the_journal_names_a_bare_memory_error_too(diagnosis):
    """The journal's blob is `f"{type(exc).__name__}: {exc}…"` — a bare
    `MemoryError` contributes only the type name and an empty message, so the
    signature needles could never fire and it filed as UNKNOWN. Since the class
    exists precisely for failures that name nothing, the journal has to see it."""
    assert diagnosis.journal_classify(MemoryError()) == "HOST_MEMORY_EXHAUSTED"


def test_the_two_signature_lists_cannot_drift(diagnosis):
    """The journal keeps literals so importing it never drags in the taxonomy;
    this is the guard that keeps those literals equal to the real ones."""
    journal_rules = dict(diagnosis.journal_rules)

    assert set(journal_rules["HOST_MEMORY_EXHAUSTED"]) == set(
        diagnosis.host_oom_signatures
    )


def test_the_windows_paging_file_class_keeps_its_own_specific_remedy(diagnosis):
    """WinError 1455 and WinError 8 are both "not enough memory" to a user and
    both are Windows, but only the first is fixed by growing the page file."""
    payload = diagnosis.public_exception_response(
        OSError("[WinError 1455] The paging file is too small for this operation to complete"),
        fallback="Internal error.",
    )

    assert payload.get("docs_topic") == "WINDOWS_PAGING_FILE_TOO_SMALL"


@pytest.mark.parametrize("message", NOT_HOST_OOM)
def test_nothing_else_is_misread_as_ram_exhaustion(message, diagnosis):
    """#1943's lesson, applied to the new class: a hint that confidently names
    the wrong cause sends the user somewhere useless. Disk-full, the paging
    file, timeouts and the device class all share vocabulary with RAM."""
    assert diagnosis.classify(message) != "HOST_MEMORY_EXHAUSTED"


class _LocalizedWinError8(OSError):
    """An OS error 8 whose message is localized: no English signature at all."""

    def __init__(self) -> None:
        super().__init__(8, "Nicht genügend Arbeitsspeicher verfügbar")
        self.winerror = 8


def test_windows_error_8_is_recognised_by_number_without_the_english_text(diagnosis):
    """Review (CodeRabbit): a localized or reworded OS message still carries the
    errno-style number, so the host-memory topic must not depend on English."""
    err = _LocalizedWinError8()
    assert diagnosis.is_host_oom(err)
    assert diagnosis.is_host_oom("[WinError 8] Nicht genügend Arbeitsspeicher")
    assert diagnosis.classify(err) == "HOST_MEMORY_EXHAUSTED"
    assert diagnosis.journal_classify(err) == "HOST_MEMORY_EXHAUSTED"
    # Wrapped, as the generation router re-raises it.
    try:
        try:
            raise _LocalizedWinError8()
        except OSError as inner:
            raise RuntimeError("generation failed") from inner
    except RuntimeError as wrapped:
        assert diagnosis.classify(wrapped) == "HOST_MEMORY_EXHAUSTED"


def test_other_windows_error_numbers_are_not_claimed_as_host_memory(diagnosis):
    for text in ("[WinError 80] file exists", "[WinError 87] bad parameter", "[WinError 1455] paging"):
        assert not diagnosis.is_host_oom(text), text
