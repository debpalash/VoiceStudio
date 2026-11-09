"""POST-style install of the pyannote pipeline: config-only parent, weight-bearing
dependencies, and a bearer string (never a token record) on every download call."""
import asyncio
from pathlib import Path

import pytest

PIPELINE = "pyannote/speaker-diarization-3.1"
DEPS = ["pyannote/segmentation-3.0", "pyannote/wespeaker-voxceleb-resnet34-LM"]
SECRET = "hf_pipeline-install-fixture-77"


class _FakeHub:
    """Replacement for huggingface_hub.snapshot_download that lays files out on disk."""

    def __init__(self, root: Path):
        self.root = root
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        if kw.get("dry_run"):
            return []
        folder = self.root / kw["repo_id"].replace("/", "__")
        folder.mkdir(parents=True, exist_ok=True)
        if kw["repo_id"] == PIPELINE:
            (folder / "config.yaml").write_text("pipeline: {}\n")
        else:
            (folder / "config.yaml").write_text("model: {}\n")
            (folder / "pytorch_model.bin").write_bytes(bytes(6 * 1024 * 1024))
        return str(folder)

    def real_calls(self):
        return [c for c in self.calls if not c.get("dry_run")]


@pytest.fixture
def hub(monkeypatch, tmp_path):
    import huggingface_hub
    from api.routers.setup import download
    from services import hf_revisions, performance_profiles
    from services.token_resolver import ResolvedToken

    fake = _FakeHub(tmp_path)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake)
    monkeypatch.setattr(
        "services.token_resolver.resolve",
        lambda *a, **k: ResolvedToken(token=SECRET, source="app", username="pipeline-tester"),
    )
    empty_plan = {
        "to_download_bytes": 0, "n_files": 0, "n_cached": 0,
        "total_bytes": 0, "cached_bytes": 0,
    }
    monkeypatch.setattr(download, "compute_plan", lambda *a, **k: dict(empty_plan))
    monkeypatch.setattr(download, "disk_space_error", lambda *a, **k: None)
    monkeypatch.setattr(download, "_segmented_enabled", lambda *a, **k: False)
    monkeypatch.setattr(hf_revisions, "remember_revision", lambda *a, **k: None)
    monkeypatch.setattr(performance_profiles, "reconcile_active_profile", lambda *a, **k: None)
    download._install_cooldowns.pop(PIPELINE, None)
    download._install_failures.pop(PIPELINE, None)
    yield fake
    for repo in (PIPELINE, *DEPS):
        download._install_cooldowns.pop(repo, None)
        download._install_failures.pop(repo, None)


def _install_and_collect_phases():
    from api.routers.setup import download
    from utils import hf_progress

    phases = []

    def listener(ev):
        if ev.get("repo_id") == PIPELINE:
            phases.append(ev.get("phase"))

    lid = hf_progress.register_listener(listener)

    async def go():
        await download.install_model(download.InstallModelRequest(repo_id=PIPELINE))
        task = download._install_tasks_by_repo.get(PIPELINE)
        if task is not None:
            await task

    try:
        asyncio.run(go())
    finally:
        hf_progress.unregister_listener(lid)
    return phases


def test_pipeline_install_completes_without_weights_in_parent(hub):
    phases = _install_and_collect_phases()
    assert "install_done" in phases
    assert "install_error" not in phases


def test_dependencies_download_after_parent_with_their_patterns(hub):
    _install_and_collect_phases()
    real = hub.real_calls()
    assert [c["repo_id"] for c in real] == [PIPELINE, *DEPS]
    assert "allow_patterns" not in real[0]
    for dep_call in real[1:]:
        assert dep_call["allow_patterns"] == ["config.yaml", "pytorch_model.bin"]


def test_every_download_call_carries_the_bearer_string(hub):
    _install_and_collect_phases()
    assert hub.calls, "nothing was downloaded"
    for call in hub.calls:
        assert type(call["token"]) is str
        assert call["token"] == SECRET
