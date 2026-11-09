"""Gated catalogue installs and Hugging Face mirrors.

The token only ever goes to Hugging Face, so a mirror cannot serve a gated
repository. An automatically picked mirror must therefore never be used for
one, and a mirror the user chose must fail with its own recovery topic instead
of the misleading "set HF_TOKEN / accept the terms" guidance.
"""
import asyncio
import importlib
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

GATED_PIPELINE = "pyannote/speaker-diarization-3.1"
APP_TOKEN = "hf_test_gated_install_4821"
MODEL_BYTES = 6 * 1024 * 1024
MIRROR = "https://hf-mirror.com"
CANONICAL = "https://huggingface.co"


@pytest.fixture
def download():
    return importlib.import_module("api.routers.setup.download")


def _run_install(download, monkeypatch, tmp_path, fail=None):
    import huggingface_hub
    from huggingface_hub import constants as hub_constants
    from services import hf_revisions, performance_profiles, token_resolver
    from services.token_resolver import ResolvedToken
    from utils import hf_progress

    calls = []
    events = []
    isolated_cache = tmp_path / "hub-cache"
    isolated_cache.mkdir()
    monkeypatch.setattr(hub_constants, "HF_HUB_CACHE", str(isolated_cache))
    monkeypatch.setattr(
        token_resolver,
        "resolve",
        lambda: ResolvedToken(token=APP_TOKEN, source="app", username="clean-room-user"),
    )

    def snapshot(**kwargs):
        calls.append(kwargs)
        if kwargs.get("dry_run"):
            return []
        if fail is not None:
            raise fail
        folder = tmp_path / kwargs["repo_id"].replace("/", "--")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "config.yaml").write_text("model: fixture\n", encoding="utf-8")
        if kwargs["repo_id"] != GATED_PIPELINE:
            (folder / "pytorch_model.bin").write_bytes(bytes(MODEL_BYTES))
        return str(folder)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    monkeypatch.setattr(
        download,
        "compute_plan",
        lambda _files: {
            "total_bytes": 1,
            "cached_bytes": 0,
            "to_download_bytes": 1,
            "n_files": 1,
            "n_cached": 0,
        },
    )
    monkeypatch.setattr(download, "disk_space_error", lambda _size: None)
    monkeypatch.setattr(download, "_segmented_enabled", lambda: False)

    async def run_inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(download.asyncio, "to_thread", run_inline)
    monkeypatch.setattr(hf_revisions, "remember_revision", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        performance_profiles,
        "reconcile_active_profile",
        lambda: None,
    )
    listener = hf_progress.register_listener(events.append)

    async def install():
        await download.install_model(download.InstallModelRequest(repo_id=GATED_PIPELINE))
        task = download._install_tasks_by_repo.get(GATED_PIPELINE)
        if task is not None:
            await task

    try:
        asyncio.run(install())
    finally:
        hf_progress.unregister_listener(listener)
        cooldown_was_set = GATED_PIPELINE in download._install_cooldowns
        download._install_cooldowns.pop(GATED_PIPELINE, None)
        download._install_failures.pop(GATED_PIPELINE, None)
    return calls, events, cooldown_was_set


@pytest.fixture
def auto_mirror(monkeypatch):
    """Auto mode whose cached decision picked the community mirror."""
    from services import endpoint_race

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.setattr(endpoint_race, "explicit_endpoint", lambda: "")
    monkeypatch.setattr(endpoint_race, "mode", lambda: "auto")
    monkeypatch.setattr(
        endpoint_race, "cached_decision", lambda: {"endpoint": MIRROR, "reachable": True}
    )
    return endpoint_race


@pytest.fixture
def chosen_mirror(monkeypatch):
    from services import endpoint_race

    monkeypatch.setenv("HF_ENDPOINT", MIRROR)
    return endpoint_race


def test_auto_picked_mirror_serves_only_public_repositories(auto_mirror):
    assert auto_mirror.download_endpoint() == MIRROR
    assert auto_mirror.download_endpoint(gated=True) == CANONICAL


def test_chosen_mirror_is_kept_for_gated_repositories(chosen_mirror):
    assert chosen_mirror.download_endpoint(gated=True) == MIRROR
    assert chosen_mirror.explicit_mirror() == MIRROR


def test_chosen_official_endpoint_is_not_a_mirror(monkeypatch):
    from services import endpoint_race

    monkeypatch.setenv("HF_ENDPOINT", CANONICAL)
    assert endpoint_race.explicit_mirror() == ""
    assert endpoint_race.download_endpoint(gated=True) == CANONICAL


def test_mirror_gated_topic_is_registered():
    from core.failure import public_hint_for_topic

    assert "Hugging Face (official)" in public_hint_for_topic("HF_MIRROR_GATED")


def test_auto_mirror_routes_gated_install_to_official_hub(
    auto_mirror, download, monkeypatch, tmp_path
):
    calls, events, _cooldown_was_set = _run_install(download, monkeypatch, tmp_path)

    assert any(event.get("phase") == "install_done" for event in events)
    assert calls
    assert all(call.get("endpoint") == CANONICAL for call in calls)
    assert all(call.get("token") == APP_TOKEN for call in calls)


def test_explicit_mirror_reports_gated_recovery_without_token(
    chosen_mirror, download, monkeypatch, tmp_path
):
    failure = RuntimeError(
        f"401 Unauthorized while requesting {MIRROR}/{GATED_PIPELINE}"
    )
    calls, events, cooldown_was_set = _run_install(
        download, monkeypatch, tmp_path, fail=failure
    )

    assert calls
    assert all(call.get("endpoint") == MIRROR for call in calls)
    assert all(call.get("token") is False for call in calls)
    errors = [event for event in events if event.get("phase") == "install_error"]
    assert errors[-1]["docs_topic"] == "HF_MIRROR_GATED"
    assert "Hugging Face (official)" in errors[-1]["error"]
    assert "HF_TOKEN" not in errors[-1]["error"]
    assert cooldown_was_set is False
