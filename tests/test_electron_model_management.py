"""Read-only install recovery and explicit translation selection contracts."""
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_install_status_restores_progress_without_starting_work(monkeypatch):
    from api.routers.setup import download

    monkeypatch.setattr(download, "_active_installs", {"example/active"})
    monkeypatch.setattr(
        download,
        "_install_cooldowns",
        {"example/failed": time.time(), "example/old": 0},
    )
    monkeypatch.setattr(download, "_install_failures", {})
    monkeypatch.setattr(
        download.download_aggregator,
        "_get",
        lambda repo: SimpleNamespace(
            snapshot=lambda: {"bytes_done": 12, "total_bytes": 100}
        ),
    )
    jobs = download.model_install_status()["jobs"]
    assert jobs[0] == {
        "repo_id": "example/active",
        "target": "local",
        "state": "downloading",
        "bytes_done": 12,
        "total_bytes": 100,
    }
    assert jobs[1]["repo_id"] == "example/failed"
    assert jobs[1]["state"] == "failed"
    assert 1 <= jobs[1]["retry_after_seconds"] <= 60


@pytest.mark.asyncio
async def test_non_local_install_target_is_rejected():
    from api.routers.setup import download

    repo_id = download.KNOWN_MODELS[0]["repo_id"]
    with pytest.raises(HTTPException) as install_error:
        await download.install_model(
            download.InstallModelRequest(repo_id=repo_id, target="gpu2")
        )
    with pytest.raises(HTTPException) as cancel_error:
        await download.cancel_install(
            download.InstallModelRequest(repo_id=repo_id, target="gpu2")
        )

    assert install_error.value.status_code == 400
    assert cancel_error.value.status_code == 400
    assert "target must be 'local'" in str(install_error.value.detail)


@pytest.mark.parametrize("known,installed,ready,status", [
    (False, False, False, 404),
    (True, False, False, 409),
    (True, True, False, 409),
    (True, True, True, 200),
])
def test_translation_selection_requires_ready_engine(monkeypatch, known, installed, ready, status):
    from api.routers import engines
    writes = []
    monkeypatch.setattr(engines.translation_engines, "get_engine", lambda _: {"id": "test"} if known else None)
    monkeypatch.setattr(engines.translation_engines, "is_installed", lambda _: installed)
    monkeypatch.setattr(engines.translation_engines, "is_ready", lambda _: ready)
    monkeypatch.setattr(engines.prefs, "set_", lambda *args: writes.append(args))
    request = engines.TranslationSelection(engine_id="test")
    if status == 200:
        assert engines.select_translation_engine(request) == {"active": "test"}
        assert writes == [("translation_backend", "test")]
    else:
        with pytest.raises(HTTPException) as error:
            engines.select_translation_engine(request)
        assert error.value.status_code == status
        assert writes == []
