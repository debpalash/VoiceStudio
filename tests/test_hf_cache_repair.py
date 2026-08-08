"""Regression tests for the broken-snapshot-link cache self-heal.

The reported first-run breaker (Windows 11): every blob of the TTS repo is
fully downloaded (multi-GB files under ``blobs/``), but the
``snapshots/<rev>/`` entries pointing at them are dangling symlinks (0 KB) —
so ``os.path.isfile()`` is False and transformers dies with "… does not appear
to have a file named pytorch_model.bin or model.safetensors" even though the
bytes are on disk. The pre-existing resume repair (#581/#739) can't fix that
state; the fix adds rung 0 to the recovery ladder: delete exactly the broken
entries, ``snapshot_download`` to restore them, retry the load once (guarded
per repo per process). These tests fail before the fix and pass after.

All network is mocked — ``snapshot_download`` never runs for real.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from core import failure
from services import hf_cache_repair


_SIGNATURE = (
    "test/checkpoint does not appear to have a file named pytorch_model.bin "
    "or model.safetensors"
)


def _symlink_or_skip(target: str, link: str) -> None:
    """Create a symlink, skipping the test where the OS forbids it (Windows
    without Developer Mode). CI's full pytest run is Linux, so coverage holds."""
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows-only
        pytest.skip("symlinks not supported in this environment")


@pytest.fixture(autouse=True)
def _no_ambient_offline_mode(monkeypatch):
    """All network in this file is mocked, but `repair_repo_cache` itself
    deliberately refuses to delete anything while HF offline mode is set
    ("don't delete what we can't restore"). An ambient HF_HUB_OFFLINE=1 —
    offline CI, air-gapped dev shell — must not silently flip these tests
    onto that skip path. `test_repair_offline_deletes_nothing` opts back in
    explicitly via monkeypatch.setenv."""
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)


def _mk_repo_cache(tmp_path, repo_id: str = "test/checkpoint"):
    """The canonical HF cache layout for ``repo_id``, with one healthy blob,
    one healthy snapshot link, one healthy regular file — and no breakage yet.

    Returns (cache_dir, repo_dir, snap_dir)."""
    repo_dir = tmp_path / ("models--" + repo_id.replace("/", "--"))
    blobs = repo_dir / "blobs"
    snap = repo_dir / "snapshots" / "abc123"
    blobs.mkdir(parents=True)
    snap.mkdir(parents=True)
    (blobs / "blob-config").write_bytes(b'{"ok": true}')
    _symlink_or_skip(os.path.join("..", "..", "blobs", "blob-config"),
                     str(snap / "config.json"))
    (snap / "tokenizer.json").write_bytes(b'{"tok": 1}')
    return tmp_path, repo_dir, snap


# ── find_dangling_entries ──────────────────────────────────────────────────


def test_healthy_cache_scan_is_a_noop(tmp_path):
    _cache, repo_dir, _snap = _mk_repo_cache(tmp_path)
    assert hf_cache_repair.find_dangling_entries(str(repo_dir)) == []


def test_missing_repo_dir_scans_empty(tmp_path):
    assert hf_cache_repair.find_dangling_entries(str(tmp_path / "nope")) == []


def test_dangling_symlink_detected(tmp_path):
    _cache, repo_dir, snap = _mk_repo_cache(tmp_path)
    _symlink_or_skip(os.path.join("..", "..", "blobs", "MISSING"),
                     str(snap / "model.safetensors"))
    broken = hf_cache_repair.find_dangling_entries(str(repo_dir))
    assert broken == [str(snap / "model.safetensors")]


def test_dangling_symlink_in_subfolder_detected(tmp_path):
    # Repos with nested paths (e.g. onnx/model.onnx) must be scanned too.
    _cache, repo_dir, snap = _mk_repo_cache(tmp_path)
    sub = snap / "onnx"
    sub.mkdir()
    _symlink_or_skip(os.path.join("..", "..", "..", "blobs", "MISSING"),
                     str(sub / "model.onnx"))
    assert hf_cache_repair.find_dangling_entries(str(repo_dir)) == [
        str(sub / "model.onnx")
    ]


def test_zero_byte_weight_and_config_files_flagged(tmp_path):
    # A 0-byte regular file standing where weights/config must be is broken —
    # those formats are never legitimately empty.
    _cache, repo_dir, snap = _mk_repo_cache(tmp_path)
    (snap / "model.safetensors").write_bytes(b"")
    (snap / "generation_config.json").write_bytes(b"")
    assert sorted(hf_cache_repair.find_dangling_entries(str(repo_dir))) == sorted([
        str(snap / "model.safetensors"),
        str(snap / "generation_config.json"),
    ])


def test_zero_byte_unknown_suffix_not_flagged(tmp_path):
    # Conservatism: when unsure, don't flag — repos legitimately ship empty
    # markers / text files.
    _cache, repo_dir, snap = _mk_repo_cache(tmp_path)
    (snap / "README.md").write_bytes(b"")
    (snap / ".gitattributes").write_bytes(b"")
    (snap / "empty.txt").write_bytes(b"")
    assert hf_cache_repair.find_dangling_entries(str(repo_dir)) == []


def test_resolving_symlink_to_zero_byte_blob_not_flagged(tmp_path):
    # An entry that RESOLVES is never touched, even if its target is empty —
    # the heal repairs broken links, it doesn't second-guess repo content.
    _cache, repo_dir, snap = _mk_repo_cache(tmp_path)
    (repo_dir / "blobs" / "blob-empty").write_bytes(b"")
    _symlink_or_skip(os.path.join("..", "..", "blobs", "blob-empty"),
                     str(snap / "vocab.json"))
    assert hf_cache_repair.find_dangling_entries(str(repo_dir)) == []


# ── repair_repo_cache ──────────────────────────────────────────────────────


def test_repair_removes_only_broken_and_redownloads(tmp_path, monkeypatch):
    import huggingface_hub

    cache, repo_dir, snap = _mk_repo_cache(tmp_path)
    _symlink_or_skip(os.path.join("..", "..", "blobs", "MISSING"),
                     str(snap / "model.safetensors"))
    calls = []
    monkeypatch.setattr(huggingface_hub, "snapshot_download",
                        lambda **k: calls.append(k))

    summary = hf_cache_repair.repair_repo_cache("test/checkpoint",
                                                cache_dir=str(cache))
    assert summary["found"] == 1
    assert summary["removed"] == 1
    assert summary["restored"] is True
    assert summary["ok"] is True
    assert summary["outcome"] == "healed_with_links"
    assert summary["error"] == ""
    # The broken entry is gone; snapshot_download was asked to restore it.
    assert not os.path.lexists(snap / "model.safetensors")
    assert calls == [{"repo_id": "test/checkpoint", "cache_dir": str(cache)}]
    # Healthy entries and blobs are untouched.
    assert (snap / "config.json").read_bytes() == b'{"ok": true}'
    assert (snap / "tokenizer.json").read_bytes() == b'{"tok": 1}'
    assert (repo_dir / "blobs" / "blob-config").exists()


def test_repair_noop_on_healthy_cache_skips_download(tmp_path, monkeypatch):
    import huggingface_hub

    cache, _repo_dir, _snap = _mk_repo_cache(tmp_path)
    called = []
    monkeypatch.setattr(huggingface_hub, "snapshot_download",
                        lambda **k: called.append(k))

    summary = hf_cache_repair.repair_repo_cache("test/checkpoint",
                                                cache_dir=str(cache))
    assert summary == {
        "repo_id": "test/checkpoint",
        "repo_dir": str(cache / "models--test--checkpoint"),
        "found": 0, "removed": 0, "restored": False,
        "outcome": "healthy", "ok": True, "error": "",
    }
    assert called == []  # healthy caches never hit the network


def test_repair_never_raises_when_download_fails(tmp_path, monkeypatch):
    import huggingface_hub

    cache, _repo_dir, snap = _mk_repo_cache(tmp_path)
    _symlink_or_skip(os.path.join("..", "..", "blobs", "MISSING"),
                     str(snap / "model.safetensors"))

    def boom(**_k):
        raise OSError("network down")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", boom)
    summary = hf_cache_repair.repair_repo_cache("test/checkpoint",
                                                cache_dir=str(cache))
    assert summary["ok"] is False
    assert summary["restored"] is False
    assert summary["outcome"] == "repair_failed"
    assert "network down" in summary["error"]


def test_restore_that_recreates_dangling_links_forces_copy_mode(
    tmp_path, monkeypatch
):
    """The reporter's root cause: hub's memoized symlink probe says links work,
    but real snapshot links come out dangling — a plain snapshot_download retry
    recreates the SAME broken links. The repair must verify after restoring,
    force copy-mode (pre-seed hub's private memo with False), delete the
    re-broken entries, and restore once more with real file copies."""
    import huggingface_hub
    import huggingface_hub.file_download as fd

    cache, _repo_dir, snap = _mk_repo_cache(tmp_path)
    entry = snap / "model.safetensors"
    _symlink_or_skip(os.path.join("..", "..", "blobs", "MISSING"), str(entry))
    monkeypatch.setattr(fd, "_are_symlinks_supported_in_dir", {}, raising=False)

    calls = []

    def fake_snapshot_download(**k):
        calls.append(k)
        if len(calls) == 1:  # first restore: the broken-probe host relinks badly
            os.symlink(os.path.join("..", "..", "blobs", "MISSING"), str(entry))
        else:                # copy-mode restore: a real file materializes
            entry.write_bytes(b"weights")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)

    summary = hf_cache_repair.repair_repo_cache("test/checkpoint",
                                                cache_dir=str(cache))
    assert summary["ok"] is True
    assert summary["outcome"] == "healed_with_copies"
    assert summary["removed"] == 2  # original dangling link + the recreated one
    assert len(calls) == 2
    assert entry.read_bytes() == b"weights"
    # Copy-mode was forced via hub's memo, keyed by the resolved cache root,
    # and stays False so every later download uses copies too.
    from pathlib import Path
    key = str(Path(str(cache)).expanduser().resolve())
    assert fd._are_symlinks_supported_in_dir[key] is False


def test_copy_mode_pass_skipped_when_private_api_changed(tmp_path, monkeypatch):
    """_force_copy_mode leans on huggingface_hub's PRIVATE memo dict; if a
    future hub changes it, the second pass must be skipped gracefully (repair
    reports failure with a clear error) rather than crash the load path."""
    import huggingface_hub
    import huggingface_hub.file_download as fd

    cache, _repo_dir, snap = _mk_repo_cache(tmp_path)
    entry = snap / "model.safetensors"
    _symlink_or_skip(os.path.join("..", "..", "blobs", "MISSING"), str(entry))
    # Simulate the private attribute changing shape in a future hub version.
    monkeypatch.setattr(fd, "_are_symlinks_supported_in_dir", None, raising=False)

    calls = []

    def fake_snapshot_download(**k):
        calls.append(k)
        os.symlink(os.path.join("..", "..", "blobs", "MISSING"), str(entry))

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)

    summary = hf_cache_repair.repair_repo_cache("test/checkpoint",
                                                cache_dir=str(cache))
    assert summary["ok"] is False
    assert summary["outcome"] == "repair_failed"
    assert "copy-mode" in summary["error"]
    assert len(calls) == 1  # no second download without forced copy-mode
    # The still-broken entry was NOT deleted a second time (nothing left to
    # restore it with) and no exception escaped.
    assert os.path.lexists(entry)


def test_repair_offline_deletes_nothing(tmp_path, monkeypatch):
    # Don't delete what we can't restore: offline mode leaves the cache as-is.
    import huggingface_hub

    cache, _repo_dir, snap = _mk_repo_cache(tmp_path)
    _symlink_or_skip(os.path.join("..", "..", "blobs", "MISSING"),
                     str(snap / "model.safetensors"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    called = []
    monkeypatch.setattr(huggingface_hub, "snapshot_download",
                        lambda **k: called.append(k))

    summary = hf_cache_repair.repair_repo_cache("test/checkpoint",
                                                cache_dir=str(cache))
    assert summary["ok"] is False
    assert summary["removed"] == 0
    assert "offline" in summary["error"].lower()
    assert os.path.lexists(snap / "model.safetensors")  # nothing deleted
    assert called == []


def test_repo_cache_dir_uses_env_cache(monkeypatch, tmp_path):
    # Mirrors the Windows short-cache redirect (core.config sets HF_HUB_CACHE),
    # read at call time so the manual-delete hint names the REAL folder.
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    assert hf_cache_repair.repo_cache_dir("org/name") == str(
        tmp_path / "models--org--name"
    )


# ── model_manager wiring ───────────────────────────────────────────────────


@pytest.fixture
def model_manager(monkeypatch):
    for mod_name in ("core.config", "services.model_manager"):
        if getattr(sys.modules.get(mod_name), "__file__", None) is None:
            sys.modules.pop(mod_name, None)

    import services.model_manager as mm

    monkeypatch.setattr(mm, "_torch", None)
    monkeypatch.setattr(mm, "_OmniVoice", None)
    monkeypatch.setattr(mm, "model", None)
    monkeypatch.setattr(mm, "_LINK_REPAIR_ATTEMPTED", set())
    monkeypatch.setenv("OMNIVOICE_MODEL", "test/checkpoint")
    monkeypatch.delenv("OMNIVOICE_PRELOAD_TTS_ASR", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.setattr(mm, "_lazy_torch", lambda: SimpleNamespace(float16="float16"))
    monkeypatch.setattr(mm, "get_best_device", lambda: "cpu")
    return mm


def _healed_summary(repo_id="test/checkpoint"):
    return {"repo_id": repo_id, "repo_dir": "/cache/models--test--checkpoint",
            "found": 2, "removed": 2, "restored": True,
            "outcome": "healed_with_links", "ok": True, "error": ""}


def test_signature_failure_link_repairs_and_retries(model_manager, monkeypatch):
    """The core fix: first load hits the missing-weights signature, the link
    self-heal repairs the snapshot, and the single retry succeeds — the legacy
    resume ladder (a real network re-download) is never entered."""
    repair_calls = []
    monkeypatch.setattr(
        "services.hf_cache_repair.repair_repo_cache",
        lambda repo_id, cache_dir=None: repair_calls.append(repo_id)
        or _healed_summary(repo_id),
    )
    monkeypatch.setattr(
        model_manager, "_repair_model_cache",
        lambda *a, **k: pytest.fail("resume repair must not run when the link heal fixed the cache"),
    )

    class FlakyOmniVoice:
        attempts = 0

        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            cls.attempts += 1
            if cls.attempts == 1:
                raise OSError(_SIGNATURE)
            return SimpleNamespace(llm=object())

    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: FlakyOmniVoice)

    loaded = model_manager._load_model_sync()
    assert loaded.llm is not None
    assert repair_calls == ["test/checkpoint"]
    assert FlakyOmniVoice.attempts == 2  # load, link-repair, reload — once


def test_link_repair_runs_at_most_once_per_repo(model_manager, monkeypatch):
    """Retry-once guard: when the cache stays broken after a 'successful'
    repair, the second load failure must NOT re-trigger the link repair —
    it falls through to the legacy ladder / actionable message instead."""
    repair_calls = []
    monkeypatch.setattr(
        "services.hf_cache_repair.repair_repo_cache",
        lambda repo_id, cache_dir=None: repair_calls.append(repo_id)
        or _healed_summary(repo_id),
    )
    monkeypatch.setattr(model_manager, "_repair_model_cache",
                        lambda *a, **k: False)

    class AlwaysBroken:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            raise OSError(_SIGNATURE)

    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: AlwaysBroken)

    with pytest.raises(RuntimeError, match="incomplete"):
        model_manager._load_model_sync()
    assert repair_calls == ["test/checkpoint"]  # ran once for this failure

    # A second load attempt in the same process: no second link repair.
    with pytest.raises(RuntimeError, match="incomplete"):
        model_manager._load_model_sync()
    assert repair_calls == ["test/checkpoint"]


def test_unrelated_load_error_does_not_trigger_repair(model_manager, monkeypatch):
    repair_calls = []
    monkeypatch.setattr(
        "services.hf_cache_repair.repair_repo_cache",
        lambda repo_id, cache_dir=None: repair_calls.append(repo_id)
        or _healed_summary(repo_id),
    )

    class DiskFull:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            raise OSError("disk full")

    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: DiskFull)

    with pytest.raises(OSError, match="disk full"):
        model_manager._load_model_sync()
    assert repair_calls == []


def test_noop_repair_falls_through_to_resume_ladder(model_manager, monkeypatch):
    """No broken links found → rung 0 must not eat the load retry budget; the
    pre-existing resume repair (#581) still runs and heals the cache."""
    monkeypatch.setattr(
        "services.hf_cache_repair.repair_repo_cache",
        lambda repo_id, cache_dir=None: {
            "repo_id": repo_id, "repo_dir": "", "found": 0, "removed": 0,
            "restored": False, "ok": True, "error": "",
        },
    )
    resume_calls = []
    monkeypatch.setattr(
        model_manager, "_repair_model_cache",
        lambda checkpoint: resume_calls.append(checkpoint) or True,
    )

    class Flaky:
        attempts = 0

        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            cls.attempts += 1
            if cls.attempts == 1:
                raise OSError(_SIGNATURE)
            return SimpleNamespace(llm=object())

    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: Flaky)

    loaded = model_manager._load_model_sync()
    assert loaded.llm is not None
    assert resume_calls == ["test/checkpoint"]
    assert Flaky.attempts == 2  # no extra load attempt for a no-op rung 0


def test_repair_failed_message_names_the_real_cache_path(
    model_manager, monkeypatch, tmp_path
):
    """When every rung fails, the surfaced error must tell the user the exact
    models--<org>--<name> folder to delete — resolved from the cache in effect
    (the Windows short cache, HF_HOME, or the default)."""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setattr(
        "services.hf_cache_repair.repair_repo_cache",
        lambda repo_id, cache_dir=None: {
            "repo_id": repo_id, "repo_dir": "", "found": 1, "removed": 0,
            "restored": False, "ok": False, "error": "PermissionError: locked",
        },
    )
    monkeypatch.setattr(model_manager, "_repair_model_cache",
                        lambda *a, **k: False)

    class AlwaysBroken:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            raise OSError(_SIGNATURE)

    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: AlwaysBroken)

    with pytest.raises(RuntimeError) as excinfo:
        model_manager._load_model_sync()
    expected = str(tmp_path / "models--test--checkpoint")
    assert expected in str(excinfo.value)
    assert "restart" in str(excinfo.value)


# ── failure classification ─────────────────────────────────────────────────


def test_classify_missing_weights_signature():
    assert failure.classify(_SIGNATURE) == "MODEL_CACHE_CORRUPT"
    evt = failure.build_failure(_SIGNATURE, stage="model-load",
                                include_diagnostic=False)
    assert evt["docs_topic"] == "MODEL_CACHE_CORRUPT"
    # The hint covers both halves of the class since #1406 — a file that is
    # missing and one that arrived damaged — so it no longer names only the
    # broken-link cause. What must survive is that it promises the automatic
    # repair and names the manual fallback.
    assert "missing or damaged" in evt["hint"]
    assert "repairs it automatically" in evt["hint"]
    assert "models--<org>--<name>" in evt["hint"]


def test_classify_repair_messages():
    # OmniVoice's own wording (status detail / logs / bug reports) must name
    # the same class as the raw transformers error.
    assert failure.classify(
        "Model cache had broken file links — repaired automatically, retrying…"
    ) == "MODEL_CACHE_CORRUPT"


def test_classify_unrelated_errors_not_cache_corrupt():
    assert failure.classify("disk full") != "MODEL_CACHE_CORRUPT"
    assert failure.classify("") != "MODEL_CACHE_CORRUPT"


# ── the second transformers wording (#1273) ────────────────────────────────
#
# transformers has two unrelated ways of saying "this snapshot has no weight
# shard". The hub-load wording is _SIGNATURE above; loading a *subfolder* of
# an already-cached snapshot raises this one instead, with no words in common.
# Matching only the first meant a half-written repo produced neither the
# automatic repair nor an actionable message — just a raw 500 (#1273, on a
# host with 10 GB free, i.e. a download that ran out of room).
_SIGNATURE_LOCAL_DIR = (
    "Error no file named model.safetensors, or pytorch_model.bin, found in "
    r"directory C:\Users\u\AppData\Local\OmniVoice\hf_cache\models--k2-fsa--"
    r"OmniVoice\snapshots\c5fdb5ccb189668d56333f77ba2629f4cd7535f4\audio_tokenizer."
)


def test_classify_local_directory_missing_weights_signature():
    assert failure.classify(_SIGNATURE_LOCAL_DIR) == "MODEL_CACHE_CORRUPT"
    evt = failure.build_failure(_SIGNATURE_LOCAL_DIR, stage="model-load",
                                include_diagnostic=False)
    assert evt["docs_topic"] == "MODEL_CACHE_CORRUPT"
    assert "repairs it automatically" in evt["hint"]


def test_self_heal_recognises_both_wordings():
    """The repair ladder keys off the same predicate as the classifier — if it
    misses a wording, the user gets a hint about an automatic repair that
    never ran."""
    from services import model_manager as mm

    assert mm._is_incomplete_cache_error(OSError(_SIGNATURE))
    assert mm._is_incomplete_cache_error(OSError(_SIGNATURE_LOCAL_DIR))


def test_incomplete_cache_predicate_does_not_overmatch():
    # "found in directory" and "no file named" are common English; both
    # fragments together are what identifies the class.
    assert not failure.is_incomplete_cache_message("no file named foo.wav")
    assert not failure.is_incomplete_cache_message("nothing found in directory /tmp")
    assert not failure.is_incomplete_cache_message("")
    assert failure.classify("no file named foo.wav") != "MODEL_CACHE_CORRUPT"
