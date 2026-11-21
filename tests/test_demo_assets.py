"""On-demand dubbing demo videos: integrity, consent, offline and serving.

No test here touches the network: downloads run through an
``httpx.MockTransport`` that counts every request, which is what makes "no
request without the user's POST" an assertable outcome.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from services import demo_assets

_GOOD = b"good-video-bytes" * 64


def _pin(data: bytes) -> dict:
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "kind": "video"}


class Server:
    """Serves {name: bytes} from memory and records every request."""

    def __init__(self, files: dict[str, bytes], status: int = 200):
        self.files = files
        self.status = status
        self.requests: list[str] = []

    def client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(str(request.url))
            name = request.url.path.rsplit("/", 1)[-1]
            if self.status != 200 or name not in self.files:
                return httpx.Response(self.status if self.status != 200 else 404)
            return httpx.Response(200, content=self.files[name])

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_assets, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(demo_assets, "PINNED", {"a.mp4": _pin(_GOOD), "b.mp4": _pin(_GOOD + b"x")})
    monkeypatch.setattr(demo_assets, "_state", {"state": "idle", "downloaded_bytes": 0, "error": None})
    monkeypatch.setattr(demo_assets, "_task", None)
    monkeypatch.delenv("OMNIVOICE_DEMO_ASSETS_URL", raising=False)
    return tmp_path


def _cached(env: Path) -> list[str]:
    d = demo_assets.cache_dir()
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def test_download_verifies_and_caches_every_file(env):
    server = Server({"a.mp4": _GOOD, "b.mp4": _GOOD + b"x"})
    result = asyncio.run(demo_assets.download_all(server.client()))
    assert result["state"] == "ready"
    assert _cached(env) == ["a.mp4", "b.mp4"]
    assert demo_assets.cache_dir() == env / "demo_cache" / "dubbing-v1"
    assert all(u.startswith(demo_assets.base_url() + "/") for u in server.requests)
    assert demo_assets.status()["files"] == {"a.mp4": True, "b.mp4": True}


def test_hash_mismatch_is_rejected_and_never_cached(env):
    tampered = b"X" + _GOOD[1:]  # same size, different bytes
    server = Server({"a.mp4": tampered, "b.mp4": _GOOD + b"x"})
    result = asyncio.run(demo_assets.download_all(server.client()))
    assert result["state"] == "error"
    assert "sha256" in result["error"]
    assert "a.mp4" not in _cached(env)
    assert not [n for n in _cached(env) if n.endswith(".part")]
    assert demo_assets.cached_path("a.mp4") is None


def test_truncated_download_is_not_cached(env):
    server = Server({"a.mp4": _GOOD[:-10], "b.mp4": _GOOD + b"x"})
    result = asyncio.run(demo_assets.download_all(server.client()))
    assert result["state"] == "error"
    assert "truncated" in result["error"]
    assert _cached(env) == []


def test_oversized_download_is_cut_off_at_the_pinned_size(env):
    server = Server({"a.mp4": _GOOD + b"extra", "b.mp4": _GOOD + b"x"})
    result = asyncio.run(demo_assets.download_all(server.client()))
    assert result["state"] == "error"
    assert "size cap" in result["error"]
    assert _cached(env) == []


def test_http_error_reports_and_retry_succeeds(env):
    bad = Server({}, status=503)
    assert asyncio.run(demo_assets.download_all(bad.client()))["state"] == "error"
    good = Server({"a.mp4": _GOOD, "b.mp4": _GOOD + b"x"})
    assert asyncio.run(demo_assets.download_all(good.client()))["state"] == "ready"


def test_a_file_with_the_wrong_size_on_disk_is_not_treated_as_cached(env):
    demo_assets.cache_dir().mkdir(parents=True)
    (demo_assets.cache_dir() / "a.mp4").write_bytes(b"short")
    assert demo_assets.cached_path("a.mp4") is None
    assert demo_assets.cached_path("../../etc/passwd") is None


def test_status_offline_reads_local_state_only(env):
    # No client is ever created: this would raise if status() reached the network.
    def boom(*a, **k):
        raise AssertionError("status() must not use the network")

    import api.http_client as hc

    original = hc.get_http_client
    hc.get_http_client = boom
    try:
        s = demo_assets.status()
    finally:
        hc.get_http_client = original
    assert s["state"] == "idle"
    assert s["total_bytes"] == len(_GOOD) * 2 + 1
    assert s["downloaded_bytes"] == 0
    assert s["files"] == {"a.mp4": False, "b.mp4": False}


def test_base_url_env_override(env, monkeypatch):
    assert demo_assets.base_url() == (
        "https://github.com/debpalash/omnivoice-gallery/releases/download/dub-demo-v1"
    )
    monkeypatch.setenv("OMNIVOICE_DEMO_ASSETS_URL", "http://127.0.0.1:9/demo/")
    assert demo_assets.base_url() == "http://127.0.0.1:9/demo"


def test_nothing_is_fetched_at_import_or_by_status_or_get(env, monkeypatch):
    """Only an explicit POST may start a download."""
    from fastapi import FastAPI

    from api.routers import demo_assets as router_module

    server = Server({"a.mp4": _GOOD, "b.mp4": _GOOD + b"x"})
    import api.http_client as hc

    monkeypatch.setattr(hc, "get_http_client", server.client)
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides.update(
        {
            router_module.require_admin: lambda: None,
            router_module.require_consumer: lambda: None,
        }
    )
    with TestClient(app) as client:
        assert client.get("/demo/dubbing/status").json()["state"] == "idle"
        assert client.get("/demo/dubbing/status").json()["state"] == "idle"
        assert server.requests == []
        assert client.post("/demo/dubbing/download").status_code == 200
        # Explicit POST is the only trigger; wait for the background task.
        deadline = time.monotonic() + 5
        while demo_assets.status()["state"] != "ready" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert demo_assets.status()["state"] == "ready"
    assert server.requests


def test_service_does_not_start_work_at_import():
    src = Path(demo_assets.__file__).read_text(encoding="utf-8")
    top_level = [ln for ln in src.splitlines() if ln and not ln[0].isspace()]
    assert not any("create_task" in ln or "get_http_client()" in ln for ln in top_level)


def test_cached_file_is_served_with_ranges_and_missing_is_404(env, tmp_path):
    samples = tmp_path / "samples"
    (samples / "demo" / "dubbing").mkdir(parents=True)
    (samples / "demo" / "dubbing" / "a.jpg").write_bytes(b"\xff\xd8poster")
    demo_assets.cache_dir().mkdir(parents=True)
    (demo_assets.cache_dir() / "a.mp4").write_bytes(_GOOD)
    app = Starlette(routes=[Mount("/demo_audio", demo_assets.make_demo_mount(str(samples)))])
    client = TestClient(app)

    ok = client.get("/demo_audio/demo/dubbing/a.mp4")
    assert ok.status_code == 200 and ok.content == _GOOD
    assert ok.headers["content-type"].startswith("video/")
    assert ok.headers["x-content-type-options"] == "nosniff"
    part = client.get("/demo_audio/demo/dubbing/a.mp4", headers={"Range": "bytes=0-3"})
    assert part.status_code == 206 and part.content == _GOOD[:4]
    # Bundled posters still come from the app bundle.
    assert client.get("/demo_audio/demo/dubbing/a.jpg").content == b"\xff\xd8poster"
    # Not cached yet / not pinned: plain 404, never a stray file.
    assert client.get("/demo_audio/demo/dubbing/b.mp4").status_code == 404
    assert client.get("/demo_audio/demo/dubbing/c.mp4").status_code == 404


def test_pinned_table_covers_every_video_and_is_well_formed():
    table = json.loads(
        (Path(demo_assets.__file__).resolve().parents[1]
         / "assets/samples/demo/dubbing/demo_assets.json").read_text(encoding="utf-8")
    )
    assert table["tag"] == "dub-demo-v1" and table["cache_dir"] == "dubbing-v1"
    for entry in table["files"]:
        assert len(entry["sha256"]) == 64 and int(entry["sha256"], 16) >= 0
        assert entry["bytes"] > 0
        assert entry["kind"] in {"video", "dev"}
    assert sum(e["bytes"] for e in table["files"] if e["kind"] == "video") < 12 << 20
