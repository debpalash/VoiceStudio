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

PIPELINE = "pyannote/speaker-diarization-3.1"
MIRROR = "https://hf-mirror.com"
CANONICAL = "https://huggingface.co"
SECRET = "hf_gatedsecret"
_WEIGHT_BYTES = 6 * 1024 * 1024


@pytest.fixture
def download():
    return importlib.import_module("api.routers.setup.download")


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


def _install(download, monkeypatch, tmp_path, fail=None):
    import huggingface_hub
    from services import hf_revisions, performance_profiles, token_resolver
    from services.token_resolver import ResolvedToken
    from utils import hf_progress

    monkeypatch.setattr(
        token_resolver,
        "resolve",
        lambda *a, **k: ResolvedToken(token=SECRET, source="app", username="tester"),
    )
    calls: list[dict] = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        if kwargs.get("dry_run"):
            return []
        if fail is not None:
            raise fail
        repo_id = kwargs["repo_id"]
        path = tmp_path / repo_id.replace("/", "__")
        path.mkdir(parents=True, exist_ok=True)
        (path / "config.yaml").write_text("pipeline: ok\n", encoding="utf-8")
        if repo_id != PIPELINE:
            (path / "pytorch_model.bin").write_bytes(b"\0" * _WEIGHT_BYTES)
        return str(path)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    monkeypatch.setattr(download, "compute_plan", lambda _plan: {
        "total_bytes": 1, "cached_bytes": 0, "to_download_bytes": 1,
        "n_files": 1, "n_cached": 0,
    })
    monkeypatch.setattr(download, "disk_space_error", lambda *_a, **_k: None)
    monkeypatch.setattr(download, "_segmented_enabled", lambda: False)
    monkeypatch.setattr(hf_revisions, "remember_revision", lambda *_a: None)
    monkeypatch.setattr(performance_profiles, "reconcile_active_profile", lambda: None)

    events: list[dict] = []
    listener_id = hf_progress.register_listener(lambda ev: events.append(ev))

    async def _run():
        await download.install_model(download.InstallModelRequest(repo_id=PIPELINE))
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)

    try:
        asyncio.run(_run())
    finally:
        hf_progress.unregister_listener(listener_id)
        download._install_cooldowns.pop(PIPELINE, None)
        download._install_failures.pop(PIPELINE, None)
    return calls, events


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


def test_gated_install_on_auto_mirror_uses_hugging_face_with_the_token(
    download, auto_mirror, monkeypatch, tmp_path
):
    calls, events = _install(download, monkeypatch, tmp_path)

    assert "install_done" in [e.get("phase") for e in events]
    assert calls
    for call in calls:
        assert call.get("endpoint") == CANONICAL, call
        assert call.get("token") == SECRET, call


def test_gated_install_on_chosen_mirror_names_the_mirror_setting(
    download, chosen_mirror, monkeypatch, tmp_path
):
    failure = RuntimeError(f"401 Client Error: Unauthorized for url: {MIRROR}/api/models/{PIPELINE}")
    calls, events = _install(download, monkeypatch, tmp_path, fail=failure)

    # The mirror never receives the token, whatever the outcome.
    assert calls and all(call.get("token") is False for call in calls)
    assert all(call.get("endpoint") == MIRROR for call in calls)
    errors = [e for e in events if e.get("phase") == "install_error"]
    assert errors, [e.get("phase") for e in events]
    assert errors[-1]["docs_topic"] == "HF_MIRROR_GATED"
    assert "Hugging Face (official)" in errors[-1]["error"]
    assert "HF_TOKEN" not in errors[-1]["error"]
    # Waiting cannot fix it, so no retry cooldown is left behind.
    assert PIPELINE not in download._install_cooldowns


def test_mirror_gated_topic_is_registered():
    from core.failure import public_hint_for_topic
    from worker import errors

    assert "Hugging Face (official)" in public_hint_for_topic("HF_MIRROR_GATED")
    assert errors._TAXONOMY["HF_MIRROR_GATED"] is errors.ErrorClass.TERMINAL
