"""A weight file that is present but unparseable is repairable (#1406).

Two failure shapes come out of an interrupted or mangled model download, and
only one of them was handled:

* the shard is **missing** — transformers says "does not appear to have a file
  named …", and a whole recovery ladder repairs it; and
* the shard is **present with wrong bytes** — a download that stopped
  mid-file, an antivirus that truncated it, a proxy that saved an HTML error
  page under its name. transformers opens it happily and safetensors then
  fails parsing its header-length prefix.

The second reached the user as a raw 500 — "Error while deserializing header:
header too large" — on every generation, from voice design and gallery
previews alike, with no repair attempted. It could not reach the ladder for
two independent reasons: the wording is not the missing-shard wording, and
``SafetensorError`` is a Rust-extension exception, not an ``OSError``.

It also needs the *opposite* repair. The ladder resumes a download, and a
resume trusts a blob that is already the expected size — so it would never
re-fetch the one file that is actually wrong.
"""
from __future__ import annotations

import pytest

@pytest.fixture(autouse=True)
def failure():
    """Resolved at run time: other suites reset `sys.modules` for app modules,
    and a module-level binding here could assert against a stale phrase table."""
    import core.failure as _failure

    return _failure


# ── classification ─────────────────────────────────────────────────────────

REPORTED = "Error while deserializing header: header too large"

CORRUPT_WORDINGS = [
    REPORTED,
    "SafetensorError: Error while deserializing header: HeaderTooLarge",
    "safetensors_rust.SafetensorError: MetadataIncompleteBuffer",
    "InvalidHeaderDeserialization",
    "UnpicklingError: invalid load key, '<'.",
    "RuntimeError: unexpected end of file while loading model.safetensors",
]


@pytest.mark.parametrize("text", CORRUPT_WORDINGS)
def test_corrupt_wordings_are_recognised(failure, text):
    assert failure.is_corrupt_weights_message(text)


@pytest.mark.parametrize("text", CORRUPT_WORDINGS)
def test_corrupt_wordings_classify_as_a_damaged_cache(failure, text):
    """Same taxonomy class as the missing-shard half: same cause, same remedy,
    same docs deeplink. Before the fix these classified as "" and shipped with
    no hint and no docs link."""
    assert failure.classify(text) == "MODEL_CACHE_CORRUPT"


def test_the_two_halves_stay_distinct(failure):
    """They are one class to the user and two repairs to the code — a resume
    for the missing half, a forced re-download for the damaged half. If these
    ever start matching each other's wording, the wrong repair runs."""
    missing = "repo does not appear to have a file named model.safetensors"
    assert failure.is_incomplete_cache_message(missing)
    assert not failure.is_corrupt_weights_message(missing)
    assert failure.is_corrupt_weights_message(REPORTED)
    assert not failure.is_incomplete_cache_message(REPORTED)


@pytest.mark.parametrize(
    "text",
    [
        "connection reset by peer",
        "CUDA out of memory",
        "No such file or directory",
        "",
        # Generic enough that zipfile, tarfile, gzip and a JSON parser all say
        # it — on its own it must NOT trigger a multi-GB re-download.
        "BadZipFile: unexpected end of file",
    ],
)
def test_unrelated_failures_are_not_swallowed(failure, text):
    """The load's new clause is `except Exception`, so a false positive here
    would divert an unrelated failure into a multi-GB re-download."""
    assert not failure.is_corrupt_weights_message(text)


# ── the load path ──────────────────────────────────────────────────────────

class _SafetensorError(Exception):
    """Stands in for safetensors_rust.SafetensorError — the point being that
    it is NOT an OSError, which is why the ladder never saw the real one."""


@pytest.fixture
def mm(monkeypatch):
    import services.model_manager as mm

    monkeypatch.setattr(mm, "_set_loading", lambda *a, **kw: None)
    monkeypatch.setattr(mm, "_manual_cache_delete_hint", lambda *a, **kw: "")
    monkeypatch.setattr(mm, "_repair_failure_detail", lambda *a, **kw: "")
    # Per-process guards must not leak between cases.
    monkeypatch.setattr(mm, "_FORCED_REDOWNLOAD_ATTEMPTED", set(), raising=False)
    return mm


def _drive_load(mm, monkeypatch, raise_first, repair_ok=True):
    """Run `_load_model_sync` with a checkpoint load that fails once."""
    calls = {"load": 0, "repair": []}

    def _fake_from_pretrained(*a, **kw):
        calls["load"] += 1
        if calls["load"] == 1:
            raise raise_first
        return object()

    class _FakeModelClass:
        from_pretrained = staticmethod(_fake_from_pretrained)

    def _fake_repair(checkpoint, force=False):
        calls["repair"].append(force)
        return repair_ok

    monkeypatch.setattr(mm, "_lazy_omnivoice", lambda: _FakeModelClass)
    monkeypatch.setattr(mm, "_lazy_torch", lambda: __import__("types").SimpleNamespace(float16="f16"))
    monkeypatch.setattr(mm, "get_best_device", lambda: "cpu")
    monkeypatch.setattr(mm, "resolve_omnivoice_checkpoint", lambda: "org/model")
    monkeypatch.setattr(mm, "should_preload_tts_asr", lambda: False)
    monkeypatch.setattr(mm, "_repair_model_cache", _fake_repair)
    monkeypatch.setattr(mm, "_selfheal_broken_snapshot_links", lambda *a, **kw: False)
    return calls


def test_a_corrupt_shard_is_re_downloaded_and_the_load_retried(mm, monkeypatch):
    """The reported bug. Before the fix this propagated as a raw 500."""
    calls = _drive_load(mm, monkeypatch, _SafetensorError(REPORTED))
    mm._load_model_sync()
    assert calls["load"] == 2, "the load was not retried after the repair"
    assert calls["repair"] == [True], (
        "the repair must be FORCED — a resume trusts the corrupt blob, which "
        "is already the size it expects, and would never re-fetch it"
    )


def test_the_same_shape_wrapped_in_an_oserror_is_also_repaired(mm, monkeypatch):
    """transformers wraps tensor-library failures in OSError, where the
    missing-shard check would drop it as unrecognised and re-raise."""
    calls = _drive_load(mm, monkeypatch, OSError(f"Unable to load weights: {REPORTED}"))
    mm._load_model_sync()
    assert calls["load"] == 2
    assert calls["repair"] == [True]


def test_the_cause_is_matched_through_the_exception_chain(mm, monkeypatch):
    """transformers re-raises with the tensor error as __cause__; matching only
    the outermost message would miss every wrapped case."""
    inner = _SafetensorError(REPORTED)
    outer = RuntimeError("could not load the checkpoint")
    outer.__cause__ = inner
    calls = _drive_load(mm, monkeypatch, outer)
    mm._load_model_sync()
    assert calls["load"] == 2


def test_an_unrepairable_shard_says_what_to_do(mm, monkeypatch):
    calls = _drive_load(mm, monkeypatch, _SafetensorError(REPORTED), repair_ok=False)
    with pytest.raises(RuntimeError, match="damaged"):
        mm._load_model_sync()
    assert calls["load"] == 1, "no point retrying a load whose repair failed"


def test_an_unrelated_exception_still_propagates(mm, monkeypatch):
    """The new clause is broad; this is what stops it becoming a catch-all."""
    _drive_load(mm, monkeypatch, ValueError("something else entirely"))
    with pytest.raises(ValueError, match="something else entirely"):
        mm._load_model_sync()


def test_a_second_failure_does_not_re_download_again(mm, monkeypatch):
    """One bad shard must not turn into a full re-download per generate
    request. After one forced re-fetch that did not help, say so and stop
    (CodeRabbit)."""
    calls = _drive_load(mm, monkeypatch, _SafetensorError(REPORTED))

    # First attempt: repair runs, but the reloaded weights are still bad.
    def _always_bad(*a, **kw):
        calls["load"] += 1
        raise _SafetensorError(REPORTED)

    monkeypatch.setattr(mm, "_lazy_omnivoice", lambda: type(
        "C", (), {"from_pretrained": staticmethod(_always_bad)}
    ))
    with pytest.raises(RuntimeError, match="still damaged"):
        mm._load_model_sync()
    assert calls["repair"] == [True]

    # Second attempt: no further download, straight to the manual remedy.
    with pytest.raises(RuntimeError, match="did not fix them"):
        mm._load_model_sync()
    assert calls["repair"] == [True], "the model was re-downloaded a second time"


def test_a_damaged_asr_shard_does_not_re_download_the_tts_model(mm, monkeypatch):
    """With OMNIVOICE_PRELOAD_TTS_ASR on, `_load()` also pulls the Whisper
    checkpoint — a different repo. Blaming (and re-downloading) the TTS model
    for its damage is gigabytes that fix nothing (CodeRabbit)."""
    calls = _drive_load(mm, monkeypatch, _SafetensorError(REPORTED))
    monkeypatch.setattr(mm, "should_preload_tts_asr", lambda: True)

    def _fails_only_with_asr(*a, **kw):
        calls["load"] += 1
        if kw.get("load_asr"):
            raise _SafetensorError(REPORTED)
        return object()

    monkeypatch.setattr(mm, "_lazy_omnivoice", lambda: type(
        "C", (), {"from_pretrained": staticmethod(_fails_only_with_asr)}
    ))
    with pytest.raises(RuntimeError, match="transcription model"):
        mm._load_model_sync()
    assert calls["repair"] == [], "the TTS checkpoint was re-downloaded for an ASR fault"
