"""An install must not bypass a rejected digest through its plain fallback."""
import asyncio
import hashlib
import importlib
import re
from types import SimpleNamespace

import httpx
import pytest

from test_download_blob_integrity import CONTENT, GIT_BLOB_OID


@pytest.fixture
def install_origin(monkeypatch, tmp_path):
    import huggingface_hub
    from huggingface_hub import constants, file_download
    from services import performance_profiles, segmented_download as sd
    from services import token_resolver
    from utils import hf_progress

    download = importlib.import_module("api.routers.setup.download")
    repo = "integrity/fixture"
    revision = "c" * 40
    cache = tmp_path / "hub"
    repo_dir = cache / "models--integrity--fixture"
    snapshot = repo_dir / "snapshots" / revision
    state = SimpleNamespace(
        download=download, repo=repo, revision=revision, repo_dir=repo_dir,
        snapshot=snapshot, good=False, unsupported=False, etag="", gets=[],
        fallback_calls=[], events=[],
    )
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(cache))
    monkeypatch.setattr(download, "KNOWN_MODELS", [{
        "repo_id": repo, "config_only": True, "config_required_files": ["config.json"],
    }])
    monkeypatch.setattr(download, "revision_for", lambda _repo: revision)
    monkeypatch.setattr(download, "_download_endpoint", lambda **_kw: None)
    monkeypatch.setattr(download, "_segmented_enabled", lambda: True)
    monkeypatch.setattr(download, "_xet_active", lambda: False)
    monkeypatch.setattr(download, "disk_space_error", lambda _size: None)
    monkeypatch.setattr(download, "invalidate_cache", lambda: None)
    monkeypatch.setattr(download, "_active_installs", set())
    monkeypatch.setattr(download, "_install_cooldowns", {})
    monkeypatch.setattr(download, "_install_failures", {})
    monkeypatch.setattr(download, "_cancelled", set())
    monkeypatch.setattr(download, "_install_tasks", set())
    monkeypatch.setattr(download, "_install_tasks_by_repo", {})
    monkeypatch.setattr(performance_profiles, "reconcile_active_profile", lambda: {})
    monkeypatch.setattr(token_resolver, "resolve", lambda: None)

    class Hub:
        def __init__(self, **_kwargs):
            pass

        def repo_info(self, *_args, **_kwargs):
            return SimpleNamespace(sha=revision, siblings=[SimpleNamespace(rfilename="config.json")])

    def metadata(*_args, **_kwargs):
        return SimpleNamespace(etag=state.etag, size=len(CONTENT),
                               location="https://download.example/config.json")

    def plain_snapshot(**kwargs):
        if kwargs.get("dry_run"):
            return []
        state.fallback_calls.append(kwargs)
        snapshot.mkdir(parents=True, exist_ok=True)
        (snapshot / "config.json").write_bytes(CONTENT if state.good else b"!" + CONTENT[1:])
        return str(snapshot)

    def response(request):
        if request.method == "HEAD":
            return httpx.Response(200, headers={
                "Content-Length": str(len(CONTENT)), "Accept-Ranges": "bytes",
            })
        bounds = tuple(map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", request.headers["Range"]).groups()))
        state.gets.append(bounds)
        if state.unsupported:
            return httpx.Response(200, content=CONTENT)  # ignores the range
        body = CONTENT if state.good else b"!" + CONTENT[1:]
        lo, hi = bounds
        return httpx.Response(206, headers={
            "Content-Range": f"bytes {lo}-{hi}/{len(body)}",
        }, content=body[lo:hi + 1])

    transfer = sd.segmented_download

    async def download_with_transport(*args, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(response), trust_env=False) as client:
            return await transfer(*args, **kwargs, client=client, num_connections=1)

    monkeypatch.setattr(huggingface_hub, "HfApi", Hub)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", plain_snapshot)
    monkeypatch.setattr(file_download, "get_hf_file_metadata", metadata)
    monkeypatch.setattr(sd, "segmented_download", download_with_transport)
    monkeypatch.setattr(sd, "_MIN_SEGMENT_BYTES", 16)
    monkeypatch.setattr(sd, "_MAX_SEGMENT_BYTES", 16)
    listener = hf_progress.register_listener(state.events.append)
    try:
        yield state
    finally:
        hf_progress.unregister_listener(listener)


def _install(state):
    async def run():
        result = await state.download.install_model(
            state.download.InstallModelRequest(repo_id=state.repo),
        )
        assert result["status"] == "install_started"
        await state.download._install_tasks_by_repo[state.repo]

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["git-blob", "sha256"])
def test_install_rejects_corrupt_digest_without_plain_fallback_and_retry_heals(
    install_origin, monkeypatch, kind,
):
    state = install_origin
    state.etag = GIT_BLOB_OID if kind == "git-blob" else hashlib.sha256(CONTENT).hexdigest()
    _install(state)
    assert state.gets  # real segmentation, byte assembly and hashing were used
    assert state.fallback_calls == []
    assert not (state.repo_dir / "voicestudio-revision").exists()
    assert not (state.repo_dir / "refs" / "main").exists()
    assert not (state.snapshot / "config.json").exists()
    phases = [event["phase"] for event in state.events]
    assert "install_error" in phases and "install_done" not in phases
    failure = state.download._install_failures[state.repo]
    assert "mismatch" in failure["error"] and "corrupt" in failure["error"]
    failed_job, = state.download.model_install_status()["jobs"]
    assert failed_job["state"] == "failed" and failed_job["error"] == failure["error"]

    # Elapse the existing retry cooldown without a real one-minute sleep.
    monkeypatch.setattr(state.download, "_COOLDOWN_SECS", 0)
    state.good = True
    state.gets.clear()
    state.events.clear()
    _install(state)
    assert sorted(state.gets) == [(0, 15), (16, 31), (32, 47), (48, 49)]
    assert state.fallback_calls == []
    assert (state.snapshot / "config.json").read_bytes() == CONTENT
    assert (state.repo_dir / "voicestudio-revision").read_text(encoding="utf-8").strip() == state.revision
    assert state.repo not in state.download._install_failures
    assert "install_done" in [event["phase"] for event in state.events]


def test_install_keeps_plain_fallback_for_unsupported_ranges(install_origin):
    state = install_origin
    state.etag = hashlib.sha256(CONTENT).hexdigest()
    state.good = True
    state.unsupported = True
    _install(state)
    assert len(state.fallback_calls) == 1
    assert (state.snapshot / "config.json").read_bytes() == CONTENT
    assert (state.repo_dir / "voicestudio-revision").read_text(encoding="utf-8").strip() == state.revision
    assert "install_done" in [event["phase"] for event in state.events]
