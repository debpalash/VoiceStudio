"""A repository with one finished weight file is not installed while the install
of that revision is unfinished.

OmniVoice ships two weight files: the 2.4 GB model and an 0.8 GB audio
tokenizer under ``audio_tokenizer/``. The weight-presence check accepts *any*
plausible weight file, so as soon as the smaller tokenizer landed, the catalogue
reported the model ``installed`` (and the setup wizard showed it as installed)
while the main weight was still transferring. A failed install left the same
false positive. The installer now records the revision it is downloading and
clears it once the download returns with every file; only that revision's
snapshot is held back, so other revisions and existing installs are unaffected.
"""
from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

REPO = "k2-fsa/OmniVoice"
FOLDER = "models--k2-fsa--OmniVoice"
COMMIT = "a" * 40
OTHER = "b" * 40
MODEL = {"repo_id": REPO}
WEIGHT = 6 * 1024 * 1024


@pytest.fixture
def catalogue():
    return importlib.import_module("services.model_catalogue")


@pytest.fixture
def revisions():
    return importlib.import_module("services.hf_revisions")


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


def _snapshot(cache: Path, revision: str = COMMIT, *, main_weight: bool = False) -> Path:
    """A snapshot holding the finished tokenizer weight, and optionally the main one."""
    snapshot = cache / FOLDER / "snapshots" / revision
    (snapshot / "audio_tokenizer").mkdir(parents=True, exist_ok=True)
    (snapshot / "audio_tokenizer" / "model.safetensors").write_bytes(b"\0" * WEIGHT)
    (snapshot / "config.json").write_text("{}")
    if main_weight:
        (snapshot / "model.safetensors").write_bytes(b"\0" * WEIGHT)
    return snapshot


def _marker(cache: Path) -> Path:
    return cache / FOLDER / "voicestudio-installing"


def _full_plan():
    from types import SimpleNamespace

    return [
        SimpleNamespace(filename=name, file_size=size, is_cached=False, will_download=True)
        for name, size in (("config.json", 2), ("audio_tokenizer/model.safetensors", WEIGHT),
                           ("model.safetensors", WEIGHT))
    ]


def _hub(cache, *, complete: bool, dry_run_fails: bool = False):
    """A snapshot_download fake: the dry run lists the full repo; a real call
    returns the snapshot, which only holds every file when ``complete`` (a
    metadata timeout hands back the existing incomplete snapshot)."""
    def fake(**kwargs):
        if kwargs.get("dry_run"):
            if dry_run_fails:
                raise OSError("metadata timeout")
            return _full_plan()
        return str(_snapshot(cache, main_weight=complete))
    return fake


# ── catalogue check ─────────────────────────────────────────────────────────


def test_pending_install_of_this_revision_marks_snapshot_incomplete(catalogue, revisions, tmp_path):
    snapshot = _snapshot(tmp_path)
    revisions.mark_install_pending(REPO, COMMIT, str(tmp_path))

    assert not catalogue.snapshot_is_complete(MODEL, str(snapshot))


def test_pending_install_of_another_revision_keeps_this_one_installed(catalogue, revisions, tmp_path):
    snapshot = _snapshot(tmp_path, COMMIT, main_weight=True)
    revisions.mark_install_pending(REPO, OTHER, str(tmp_path))

    assert catalogue.snapshot_is_complete(MODEL, str(snapshot))


def test_snapshot_without_a_pending_install_is_complete(catalogue, tmp_path):
    assert catalogue.snapshot_is_complete(MODEL, str(_snapshot(tmp_path)))


def test_entries_with_required_files_keep_their_exact_check(catalogue, revisions, tmp_path):
    snapshot = _snapshot(tmp_path)
    revisions.mark_install_pending(REPO, COMMIT, str(tmp_path))
    model = {**MODEL, "required_files": ["audio_tokenizer/model.safetensors"]}

    assert catalogue.snapshot_is_complete(model, str(snapshot))


def test_clear_removes_only_the_marker_of_its_own_revision(revisions, tmp_path):
    revisions.mark_install_pending(REPO, OTHER, str(tmp_path))
    revisions.clear_install_pending(REPO, COMMIT, str(tmp_path))
    assert revisions.install_pending_revision(str(tmp_path / FOLDER)) == OTHER

    revisions.clear_install_pending(REPO, OTHER, str(tmp_path))
    assert revisions.install_pending_revision(str(tmp_path / FOLDER)) is None


def test_a_malformed_marker_is_ignored(revisions, tmp_path):
    (tmp_path / FOLDER).mkdir()
    (tmp_path / FOLDER / "voicestudio-installing").write_text("not-a-revision")

    assert revisions.install_pending_revision(str(tmp_path / FOLDER)) is None


# ── installer ───────────────────────────────────────────────────────────────


def _patch_install(download, monkeypatch, snapshot_download):
    from services import hf_revisions, performance_profiles
    import huggingface_hub

    monkeypatch.setattr(download, "revision_for", lambda *a, **k: COMMIT)
    monkeypatch.setattr(download, "disk_space_error", lambda *a, **k: None)
    monkeypatch.setattr(download, "_segmented_enabled", lambda *a, **k: False)
    monkeypatch.setattr(download, "_sleep_unless_cancelled", lambda *a, **k: None)
    monkeypatch.setattr(hf_revisions, "remember_revision", lambda *a, **k: None)
    monkeypatch.setattr(performance_profiles, "reconcile_active_profile", lambda *a, **k: None)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)


def _run_install(download):
    async def install_and_drain():
        await download.install_model(download.InstallModelRequest(repo_id=REPO))
        current = asyncio.current_task()
        pending = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
        if pending:
            await asyncio.gather(*pending)

    asyncio.run(install_and_drain())


def test_install_holds_the_snapshot_back_until_every_file_lands(download, catalogue, cache, monkeypatch):
    seen_mid_download = []

    def fake_snapshot_download(**kwargs):
        if kwargs.get("dry_run"):
            return _full_plan()
        snapshot = _snapshot(cache)  # the tokenizer finished first
        seen_mid_download.append(catalogue.snapshot_is_complete(MODEL, str(snapshot)))
        _snapshot(cache, main_weight=True)
        return str(snapshot)

    _patch_install(download, monkeypatch, fake_snapshot_download)
    _run_install(download)

    assert seen_mid_download == [False]
    assert not _marker(cache).exists()
    assert catalogue.snapshot_is_complete(MODEL, str(cache / FOLDER / "snapshots" / COMMIT))


def test_a_failed_install_stays_incomplete(download, catalogue, cache, monkeypatch):
    def fake_snapshot_download(**kwargs):
        if kwargs.get("dry_run"):
            return _full_plan()
        _snapshot(cache)
        raise OSError("connection reset by peer")

    _patch_install(download, monkeypatch, fake_snapshot_download)
    _run_install(download)

    assert REPO in download._install_cooldowns
    assert not catalogue.snapshot_is_complete(MODEL, str(cache / FOLDER / "snapshots" / COMMIT))


def test_a_failed_repair_keeps_a_finished_install_usable(download, catalogue, cache, monkeypatch):
    snapshot = _snapshot(cache, main_weight=True)

    def fake_snapshot_download(**kwargs):
        if kwargs.get("dry_run"):
            return _full_plan()
        raise OSError("connection reset by peer")

    _patch_install(download, monkeypatch, fake_snapshot_download)
    _run_install(download)

    assert not _marker(cache).exists()
    assert catalogue.snapshot_is_complete(MODEL, str(snapshot))


def test_a_cancelled_install_removes_its_marker(download, cache, monkeypatch):
    import threading

    def fake_snapshot_download(**kwargs):
        if kwargs.get("dry_run"):
            return _full_plan()
        _snapshot(cache)
        request = download.InstallModelRequest(repo_id=REPO)
        caller = threading.Thread(target=lambda: asyncio.run(download.cancel_install(request)))
        caller.start()
        caller.join()
        raise OSError("connection reset by peer")

    _patch_install(download, monkeypatch, fake_snapshot_download)
    _run_install(download)

    assert not _marker(cache).exists()


def test_a_tokenizer_only_leftover_is_not_taken_for_a_finished_repair(download, catalogue, cache, monkeypatch):
    """An older interrupted install holds only the tokenizer; the plan says the main
    weight is still to download, so the marker is written and a failure keeps it."""
    snapshot = _snapshot(cache)

    def fake_snapshot_download(**kwargs):
        if kwargs.get("dry_run"):
            return _full_plan()
        raise OSError("connection reset by peer")

    _patch_install(download, monkeypatch, fake_snapshot_download)
    _run_install(download)

    assert _marker(cache).exists()
    assert not catalogue.snapshot_is_complete(MODEL, str(snapshot))


def test_a_failed_preflight_never_counts_a_tokenizer_only_snapshot_as_a_repair(download, catalogue, cache, monkeypatch):
    snapshot = _snapshot(cache)

    def fake_snapshot_download(**kwargs):
        if kwargs.get("dry_run"):
            raise OSError("metadata timeout")
        raise OSError("connection reset by peer")

    _patch_install(download, monkeypatch, fake_snapshot_download)
    _run_install(download)

    assert _marker(cache).exists()
    assert not catalogue.snapshot_is_complete(MODEL, str(snapshot))


def test_a_complete_repair_writes_no_marker(download, catalogue, cache, monkeypatch):
    snapshot = _snapshot(cache, main_weight=True)
    markers = []

    def fake_snapshot_download(**kwargs):
        if kwargs.get("dry_run"):
            return _full_plan()
        markers.append(_marker(cache).exists())
        raise OSError("connection reset by peer")

    _patch_install(download, monkeypatch, fake_snapshot_download)
    _run_install(download)

    assert markers and not any(markers)
    assert not _marker(cache).exists()
    assert catalogue.snapshot_is_complete(MODEL, str(snapshot))


def test_an_unfinished_transfer_blob_is_not_a_finished_repair(download, cache):
    _snapshot(cache, main_weight=True)
    repo_dir = str(cache / FOLDER)
    blobs = cache / FOLDER / "blobs"
    blobs.mkdir()
    (blobs / "abc.incomplete").write_bytes(b"\0")
    assert not download._is_finished_repair(_full_plan(), repo_dir, COMMIT)
    (blobs / "abc.incomplete").unlink()
    assert download._is_finished_repair(_full_plan(), repo_dir, COMMIT)
    assert not download._is_finished_repair(None, repo_dir, COMMIT)


def test_install_keeps_the_marker_when_download_returns_an_incomplete_snapshot(download, catalogue, cache, monkeypatch):
    _patch_install(download, monkeypatch, _hub(cache, complete=False))
    _run_install(download)

    assert _marker(cache).exists()
    assert REPO in download._install_cooldowns
    assert not catalogue.snapshot_is_complete(MODEL, str(cache / FOLDER / "snapshots" / COMMIT))


def test_install_keeps_the_marker_when_the_file_list_is_unknown(download, cache, monkeypatch):
    _patch_install(download, monkeypatch, _hub(cache, complete=True, dry_run_fails=True))
    _run_install(download)

    assert _marker(cache).exists()  # no proof of completeness, so it stays


def test_load_time_repair_clears_the_marker(revisions, cache, monkeypatch):
    from services import hf_cache_repair, model_manager
    import huggingface_hub

    _snapshot(cache)
    revisions.mark_install_pending(REPO, COMMIT, str(cache))
    monkeypatch.setattr(revisions, "installed_revision", lambda *a, **k: COMMIT)
    monkeypatch.setattr(hf_cache_repair, "hf_cache_home", lambda: str(cache))
    monkeypatch.setattr(model_manager, "_hf_offline", lambda: False)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", _hub(cache, complete=True))

    assert model_manager._repair_model_cache(REPO) is True
    assert not _marker(cache).exists()


def test_load_time_repair_keeps_the_marker_when_nothing_was_restored(revisions, cache, monkeypatch):
    from services import hf_cache_repair, model_manager
    import huggingface_hub

    _snapshot(cache)
    revisions.mark_install_pending(REPO, COMMIT, str(cache))
    monkeypatch.setattr(revisions, "installed_revision", lambda *a, **k: COMMIT)
    monkeypatch.setattr(hf_cache_repair, "hf_cache_home", lambda: str(cache))
    monkeypatch.setattr(model_manager, "_hf_offline", lambda: False)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", _hub(cache, complete=False))

    assert model_manager._repair_model_cache(REPO) is True
    assert _marker(cache).exists()


def test_cache_self_heal_clears_the_marker(revisions, cache, monkeypatch, symlink_or_skip):
    from services import hf_cache_repair
    import huggingface_hub

    # CI exports these; offline mode makes the repair skip before it restores.
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    snapshot = _snapshot(cache)
    broken = snapshot / "model.safetensors"
    symlink_or_skip(broken, cache / "nowhere")
    revisions.mark_install_pending(REPO, COMMIT, str(cache))
    monkeypatch.setattr(revisions, "installed_revision", lambda *a, **k: COMMIT)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", _hub(cache, complete=True))

    summary = hf_cache_repair.repair_repo_cache(REPO, str(cache))
    assert summary["restored"] is True
    assert not _marker(cache).exists()


def test_cache_self_heal_keeps_the_marker_when_the_restore_returned_an_incomplete_snapshot(
        revisions, cache, monkeypatch, symlink_or_skip):
    from services import hf_cache_repair
    import huggingface_hub

    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    snapshot = _snapshot(cache)
    symlink_or_skip(snapshot / "model.safetensors", cache / "nowhere")
    revisions.mark_install_pending(REPO, COMMIT, str(cache))
    monkeypatch.setattr(revisions, "installed_revision", lambda *a, **k: COMMIT)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", _hub(cache, complete=False))

    hf_cache_repair.repair_repo_cache(REPO, str(cache))
    assert _marker(cache).exists()


def test_marker_writes_use_unique_temp_files(revisions, tmp_path, monkeypatch):
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(src)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    revisions.mark_install_pending(REPO, COMMIT, str(tmp_path))
    revisions.mark_install_pending(REPO, OTHER, str(tmp_path))
    revisions.remember_revision(REPO, COMMIT, str(tmp_path))
    assert len(set(seen)) == 3
    assert sorted(p.name for p in (tmp_path / FOLDER).iterdir()) == [
        "voicestudio-installing", "voicestudio-revision"]


def test_failed_copy_mode_recovery_keeps_the_marker(revisions, catalogue, cache, monkeypatch, symlink_or_skip):
    from services import hf_cache_repair
    import huggingface_hub

    # CI exports these; offline mode makes the repair skip before it restores.
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    snapshot = _snapshot(cache)
    broken = snapshot / "model.safetensors"
    symlink_or_skip(broken, cache / "nowhere")
    revisions.mark_install_pending(REPO, COMMIT, str(cache))
    monkeypatch.setattr(revisions, "installed_revision", lambda *a, **k: COMMIT)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", _hub(cache, complete=True))
    monkeypatch.setattr(hf_cache_repair, "find_dangling_entries", lambda d: [str(broken)])
    monkeypatch.setattr(hf_cache_repair, "_force_copy_mode", lambda root: False)

    summary = hf_cache_repair.repair_repo_cache(REPO, str(cache))
    assert summary["restored"] is True and summary["ok"] is False
    assert _marker(cache).exists()
    assert not catalogue.snapshot_is_complete(MODEL, str(snapshot))


def test_clearing_a_missing_marker_logs_no_path(revisions, tmp_path, caplog):
    import logging

    with caplog.at_level(logging.DEBUG, logger="omnivoice.hf_revisions"):
        revisions.clear_install_pending(REPO, COMMIT, str(tmp_path))
    assert str(tmp_path) not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)
