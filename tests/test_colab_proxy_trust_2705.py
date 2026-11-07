"""#2705: the Colab notebook's proxied web UI must reach the API.

Cell 6 opens the UI through Colab's port proxy on a per-session
``https://<id>-3900-colab.googleusercontent.com`` origin. The backend's browser
guard refuses API calls that name an unknown host or come from an unknown
origin, so the page loaded and every API call got a 403. Cell 5 now trusts
exactly that session's proxy origin for the backend it launches.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from core.browser_guard import BrowserGuardMiddleware

NOTEBOOK = Path(__file__).resolve().parents[1] / "notebooks/OmniVoice_Studio_Colab.ipynb"
PROXY = "https://8ryeuvc2w3-496ff2e9c6d22116-3900-colab.googleusercontent.com"
PROXY_HOST = PROXY.removeprefix("https://")


def _launch_cell() -> str:
    cells = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]
    return next("".join(c["source"]) for c in cells
                if "# ── 5. Launch the backend" in "".join(c["source"]))


def _helpers() -> dict:
    source = _launch_cell()
    block = source[source.index("# -- Colab proxy trust"):source.index("# -- end Colab proxy trust")]
    namespace: dict = {}
    exec(block, namespace)  # noqa: S102 — our own notebook source
    return namespace


@pytest.fixture
def proxy_trust_env():
    return _helpers()["proxy_trust_env"]


def test_trusts_exactly_the_session_proxy_origin(proxy_trust_env):
    env = proxy_trust_env(PROXY + "/?authuser=0", {})
    assert env == {
        "OMNIVOICE_ALLOWED_ORIGINS": PROXY,
        "OMNIVOICE_ALLOWED_HOSTS": PROXY_HOST,
    }


def test_existing_allow_lists_are_kept(proxy_trust_env):
    env = proxy_trust_env(PROXY, {
        "OMNIVOICE_ALLOWED_ORIGINS": "http://localhost:3901",
        "OMNIVOICE_ALLOWED_HOSTS": f"studio.example.com,{PROXY_HOST}",
    })
    assert env["OMNIVOICE_ALLOWED_ORIGINS"] == f"http://localhost:3901,{PROXY}"
    assert env["OMNIVOICE_ALLOWED_HOSTS"] == f"studio.example.com,{PROXY_HOST}"


@pytest.mark.parametrize("bad", [
    None, "", "http://abc-3900-colab.googleusercontent.com", "javascript:alert(1)",
    "https://user:pw@abc.googleusercontent.com", "not a url", "https://",
])
def test_anything_but_a_plain_https_origin_trusts_nothing(proxy_trust_env, bad):
    assert proxy_trust_env(bad, {}) == {}


def test_outside_colab_the_launcher_adds_nothing():
    assert _helpers()["colab_proxy_env"](3900, {}) == {}


def test_launch_cell_applies_the_trust_before_starting_uvicorn():
    source = _launch_cell()
    trust = source.index("env.update(colab_proxy_env(PORT, env))")
    assert trust < source.index("subprocess.Popen(")
    # A Drive data dir set per the troubleshooting cell is not overwritten.
    assert 'env.setdefault("OMNIVOICE_DATA_DIR", "/content/omnivoice_data")' in source


# ── The guard itself, in both proxy topologies ──────────────────────────────

def _client(host: str) -> TestClient:
    async def ok(_request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/generate", ok, methods=["POST"]),
                            Route("/profiles", ok, methods=["GET"])])
    app.add_middleware(BrowserGuardMiddleware)
    return TestClient(app, base_url=f"http://{host}")


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("OMNIVOICE_ALLOWED_ORIGINS", "OMNIVOICE_ALLOWED_HOSTS",
                 "OMNIVOICE_API_KEY", "OMNIVOICE_PUBLIC_API_BASE"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize("forwarded_host", [PROXY_HOST, "localhost:3900"])
def test_proxied_ui_is_refused_without_the_trust(clean_env, forwarded_host):
    client = _client(forwarded_host)
    r = client.post("/generate", headers={"Origin": PROXY, "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 403


@pytest.mark.parametrize("forwarded_host", [PROXY_HOST, "localhost:3900"])
def test_proxied_ui_reaches_the_api_with_the_trust(clean_env, proxy_trust_env, forwarded_host):
    for name, value in proxy_trust_env(PROXY, {}).items():
        clean_env.setenv(name, value)
    client = _client(forwarded_host)
    headers = {"Origin": PROXY, "Sec-Fetch-Site": "same-origin"}
    assert client.post("/generate", headers=headers).status_code == 200
    assert client.get("/profiles", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_another_colab_session_stays_refused(clean_env, proxy_trust_env):
    for name, value in proxy_trust_env(PROXY, {}).items():
        clean_env.setenv(name, value)
    other = "https://zzz-3900-colab.googleusercontent.com"
    client = _client(PROXY_HOST)
    assert client.post("/generate", headers={"Origin": other}).status_code == 403
    assert _client(other.removeprefix("https://")).get("/profiles").status_code == 403
