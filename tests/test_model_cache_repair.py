"""Regression tests for #581: an incomplete/corrupt TTS model cache must
self-repair (re-fetch the missing files) instead of dead-ending the user with
a manual delete-and-reinstall instruction.

The old behavior raised a RuntimeError on the *first* truncated-cache OSError.
The fix makes `_load_model_sync` attempt an in-place `snapshot_download` repair
and retry the load once before surfacing the actionable message — so these
tests fail before the fix (no repair is attempted; load_asr default path raises
RuntimeError) and pass after.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def model_manager(monkeypatch):
    for mod_name in ("core.config", "services.model_manager"):
        if getattr(sys.modules.get(mod_name), "__file__", None) is None:
            sys.modules.pop(mod_name, None)

    import services.model_manager as mm
    from services import hf_revisions

    monkeypatch.setattr(mm, "_torch", None)
    monkeypatch.setattr(mm, "_OmniVoice", None)
    monkeypatch.setattr(mm, "model", None)
    monkeypatch.setenv("OMNIVOICE_MODEL", "test/checkpoint")
    monkeypatch.delenv("OMNIVOICE_PRELOAD_TTS_ASR", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.setattr(mm, "_lazy_torch", lambda: SimpleNamespace(float16="float16", float32="float32"))
    monkeypatch.setattr(mm, "get_best_device", lambda: "cpu")
    monkeypatch.setitem(hf_revisions.CURATED_REVISIONS, "test/checkpoint", "a" * 40)
    return mm


_TRUNCATED = OSError(
    "test/checkpoint does not appear to have a file named pytorch_model.bin "
    "or model.safetensors"
)


def test_incomplete_cache_error_detection(model_manager):
    assert model_manager._is_incomplete_cache_error(_TRUNCATED) is True
    # An unrelated OSError must NOT be classified as an incomplete cache.
    assert model_manager._is_incomplete_cache_error(OSError("disk full")) is False


def test_complete_cache_does_not_trigger_repair(model_manager, monkeypatch):
    """Fast path: a complete cache loads on the first try with no repair call."""
    repair_calls = []
    monkeypatch.setattr(
        model_manager, "_repair_model_cache",
        lambda checkpoint: repair_calls.append(checkpoint) or True,
    )

    class GoodOmniVoice:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            return SimpleNamespace(llm=object())

    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: GoodOmniVoice)

    loaded = model_manager._load_model_sync()
    assert loaded.llm is not None
    assert repair_calls == []  # repair never attempted on a healthy cache


def test_incomplete_cache_self_repairs_and_retries(model_manager, monkeypatch):
    """The core #581 fix: the first load hits a truncated cache, repair runs,
    and the retried load succeeds — no RuntimeError surfaces to the user."""
    repair_calls = []
    monkeypatch.setattr(
        model_manager, "_repair_model_cache",
        lambda checkpoint: repair_calls.append(checkpoint) or True,
    )

    class FlakyOmniVoice:
        attempts = 0

        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            cls.attempts += 1
            if cls.attempts == 1:
                raise _TRUNCATED
            return SimpleNamespace(llm=object())

    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: FlakyOmniVoice)

    loaded = model_manager._load_model_sync()
    assert loaded.llm is not None
    assert repair_calls == ["test/checkpoint"]
    assert FlakyOmniVoice.attempts == 2  # load, repair, reload


def test_repair_failure_surfaces_actionable_message(model_manager, monkeypatch):
    """If repair can't fix the cache, the user still gets the actionable
    delete-and-reinstall message (not a raw transformers OSError)."""
    monkeypatch.setattr(model_manager, "_repair_model_cache", lambda checkpoint: False)

    class BrokenOmniVoice:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            raise _TRUNCATED

    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: BrokenOmniVoice)

    with pytest.raises(RuntimeError, match="incomplete"):
        model_manager._load_model_sync()


def test_corrupt_cache_force_repairs_on_second_failure(model_manager, monkeypatch):
    """#739: resume-repair can't fix a present-but-corrupt blob (right size, wrong
    bytes), so the reload fails again — a force re-download then replaces it and
    the model loads, so the user never hits the manual delete-and-reinstall."""
    repair_calls = []

    def fake_repair(checkpoint, *, force=False):
        repair_calls.append(force)
        return True

    monkeypatch.setattr(model_manager, "_repair_model_cache", fake_repair)

    class TwiceTruncatedOmniVoice:
        attempts = 0

        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            cls.attempts += 1
            if cls.attempts <= 2:  # initial load + post-resume reload both truncated
                raise _TRUNCATED
            return SimpleNamespace(llm=object())

    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: TwiceTruncatedOmniVoice)

    loaded = model_manager._load_model_sync()
    assert loaded.llm is not None
    assert repair_calls == [False, True]  # resume first, then force re-download
    assert TwiceTruncatedOmniVoice.attempts == 3  # load, reload, force-reload


def test_force_repair_failure_still_surfaces_actionable_message(model_manager, monkeypatch):
    """If even the force re-download can't make the cache load, the user still
    gets the actionable 'could not be auto-repaired' message, not a raw OSError."""
    monkeypatch.setattr(
        model_manager, "_repair_model_cache",
        lambda checkpoint, *, force=False: True,  # repair "succeeds" but cache stays broken
    )

    class AlwaysTruncated:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            raise _TRUNCATED

    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: AlwaysTruncated)

    with pytest.raises(RuntimeError, match="could not be auto-repaired"):
        model_manager._load_model_sync()


def test_force_repair_passes_force_download(model_manager, monkeypatch):
    """force=True must set force_download (replaces corrupt blobs); the default
    resume path must NOT — re-downloading everything on a simple missing-file
    repair would be wasteful."""
    import huggingface_hub

    calls = []
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda **k: calls.append(k))

    assert model_manager._repair_model_cache("test/checkpoint", force=True) is True
    assert calls and calls[0].get("force_download") is True

    calls.clear()
    assert model_manager._repair_model_cache("test/checkpoint") is True
    assert "force_download" not in calls[0]


def test_repair_skipped_in_offline_mode(model_manager, monkeypatch):
    """Offline mode must not trigger a network re-fetch the user opted out of."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    called = []
    monkeypatch.setattr(
        "huggingface_hub.snapshot_download",
        lambda *a, **k: called.append((a, k)),
    )
    assert model_manager._repair_model_cache("test/checkpoint") is False
    assert called == []  # no download attempted offline


def test_repair_invokes_snapshot_download(model_manager, monkeypatch, tmp_path):
    """Repair re-fetches the repo via snapshot_download (resume/fill missing)."""
    calls = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        return "/cache/test/checkpoint"

    import huggingface_hub
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)

    assert model_manager._repair_model_cache("test/checkpoint") is True
    assert calls and calls[0]["repo_id"] == "test/checkpoint"
    assert calls[0]["revision"] == "a" * 40
    assert calls[0]["cache_dir"] == str(tmp_path)


def test_repair_returns_false_when_download_fails(model_manager, monkeypatch):
    """A failed re-fetch (no network, gated repo) returns False, never raises."""
    import huggingface_hub

    calls = []

    def boom(**kwargs):
        calls.append(kwargs)
        raise OSError("network down")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", boom)
    monkeypatch.setenv("OMNIVOICE_MODEL_REPAIR_BACKOFF_S", "0")  # no real sleeps
    monkeypatch.setenv("OMNIVOICE_MODEL_REPAIR_RETRIES", "3")
    assert model_manager._repair_model_cache("test/checkpoint") is False
    # #739: a transient failure must be retried, not given up on after one try.
    assert len(calls) == 3


def test_repair_retries_then_succeeds(model_manager, monkeypatch):
    """#739: a flaky connection that drops twice then completes must self-heal —
    the repair retries snapshot_download and returns True, so the user is never
    sent to a manual delete-and-reinstall for a transient blip."""
    import huggingface_hub

    attempts = {"n": 0}

    def flaky(**kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("connection reset")
        return "/cache/test/checkpoint"

    monkeypatch.setattr(huggingface_hub, "snapshot_download", flaky)
    monkeypatch.setenv("OMNIVOICE_MODEL_REPAIR_BACKOFF_S", "0")
    monkeypatch.setenv("OMNIVOICE_MODEL_REPAIR_RETRIES", "3")
    assert model_manager._repair_model_cache("test/checkpoint") is True
    assert attempts["n"] == 3


def test_repair_failover_switches_endpoint_after_network_failure(model_manager, monkeypatch, tmp_path):
    """Auto endpoint mode: a network-classified repair failure re-races the
    endpoints ONCE and the next attempt retries on the winner — a dead
    huggingface.co mid-repair heals onto the mirror instead of burning every
    retry on it. Explicit endpoints are covered by test_endpoint_race."""
    import huggingface_hub
    import services.endpoint_race as er
    from core import prefs

    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.setattr(er, "_FAILOVER_ATTEMPTED", set())
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.delenv("OMNIVOICE_HF_ENDPOINT_MODE", raising=False)
    # The re-race finds canonical dead and the mirror alive.
    monkeypatch.setattr(
        er, "probe_endpoint",
        lambda endpoint, timeout=None: er.ProbeResult(
            endpoint=endpoint,
            reachable=endpoint == er.COMMUNITY_MIRROR,
            latency_ms=90.0 if endpoint == er.COMMUNITY_MIRROR else None,
        ),
    )

    calls = []

    def flaky(**kwargs):
        calls.append(kwargs)
        if kwargs.get("endpoint") != er.COMMUNITY_MIRROR:
            raise OSError("connection reset by peer")  # network-classified
        return "/cache/test/checkpoint"

    monkeypatch.setattr(huggingface_hub, "snapshot_download", flaky)
    monkeypatch.setenv("OMNIVOICE_MODEL_REPAIR_BACKOFF_S", "0")
    monkeypatch.setenv("OMNIVOICE_MODEL_REPAIR_RETRIES", "3")
    assert model_manager._repair_model_cache("test/checkpoint") is True
    # Attempt 1: canonical (no endpoint kwarg) fails → failover; attempt 2
    # carries the mirror endpoint and succeeds.
    assert "endpoint" not in calls[0]
    assert calls[1]["endpoint"] == er.COMMUNITY_MIRROR
    assert len(calls) == 2


def test_repair_failover_skipped_for_non_network_failures(model_manager, monkeypatch, tmp_path):
    """A disk-full/gated-repo repair failure must not trigger endpoint probes."""
    import huggingface_hub
    import services.endpoint_race as er
    from core import prefs

    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.setattr(er, "_FAILOVER_ATTEMPTED", set())
    monkeypatch.delenv("HF_ENDPOINT", raising=False)

    def boom_probe(endpoint, timeout=None):
        raise AssertionError("non-network failure must not re-race endpoints")

    monkeypatch.setattr(er, "probe_endpoint", boom_probe)
    monkeypatch.setattr(
        huggingface_hub, "snapshot_download",
        lambda **k: (_ for _ in ()).throw(OSError("No space left on device")),
    )
    monkeypatch.setenv("OMNIVOICE_MODEL_REPAIR_BACKOFF_S", "0")
    monkeypatch.setenv("OMNIVOICE_MODEL_REPAIR_RETRIES", "2")
    assert model_manager._repair_model_cache("test/checkpoint") is False


def test_repair_retries_are_env_tunable(model_manager, monkeypatch):
    """A restricted network can lower/raise the attempt count; a single attempt
    must still work (no off-by-one that skips the only try)."""
    import huggingface_hub

    calls = []
    monkeypatch.setattr(
        huggingface_hub, "snapshot_download",
        lambda **k: calls.append(k) or (_ for _ in ()).throw(OSError("down")),
    )
    monkeypatch.setenv("OMNIVOICE_MODEL_REPAIR_BACKOFF_S", "0")
    monkeypatch.setenv("OMNIVOICE_MODEL_REPAIR_RETRIES", "1")
    assert model_manager._repair_model_cache("test/checkpoint") is False
    assert len(calls) == 1
