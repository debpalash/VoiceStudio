"""Pre-install disk-space guard (Model Store "Install all" overrun).

`POST /models/install` computes an exact ``to_download_bytes`` in the FDL-05
preflight plan; before this guard it never compared that against free space, so
an "Install all" could fill the disk with no warning and fail mid-download with
a cryptic OSError. ``disk_space_error`` is the single decision point — it names
the three numbers a user needs (needs X, headroom Y, have Z) and returns None
when it fits / the size is unknown / the volume can't be probed. Platform-
agnostic: ``shutil.disk_usage`` works identically on macOS/Windows/Linux.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import importlib

import pytest


@pytest.fixture
def models_mod():
    return importlib.import_module("api.routers.setup.models")


_GIB = 1024 ** 3


def test_rejects_when_download_exceeds_free_space(models_mod, monkeypatch):
    # 50 GB to download, only 20 GB free → rejected, and the message must name
    # both the download size and the free size so the user can act.
    monkeypatch.setattr(models_mod, "disk_free_bytes", lambda *a, **k: 20 * _GIB)
    msg = models_mod.disk_space_error(50 * _GIB, cache_dir="/fake/cache")
    assert msg is not None
    assert "50.0 GB" in msg          # needs X
    assert "20.0 GB" in msg          # have Z
    assert f"{models_mod.MIN_FREE_GB} GB" in msg  # headroom named
    assert "/fake/cache" in msg      # where


def test_rejects_when_download_plus_headroom_exceeds_free(models_mod, monkeypatch):
    # Fits the raw bytes but NOT the MIN_FREE_GB headroom on top → still rejected,
    # so "Install all" can't fill the disk to the brim.
    monkeypatch.setattr(models_mod, "disk_free_bytes", lambda *a, **k: 12 * _GIB)
    msg = models_mod.disk_space_error(5 * _GIB)  # 5 + 10 headroom = 15 > 12 free
    assert msg is not None
    assert "5.0 GB" in msg


def test_allows_when_enough_space(models_mod, monkeypatch):
    # 5 GB download + 10 GB headroom = 15 GB required, 100 GB free → proceeds.
    monkeypatch.setattr(models_mod, "disk_free_bytes", lambda *a, **k: 100 * _GIB)
    assert models_mod.disk_space_error(5 * _GIB) is None


def test_does_not_block_on_unknown_size(models_mod, monkeypatch):
    # Older/gated repo or a mirror without dry-run → no plan → never block.
    monkeypatch.setattr(models_mod, "disk_free_bytes", lambda *a, **k: 1 * _GIB)
    assert models_mod.disk_space_error(None) is None
    assert models_mod.disk_space_error(0) is None


def test_does_not_block_when_volume_unprobeable(models_mod, monkeypatch):
    # disk_free_bytes returns 0 on any probe failure → don't block on missing info.
    monkeypatch.setattr(models_mod, "disk_free_bytes", lambda *a, **k: 0)
    assert models_mod.disk_space_error(999 * _GIB) is None


def test_install_worker_emits_install_error_and_skips_download(models_mod, monkeypatch):
    """Wiring guard: when the plan won't fit, the install worker emits an
    ``install_error`` SSE event (carrying the sizes) and never calls
    snapshot_download for real — the reject happens BEFORE any byte flows."""
    download = importlib.import_module("api.routers.setup.download")
    from utils import hf_progress

    # Force a known, over-budget plan and a rejection.
    monkeypatch.setattr(
        download, "compute_plan",
        lambda plan_files: {
            "total_bytes": 50 * _GIB, "cached_bytes": 0,
            "to_download_bytes": 50 * _GIB, "n_files": 3, "n_cached": 0,
        },
    )
    monkeypatch.setattr(download, "disk_space_error", lambda *a, **k: "Not enough disk space to install: needs 50.0 GB")

    events: list[dict] = []
    lid = hf_progress.register_listener(lambda ev: events.append(ev))

    # snapshot_download: the dry-run preflight returns a dummy; a REAL download
    # call must never happen (the guard returns first). Fail loudly if it does.
    import huggingface_hub

    calls = []

    def _fake_snapshot(**kwargs):
        calls.append(kwargs)
        if kwargs.get("dry_run"):
            return []  # preflight plan input (compute_plan is stubbed anyway)
        raise AssertionError("snapshot_download called for a real download despite disk-full reject")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", _fake_snapshot)

    repo_id = download.KNOWN_MODELS[0]["repo_id"]
    import asyncio

    async def _run():
        await download.install_model(download.InstallModelRequest(repo_id=repo_id))
        # install_model schedules the worker via loop.create_task(to_thread(_do));
        # drain the pending task so the worker runs to completion in-loop.
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)

    try:
        asyncio.run(_run())
    finally:
        hf_progress.unregister_listener(lid)

    errs = [e for e in events if e.get("phase") == "install_error"]
    assert errs, f"expected an install_error event, got phases: {[e.get('phase') for e in events]}"
    assert "disk space" in errs[0]["error"].lower()
    from services.hf_revisions import revision_for
    assert calls[0]["revision"] == revision_for(repo_id)
    # And the resolving heartbeat must not have leaked — no infinite 'resolving'
    # stream after the bail (the worker set the stop event before returning).


def test_install_preflight_download_and_repair_marker_share_revision(models_mod, monkeypatch):
    download = importlib.import_module("api.routers.setup.download")
    import asyncio
    import huggingface_hub
    from services import hf_revisions

    repo_id = download.KNOWN_MODELS[0]["repo_id"]
    expected = hf_revisions.revision_for(repo_id)
    calls = []
    remembered = []
    reconciled = []

    def fake_snapshot(**kwargs):
        calls.append(kwargs)
        return [] if kwargs.get("dry_run") else "/cache/snapshots/" + expected

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)
    monkeypatch.setattr(download, "compute_plan", lambda _plan: {
        "total_bytes": 1, "cached_bytes": 0, "to_download_bytes": 1,
        "n_files": 1, "n_cached": 0,
    })
    monkeypatch.setattr(download, "disk_space_error", lambda *_a, **_k: None)
    monkeypatch.setattr(download, "_segmented_enabled", lambda: False)
    monkeypatch.setattr(download, "_validate_snapshot_has_weights", lambda *_a: None)
    monkeypatch.setattr(hf_revisions, "remember_revision", lambda *args: remembered.append(args))
    from services import performance_profiles
    monkeypatch.setattr(
        performance_profiles,
        "reconcile_active_profile",
        lambda: reconciled.append(True) or {},
    )

    async def run_install():
        await download.install_model(download.InstallModelRequest(repo_id=repo_id))
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)

    asyncio.run(run_install())

    assert [call["revision"] for call in calls] == [expected, expected]
    assert calls[0]["dry_run"] is True
    assert "dry_run" not in calls[1]
    assert remembered and remembered[0][0:2] == (repo_id, expected)
    assert reconciled == [True]


def test_install_honors_catalog_allow_patterns(models_mod, monkeypatch, tmp_path):
    """A multi-package repo downloads only the model variant shown in its row."""
    download = importlib.import_module("api.routers.setup.download")
    import asyncio
    import huggingface_hub
    from services import hf_revisions

    repo_id = "audio-cpp/audio.cpp-gguf"
    spec = next(m for m in download.KNOWN_MODELS if m["repo_id"] == repo_id)
    expected_patterns = spec["allow_patterns"]
    calls = []

    def fake_snapshot(**kwargs):
        calls.append(kwargs)
        return [] if kwargs.get("dry_run") else str(tmp_path)

    def segmented_must_not_run(*_args, **_kwargs):
        raise AssertionError("filtered model installs must skip whole-repo segmented download")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)
    monkeypatch.setattr(download, "compute_plan", lambda _plan: {
        "total_bytes": 1, "cached_bytes": 0, "to_download_bytes": 1,
        "n_files": 1, "n_cached": 0,
    })
    monkeypatch.setattr(download, "disk_space_error", lambda *_a, **_k: None)
    monkeypatch.setattr(download, "_segmented_enabled", lambda: True)
    monkeypatch.setattr(download, "_xet_active", lambda: False)
    monkeypatch.setattr(download, "_segmented_snapshot", segmented_must_not_run)
    monkeypatch.setattr(download, "_validate_snapshot_has_weights", lambda *_a: None)
    monkeypatch.setattr(hf_revisions, "remember_revision", lambda *_args: None)

    async def run_install():
        await download.install_model(download.InstallModelRequest(repo_id=repo_id))
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)

    asyncio.run(run_install())

    assert len(calls) == 2
    assert all(call["allow_patterns"] == expected_patterns for call in calls)
    assert calls[0]["dry_run"] is True
    assert "dry_run" not in calls[1]


def test_catalogue_reports_the_headroom_the_install_guard_enforces(models_mod, monkeypatch):
    # The UI warns with the same rule as disk_space_error and names the
    # headroom, instead of "needs 4.8 GB but only 10.2 GB is free" (#2597).
    monkeypatch.setattr(models_mod, "_target_repo_inventory", lambda: None)
    models_mod.invalidate_cache()
    try:
        out = models_mod.list_models()
    finally:
        models_mod.invalidate_cache()
    assert out["disk_headroom_gb"] == models_mod.MIN_FREE_GB
