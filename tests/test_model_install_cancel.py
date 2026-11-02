"""Cancelling a model download stops the transfer and removes its partial data.

The owner pressed Cancel in the Model Catalogue and the download kept going,
leaving partial files on disk. Two causes:

* snapshot_download runs in a thread nothing could interrupt, and the cancel
  flag was only read between retry attempts, so an in-flight file always ran
  to completion;
* the segmented accelerator did honour the flag, but its ``DownloadCancelled``
  was classified as an accelerator failure and the install fell back to
  snapshot_download, which then fetched the whole model anyway.

Nothing removed ``*.incomplete`` / ``*.part`` files, new blobs or snapshot
entries afterwards. These tests mock every network call; no model downloads.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

REPO = "k2-fsa/OmniVoice"
FOLDER = "models--k2-fsa--OmniVoice"
COMMIT = "a" * 40
OLD_COMMIT = "b" * 40
CHUNKS = 200
CHUNK = 1024


@pytest.fixture
def download():
    module = importlib.import_module("api.routers.setup.download")
    module._cancelled.discard(REPO)
    yield module
    module._cancelled.discard(REPO)
    module._install_cooldowns.pop(REPO, None)
    module._install_failures.pop(REPO, None)


@pytest.fixture
def cache(monkeypatch, tmp_path):
    from huggingface_hub import constants

    root = tmp_path / "hub"
    root.mkdir()
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(root))
    return root


def _seed_previous_install(cache):
    """A complete older revision and an unrelated repository."""
    repo = cache / FOLDER
    (repo / "blobs").mkdir(parents=True)
    (repo / "blobs" / "oldblob").write_bytes(b"old weights")
    (repo / "snapshots" / OLD_COMMIT).mkdir(parents=True)
    (repo / "snapshots" / OLD_COMMIT / "model.safetensors").write_bytes(b"old weights")
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text(OLD_COMMIT)
    other = cache / "models--someone--other" / "blobs"
    other.mkdir(parents=True)
    (other / "keep").write_bytes(b"other model")
    (other / "busy.incomplete").write_bytes(b"other download")


def _patch_install(download, monkeypatch, snapshot_download=None):
    """Stub everything around the transfer so an install goes straight to it."""
    from services import hf_revisions, performance_profiles

    monkeypatch.setattr(download, "revision_for", lambda *a, **k: COMMIT)
    monkeypatch.setattr(download, "disk_space_error", lambda *a, **k: None)
    monkeypatch.setattr(download, "_segmented_enabled", lambda *a, **k: False)
    monkeypatch.setattr(hf_revisions, "remember_revision", lambda *a, **k: None)
    monkeypatch.setattr(performance_profiles, "reconcile_active_profile", lambda *a, **k: None)
    if snapshot_download is not None:
        import huggingface_hub

        monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)


def _run_install(download):
    from utils import hf_progress

    events = []
    listener = hf_progress.register_listener(events.append)

    async def install_and_drain():
        await download.install_model(download.InstallModelRequest(repo_id=REPO))
        current = asyncio.current_task()
        pending = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
        if pending:
            await asyncio.gather(*pending)

    try:
        asyncio.run(install_and_drain())
    finally:
        hf_progress.unregister_listener(listener)
    return [event["phase"] for event in events if event.get("repo_id") == REPO]


def _cancel(download):
    """Press Cancel: the real endpoint, called from its own thread as the
    renderer's request would be (the caller may sit inside an event loop)."""
    request = download.InstallModelRequest(repo_id=REPO)
    caller = threading.Thread(target=lambda: asyncio.run(download.cancel_install(request)))
    caller.start()
    caller.join()


def test_cancel_stops_an_in_flight_snapshot_download_and_cleans_up(
    download, cache, monkeypatch
):
    """Drives the real snapshot_download plumbing (thread map, per-file bars
    routed into ``tqdm_class``); only the Hub listing and the per-file HTTP
    transfer are faked."""
    from huggingface_hub import _snapshot_download, hf_api

    _seed_previous_install(cache)
    files = {"model.safetensors": "1" * 64, "vocoder.safetensors": "2" * 64}
    monkeypatch.setattr(
        hf_api.HfApi,
        "list_repo_tree",
        lambda self, **_k: [
            hf_api.RepoFile(path=name, size=CHUNKS * CHUNK, oid=etag)
            for name, etag in files.items()
        ],
    )
    written: dict[str, int] = {}
    lock = threading.Lock()

    def fake_hf_hub_download(repo_id, *, filename, tqdm_class, dry_run=False, **_kw):
        if dry_run:
            return SimpleNamespace(file_size=CHUNKS * CHUNK, is_cached=False, will_download=True)
        etag = files[filename]
        blobs = cache / FOLDER / "blobs"
        blobs.mkdir(parents=True, exist_ok=True)
        locks = cache / ".locks" / FOLDER
        locks.mkdir(parents=True, exist_ok=True)
        (locks / f"{etag}.lock").touch()
        partial = blobs / f"{etag}.incomplete"
        bar = tqdm_class(total=CHUNKS * CHUNK)
        with partial.open("wb") as handle:
            for index in range(CHUNKS):
                bar.update(CHUNK)
                handle.write(b"\0" * CHUNK)
                with lock:
                    written[filename] = written.get(filename, 0) + 1
                if filename == "model.safetensors" and index == 3:
                    _cancel(download)
                time.sleep(0.001)
        (blobs / etag).write_bytes(partial.read_bytes())
        return str(blobs / etag)

    monkeypatch.setattr(_snapshot_download, "hf_hub_download", fake_hf_hub_download)
    _patch_install(download, monkeypatch)

    phases = _run_install(download)

    assert "install_cancelled" in phases
    assert "install_done" not in phases
    # Every file stopped at its next chunk instead of running to completion.
    assert written.get("model.safetensors", 0) <= 5
    assert all(count < CHUNKS for count in written.values()), written
    # Partial data is gone; what existed before the install is kept.
    repo = cache / FOLDER
    assert sorted(p.name for p in (repo / "blobs").iterdir()) == ["oldblob"]
    assert sorted(p.name for p in (repo / "snapshots").iterdir()) == [OLD_COMMIT]
    assert (repo / "snapshots" / OLD_COMMIT / "model.safetensors").read_bytes() == b"old weights"
    assert (repo / "refs" / "main").read_text(encoding="utf-8") == OLD_COMMIT
    assert not (cache / ".locks" / FOLDER).exists()
    other = cache / "models--someone--other" / "blobs"
    assert sorted(p.name for p in other.iterdir()) == ["busy.incomplete", "keep"]
    # The job is gone and a fresh install is accepted at once.
    assert download.model_install_status()["jobs"] == []
    assert REPO not in download._cancelled


def test_segmented_cancel_does_not_fall_back_to_snapshot_download(
    download, cache, monkeypatch
):
    import huggingface_hub
    from huggingface_hub import file_download
    from services import segmented_download as segmented

    snapshot_calls: list[dict] = []

    def fake_snapshot_download(**kwargs):
        snapshot_calls.append(kwargs)
        return []

    monkeypatch.setattr(
        huggingface_hub,
        "HfApi",
        lambda **_k: SimpleNamespace(
            repo_info=lambda *_a, **_kw: SimpleNamespace(
                sha=COMMIT, siblings=[SimpleNamespace(rfilename="model.safetensors")]
            )
        ),
    )
    monkeypatch.setattr(
        file_download,
        "get_hf_file_metadata",
        lambda *_a, **_k: SimpleNamespace(etag="3" * 64, location=None, size=10),
    )

    async def fake_segmented(url, dest, *, cancel_check, **_kw):
        with open(dest + ".part", "wb") as handle:
            handle.write(b"\0" * 4)
        with open(dest + ".part.done", "w") as handle:
            handle.write("[]")
        _cancel(download)
        assert cancel_check()
        raise segmented.DownloadCancelled()

    monkeypatch.setattr(segmented, "segmented_download", fake_segmented)
    _patch_install(download, monkeypatch, fake_snapshot_download)
    monkeypatch.setattr(download, "_segmented_enabled", lambda: True)
    monkeypatch.setattr(download, "_xet_active", lambda: False)

    phases = _run_install(download)

    assert "install_cancelled" in phases
    assert [c for c in snapshot_calls if not c.get("dry_run")] == []
    assert not (cache / FOLDER).exists()


def test_cancel_before_the_transfer_starts_downloads_nothing(download, cache, monkeypatch):
    calls: list[dict] = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        if kwargs.get("dry_run"):
            # The cancel lands while the install is still resolving.
            _cancel(download)
            return []
        raise AssertionError("the transfer must not start after a cancel")

    _patch_install(download, monkeypatch, fake_snapshot_download)

    phases = _run_install(download)

    assert "install_cancelled" in phases
    assert [c for c in calls if not c.get("dry_run")] == []


def test_retry_backoff_is_interrupted_by_cancel(download):
    download._cancelled.add(REPO)
    started = time.monotonic()
    with pytest.raises(download._InstallCancelled):
        download._sleep_unless_cancelled(REPO, 30)
    assert time.monotonic() - started < 1


def test_a_failure_after_cancel_is_reported_as_the_cancel(download, cache, monkeypatch):
    """However an aborted transfer surfaces, it must not become a failure with
    a retry cooldown."""

    def fake_snapshot_download(**kwargs):
        if kwargs.get("dry_run"):
            return []
        _cancel(download)
        raise OSError("connection reset by peer")

    _patch_install(download, monkeypatch, fake_snapshot_download)

    phases = _run_install(download)

    assert "install_cancelled" in phases
    assert "install_error" not in phases
    assert REPO not in download._install_cooldowns


def test_status_reports_cancelling_until_the_worker_stops(download):
    with download._active_installs_lock:
        download._active_installs.add(REPO)
    download._cancelled.add(REPO)
    try:
        jobs = download.model_install_status()["jobs"]
    finally:
        with download._active_installs_lock:
            download._active_installs.discard(REPO)
    assert [(j["repo_id"], j["state"]) for j in jobs] == [(REPO, "cancelling")]


# ── rollback unit coverage ─────────────────────────────────────────────────


def test_rollback_removes_only_what_the_download_added(tmp_path):
    from services import hf_cache_rollback

    _seed_previous_install(tmp_path)
    repo = tmp_path / FOLDER
    (repo / "blobs" / "stale.incomplete").write_bytes(b"x")
    (repo / "voicestudio-revision").write_text(OLD_COMMIT + "\n")
    before = hf_cache_rollback.capture(str(tmp_path), [REPO])

    (repo / "blobs" / "newblob").write_bytes(b"new")
    (repo / "blobs" / "seg.part").write_bytes(b"p")
    (repo / "blobs" / "seg.part.done").write_text("[]")
    (repo / "snapshots" / COMMIT / "sub").mkdir(parents=True)
    (repo / "snapshots" / COMMIT / "sub" / "model.safetensors").write_bytes(b"new")
    (repo / "snapshots" / OLD_COMMIT / "extra.json").write_text("{}")
    (repo / "refs" / "main").write_text(COMMIT)
    (repo / "voicestudio-revision").write_text(COMMIT + "\n")

    assert hf_cache_rollback.rollback(before) == []

    assert sorted(p.name for p in (repo / "blobs").iterdir()) == ["oldblob"]
    assert sorted(p.name for p in (repo / "snapshots").iterdir()) == [OLD_COMMIT]
    assert sorted(p.name for p in (repo / "snapshots" / OLD_COMMIT).iterdir()) == [
        "model.safetensors"
    ]
    assert (repo / "refs" / "main").read_text(encoding="utf-8") == OLD_COMMIT
    assert (repo / "voicestudio-revision").read_text(encoding="utf-8") == OLD_COMMIT + "\n"
    assert (tmp_path / "models--someone--other" / "blobs" / "busy.incomplete").exists()


def test_rollback_removes_a_repository_the_download_created(tmp_path):
    from services import hf_cache_rollback

    before = hf_cache_rollback.capture(str(tmp_path), [REPO])
    (tmp_path / FOLDER / "blobs").mkdir(parents=True)
    (tmp_path / FOLDER / "blobs" / "x.incomplete").write_bytes(b"x")
    (tmp_path / ".locks" / FOLDER).mkdir(parents=True)
    (tmp_path / ".locks" / FOLDER / "x.lock").touch()

    assert hf_cache_rollback.rollback(before) == []
    assert not (tmp_path / FOLDER).exists()
    assert not (tmp_path / ".locks" / FOLDER).exists()


def _dir_link(link, target) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are not available here")


def test_rollback_keeps_existing_directory_links_and_their_targets(tmp_path):
    from services import hf_cache_rollback

    _seed_previous_install(tmp_path)
    repo = tmp_path / FOLDER
    moved = tmp_path / "other-drive" / "blobs"
    moved.mkdir(parents=True)
    (moved / "movedblob").write_bytes(b"old")
    _dir_link(repo / "moved", moved)
    before = hf_cache_rollback.capture(str(tmp_path), [REPO])

    added = tmp_path / "other-drive" / "added"
    added.mkdir()
    (added / "keep.bin").write_bytes(b"k")
    _dir_link(repo / "added", added)
    (moved / "newblob").write_bytes(b"new")

    assert hf_cache_rollback.rollback(before) == []

    assert os.path.islink(repo / "moved")
    assert sorted(p.name for p in moved.iterdir()) == ["movedblob", "newblob"]
    assert not os.path.lexists(repo / "added")
    assert (added / "keep.bin").exists()


def test_rollback_skips_a_repository_another_install_is_writing(tmp_path):
    from services import hf_cache_rollback

    before = hf_cache_rollback.capture(str(tmp_path), [REPO])
    (tmp_path / FOLDER / "blobs").mkdir(parents=True)
    hf_cache_rollback.rollback(before, skip=[REPO])
    assert (tmp_path / FOLDER / "blobs").exists()


def test_rollback_retries_files_windows_still_holds_open(tmp_path, monkeypatch):
    from services import hf_cache_rollback

    before = hf_cache_rollback.capture(str(tmp_path), [REPO])
    blobs = tmp_path / ".locks" / FOLDER
    blobs.mkdir(parents=True)
    (tmp_path / FOLDER / "blobs").mkdir(parents=True)
    held = tmp_path / FOLDER / "blobs" / "held.incomplete"
    held.write_bytes(b"x")
    real_rmtree = hf_cache_rollback.shutil.rmtree
    failures = {"left": 2}

    def flaky_rmtree(path, *args, **kwargs):
        if failures["left"]:
            failures["left"] -= 1
            raise PermissionError("[WinError 32] being used by another process")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(hf_cache_rollback.shutil, "rmtree", flaky_rmtree)
    sleeps: list[float] = []

    assert hf_cache_rollback.rollback(before, sleep=sleeps.append) == []
    assert not (tmp_path / FOLDER).exists()
    assert sleeps, "a held file is retried after a delay, not abandoned"


def test_rollback_reports_a_file_that_stays_locked(tmp_path, monkeypatch):
    from services import hf_cache_rollback

    (tmp_path / FOLDER / "blobs").mkdir(parents=True)
    before = hf_cache_rollback.capture(str(tmp_path), [REPO])
    held = tmp_path / FOLDER / "blobs" / "held.incomplete"
    held.write_bytes(b"x")

    def locked(_path):
        raise PermissionError("[WinError 32] being used by another process")

    monkeypatch.setattr(hf_cache_rollback.os, "remove", locked)
    monkeypatch.setattr(hf_cache_rollback.os, "chmod", lambda *_a: None)

    assert hf_cache_rollback.rollback(before, sleep=lambda _s: None) == [str(held)]
