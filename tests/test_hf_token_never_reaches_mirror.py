"""The Hugging Face token never reaches a mirror set through HF_ENDPOINT.

Settings → Network saves a mirror as ``HF_ENDPOINT``, which huggingface_hub
reads at import and uses for every call without an explicit ``endpoint=``.
An explicit ``token=`` is sent even with implicit tokens disabled, so token
validation, saving and gated-access checks must pin huggingface.co. A fresh
interpreter is used because huggingface_hub fixes its endpoint at import.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

_ROOT = Path(__file__).resolve().parents[1]
_TOKEN = "hf_syntheticmirrorprobe0123456789"

_SCRIPT = r'''
import json, sys
import httpx
import huggingface_hub

seen = []

def handler(request):
    seen.append({"url": str(request.url), "auth": request.headers.get("authorization")})
    if request.url.path == "/api/whoami-v2":
        return httpx.Response(200, json={
            "name": "alice",
            "auth": {"accessToken": {"displayName": "synthetic", "role": "read"}},
        })
    return httpx.Response(200, headers={
        "x-repo-commit": "a" * 40, "etag": '"abc"', "content-length": "1",
    })

huggingface_hub.set_client_factory(lambda: httpx.Client(transport=httpx.MockTransport(handler)))
assert huggingface_hub.constants.ENDPOINT == "https://hf-mirror.com", huggingface_hub.constants.ENDPOINT

sys.path.insert(0, sys.argv[1])
from core import db
db.init_db()
from services import token_resolver

token = sys.argv[2]
result = {}
result["resolved"] = getattr(token_resolver.resolve(), "source", None)
token_resolver.save_app_token(token)
token_resolver.invalidate_cache()
result["state"] = token_resolver.state(validate=True)["active"]
from api.routers.setup.models import model_access_status
result["access"] = model_access_status("pyannote/speaker-diarization-3.1")["ready"]
result["seen"] = list(seen)

# Control: the unpinned library call does go to the mirror, which is why
# every account call above must pin the endpoint.
seen.clear()
try:
    huggingface_hub.whoami(token=token)
except Exception:
    pass
result["control"] = list(seen)
print(json.dumps(result))
'''


def test_token_checks_go_only_to_hugging_face(tmp_path):
    env = {
        **os.environ,
        "HF_ENDPOINT": "https://hf-mirror.com",
        "HF_TOKEN": _TOKEN,
        "HF_HUB_OFFLINE": "0",
        "HF_HOME": str(tmp_path / "hf"),
        "HF_TOKEN_PATH": str(tmp_path / "hf" / "token"),
        "OMNIVOICE_DATA_DIR": str(tmp_path / "data"),
        "OMNIVOICE_MODEL": "test",
        "OMNIVOICE_DISABLE_FILE_LOG": "1",
    }
    env.pop("HF_HUB_DISABLE_IMPLICIT_TOKEN", None)
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(_ROOT / "backend"), _TOKEN],
        cwd=_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    result = json.loads(proc.stdout.strip().splitlines()[-1])

    assert result["resolved"] == "env"
    assert result["state"] == "app"
    assert result["access"] is True
    authed = [r for r in result["seen"] if r["auth"]]
    assert authed, "no authenticated request was observed"
    for request in authed:
        assert request["url"].startswith("https://huggingface.co/"), request
        assert request["auth"] == f"Bearer {_TOKEN}"
    paths = {r["url"].split("huggingface.co", 1)[1] for r in authed}
    assert "/api/whoami-v2" in paths
    assert any(p.endswith("/.gitattributes") for p in paths), paths
    assert Path(env["HF_TOKEN_PATH"]).read_text() == _TOKEN

    control = [r for r in result["control"] if r["auth"]]
    assert control and control[0]["url"].startswith("https://hf-mirror.com/"), result["control"]
