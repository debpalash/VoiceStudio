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
            return []
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
            return []
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
            return []
        raise OSError("connection reset by peer")

    _patch_install(download, monkeypatch, fake_snapshot_download)
    _run_install(download)

    assert not _marker(cache).exists()
    assert catalogue.snapshot_is_complete(MODEL, str(snapshot))


def test_a_cancelled_install_removes_its_marker(download, cache, monkeypatch):
    import threading

    def fake_snapshot_download(**kwargs):
        if kwargs.get("dry_run"):
            return []
        _snapshot(cache)
        request = download.InstallModelRequest(repo_id=REPO)
        caller = threading.Thread(target=lambda: asyncio.run(download.cancel_install(request)))
        caller.start()
        caller.join()
        raise OSError("connection reset by peer")

    _patch_install(download, monkeypatch, fake_snapshot_download)
    _run_install(download)

    assert not _marker(cache).exists()
