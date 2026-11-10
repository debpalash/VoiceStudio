"""Sound-effect generation (#3577): status, limits, fallbacks, wire, install spec.

All engine layers are stubbed: no GPU, no weights, no sidecar process.
"""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import sys
from pathlib import Path

import pytest
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def _init_db():
    from core.db import init_db

    init_db()


def _host(monkeypatch, family):
    import core.device_caps as caps

    class _Caps:
        pass

    fake = _Caps()
    fake.family = family
    monkeypatch.setattr(caps, "detect_host_caps", lambda: fake)


def _installed(monkeypatch, value=True):
    import engines.moss_sfx.bootstrap as boot

    monkeypatch.setattr(boot, "is_sfx_installed", lambda: value)


class _FakeSfx:
    sample_rate = 48000

    def __init__(self, exc=None):
        self.exc = exc
        self.calls = []

    def generate(self, prompt, **kw):
        self.calls.append((prompt, kw))
        if self.exc:
            raise self.exc
        return torch.zeros(1, 48000)


# ── status + fallbacks ─────────────────────────────────────────────────────


def test_status_not_installed_offers_size_and_licence(client, monkeypatch):
    _host(monkeypatch, "cuda")
    _installed(monkeypatch, False)
    body = client.get("/api/sfx/status").json()
    assert body["supported"] is True and body["installed"] is False and body["available"] is False
    assert "not installed" in body["reason"].lower()
    assert body["reason_code"] == "not_installed"
    assert body["license"] == "Apache-2.0"
    assert body["download_bytes"] > 10 * 1024**3
    assert (body["min_seconds"], body["max_seconds"]) == (1.0, 30.0)


def test_missing_weights_report_not_installed_with_a_repair_reason(client, monkeypatch, tmp_path):
    import engines.moss_sfx.bootstrap as boot

    _host(monkeypatch, "cuda")
    monkeypatch.setattr(boot, "venv_python", lambda: tmp_path / "python")
    monkeypatch.setattr(boot, "sfx_weights_present", lambda: False)
    body = client.get("/api/sfx/status").json()
    assert body["installed"] is False and body["available"] is False
    assert "repair" in body["reason"].lower() and "never downloads" in body["reason"]
    assert body["reason_code"] == "weights_missing"
    monkeypatch.setattr(boot, "sfx_weights_present", lambda: True)
    assert client.get("/api/sfx/status").json()["installed"] is True


def test_weights_probe_needs_the_pinned_completion_marker(monkeypatch, tmp_path):
    import services.sidecar_install as si
    from engines.moss_sfx.bootstrap import sfx_weights_present

    spec = si.SPECS["moss-soundeffect"]
    monkeypatch.setattr(si, "managed_checkout", lambda s: tmp_path)
    wdir = tmp_path / spec.weights_subdir
    wdir.mkdir()
    (wdir / "model_index.json").write_text("{}")
    (wdir / "big.safetensors").write_bytes(b"\0" * (6 * 1024 * 1024))
    assert sfx_weights_present() is False  # no completion marker
    (wdir / si._WEIGHTS_COMPLETE_MARKER).write_text(
        f"{spec.weights_repo_id}\n{spec.weights_revision}\n", encoding="utf-8")
    assert sfx_weights_present() is True
    (wdir / "big.safetensors").unlink()
    assert sfx_weights_present() is False  # weights deleted


@pytest.mark.parametrize("family", ["mps", "rocm", "cpu"])
def test_status_unsupported_host_names_the_reason(client, monkeypatch, family):
    _host(monkeypatch, family)
    _installed(monkeypatch, True)
    body = client.get("/api/sfx/status").json()
    assert body["supported"] is False and body["available"] is False
    assert "NVIDIA" in body["reason"]
    assert body["reason_code"] == "host_unsupported"
    assert body["install_allowed"] is False


def test_status_ready(client, monkeypatch):
    _host(monkeypatch, "cuda")
    _installed(monkeypatch, True)
    body = client.get("/api/sfx/status").json()
    assert body["available"] is True and body["reason"] is None and body["reason_code"] is None


def test_status_reports_a_missing_sidecar_script(client, monkeypatch, tmp_path):
    import engines.moss_sfx.bootstrap as boot

    _host(monkeypatch, "cuda")
    _installed(monkeypatch, True)
    monkeypatch.setattr(boot, "SFX_SIDECAR_SCRIPT", tmp_path / "missing.py")
    body = client.get("/api/sfx/status").json()
    assert body["available"] is False and body["reason_code"] == "sidecar_missing"


def test_unsupported_host_refuses_install_with_a_reason(monkeypatch):
    from services import sidecar_install as si

    _host(monkeypatch, "mps")
    ok, why = si.host_support(si.SPECS["moss-soundeffect"])
    assert not ok and "NVIDIA" in why
    with pytest.raises(si.HostUnsupported):
        si.start_install("moss-soundeffect")


def test_generate_refuses_when_not_installed(client, monkeypatch):
    _host(monkeypatch, "cuda")
    _installed(monkeypatch, False)
    res = client.post("/api/sfx/generate", json={"prompt": "rain", "duration": 5})
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "sfx_not_installed"


def test_generate_refuses_on_unsupported_host(client, monkeypatch):
    _host(monkeypatch, "cpu")
    res = client.post("/api/sfx/generate", json={"prompt": "rain", "duration": 5})
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "sfx_unsupported_host"


# ── limits ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("duration", [0, 0.5, 30.1, 120, -3])
def test_duration_outside_limits_is_rejected_naming_the_limit(client, duration):
    res = client.post("/api/sfx/generate", json={"prompt": "rain", "duration": duration})
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "sfx_bad_duration" and "between 1 and 30 seconds" in detail["message"]


@pytest.mark.parametrize("prompt", ["", "   \n "])
def test_blank_prompt_is_rejected(client, prompt):
    res = client.post("/api/sfx/generate", json={"prompt": prompt, "duration": 5})
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "sfx_prompt_required"


def test_overlong_prompt_is_rejected(client):
    res = client.post("/api/sfx/generate", json={"prompt": "x" * 501, "duration": 5})
    detail = res.json()["detail"]
    assert res.status_code == 422 and detail["code"] == "sfx_prompt_too_long" and "500" in detail["message"]


# ── generation ─────────────────────────────────────────────────────────────


def _ready(monkeypatch, fake):
    import engines.moss_sfx as sfx

    _host(monkeypatch, "cuda")
    _installed(monkeypatch, True)
    monkeypatch.setattr(sfx, "get_backend", lambda: fake)
    monkeypatch.setattr(
        sfx.MossSfxBackend, "availability", classmethod(lambda cls: ("ok", "ok"))
    )


def _cleanup(audio_url):
    from core.config import OUTPUTS_DIR
    from core.db import db_conn

    name = os.path.basename(audio_url)
    with db_conn() as conn:
        conn.execute("DELETE FROM generation_history WHERE audio_path=?", (name,))
    with contextlib.suppress(OSError):
        os.remove(os.path.join(OUTPUTS_DIR, name))


def test_generate_saves_a_marked_take_and_records_history(client, monkeypatch, _init_db):
    from core.config import OUTPUTS_DIR
    from core.db import db_conn
    import services.watermark as wm

    fake = _FakeSfx()
    _ready(monkeypatch, fake)
    marked = []

    async def _mark(tensor, sample_rate, *, context, **kw):
        marked.append(context)
        return tensor

    monkeypatch.setattr(wm, "mark_synthetic_async", _mark)
    res = client.post("/api/sfx/generate", json={"prompt": " rain   on a roof ", "duration": 4.0, "seed": 7})
    assert res.status_code == 200, res.text
    body = res.json()
    try:
        assert body["engine"] == "moss-soundeffect" and body["seed"] == 7
        assert body["audio_url"].startswith("/audio/")
        assert os.path.isfile(os.path.join(OUTPUTS_DIR, os.path.basename(body["audio_url"])))
        assert fake.calls == [("rain on a roof", {"seconds": 4.0, "seed": 7})]
        assert marked == ["sfx.finalize"]
        with db_conn() as conn:
            row = conn.execute("SELECT mode FROM generation_history WHERE id=?", (body["id"],)).fetchone()
        assert row["mode"] == "sfx"
    finally:
        _cleanup(body["audio_url"])


def test_oom_is_a_clear_error_and_the_app_stays_up(client, monkeypatch):
    _ready(monkeypatch, _FakeSfx(RuntimeError("moss-soundeffect sidecar synthesize error: CUDA out of memory")))
    res = client.post("/api/sfx/generate", json={"prompt": "thunder", "duration": 10})
    assert res.status_code == 500
    detail = res.json()["detail"]
    assert detail["code"] == "sfx_out_of_memory" and "ran out of memory" in detail["message"]
    assert client.get("/api/sfx/status").status_code == 200


def test_sidecar_crash_is_a_clear_error(client, monkeypatch):
    _ready(monkeypatch, _FakeSfx(RuntimeError("sidecar died")))
    res = client.post("/api/sfx/generate", json={"prompt": "thunder", "duration": 10})
    assert res.status_code == 500
    detail = res.json()["detail"]
    assert detail["code"] == "sfx_failed" and "other engines are unaffected" in detail["message"]


@pytest.mark.parametrize(
    ("exc_name", "code"), [("GpuPoolBusyError", "sfx_busy"), ("GpuJobTimeoutError", "sfx_timeout")]
)
def test_busy_and_timeout_carry_a_code_and_stay_retryable(client, monkeypatch, exc_name, code):
    import services.model_manager as mm

    _ready(monkeypatch, _FakeSfx())
    exc = getattr(mm, exc_name)
    err = exc("busy") if exc_name == "GpuJobTimeoutError" else exc("busy", retry_after=3)

    async def _raise(*a, **k):
        raise err

    monkeypatch.setattr(mm, "run_on_gpu_pool_guarded", _raise)
    res = client.post("/api/sfx/generate", json={"prompt": "thunder", "duration": 10})
    assert res.status_code == 503 and res.headers["X-OmniVoice-Retryable"] == "true"
    assert res.json()["detail"]["code"] == code


# ── isolation from speech ──────────────────────────────────────────────────


def test_sfx_is_not_a_tts_engine():
    from services import tts_backend

    ids = {b["id"] for b in tts_backend.list_backends(include_hidden=True)}
    assert "moss-soundeffect" not in ids


def test_sfx_catalogue_role_is_not_a_speech_role_and_is_hidden_from_models(client):
    import yaml

    catalogue = yaml.safe_load((ROOT / "backend/config/models.yaml").read_text(encoding="utf-8"))
    row = next(m for m in catalogue["models"] if m["repo_id"] == "OpenMOSS-Team/MOSS-SoundEffect-v2.0")
    assert row["role"] == "SFX" and "curated_on" not in row and not row.get("required")
    models = client.get("/models").json()["models"]
    assert all(m.get("role") != "SFX" for m in models)


# ── install spec pins ──────────────────────────────────────────────────────


def test_install_spec_is_pinned_and_matches_the_licence_row():
    from services import hf_revisions
    from services import sidecar_install as si

    spec = si.SPECS["moss-soundeffect"]
    assert len(spec.source_revision) == 40 and spec.source_revision in spec.tarball_url
    assert len(spec.weights_revision) == 40
    assert spec.weights_repo_id == "OpenMOSS-Team/MOSS-SoundEffect-v2.0"
    assert hf_revisions.revision_for(spec.weights_repo_id) == spec.weights_revision
    assert "--python" in spec.venv_args
    licences = json.loads((ROOT / "backend/config/model_licenses.json").read_text(encoding="utf-8"))
    row = next(m for m in licences["models"] if m["id"] == spec.weights_repo_id)
    assert row["license"] == "apache-2.0" and row["revision"] == spec.weights_revision


# ── sidecar wire + protocol ────────────────────────────────────────────────


def test_sidecar_rejects_bad_synthesize_requests_without_loading_a_model():
    from engines.moss_sfx import main as sidecar

    out = io.BytesIO()
    for msg in ({"op": "synthesize"}, {"op": "synthesize", "text": "x", "seconds": 31}):
        with pytest.raises(ValueError):
            sidecar._handle_synthesize(msg, out)
    assert sidecar._pipeline is None


def test_sidecar_pcm_conversion_keeps_time_axis():
    from engines.moss_sfx import main as sidecar

    audio = torch.linspace(-1, 1, 48000).reshape(1, 1, -1)
    b64, sr, n = sidecar._to_pcm_b64(audio, 48000)
    assert (sr, n) == (48000, 48000)
    assert len(base64.b64decode(b64)) == 48000 * 2


def test_backend_is_not_registered_and_carries_the_pinned_limits():
    from engines.moss_sfx import MAX_SECONDS, MossSfxBackend

    assert MAX_SECONDS == 30.0 and MossSfxBackend.gpu_compat == ("cuda",)
    assert MossSfxBackend._is_subprocess_isolated is True


def test_backend_round_trips_through_a_fake_sidecar(monkeypatch):
    """Real SubprocessBackend wire (spawn, ready, synthesize, audio) against the echo sidecar."""
    from engines.moss_sfx import MossSfxBackend

    class _Echoing(MossSfxBackend):
        @classmethod
        def venv_python(cls):
            return Path(sys.executable)

        @classmethod
        def sidecar_script(cls):
            return ROOT / "backend" / "engines" / "_echo" / "main.py"

    backend = _Echoing()
    try:
        audio = backend.generate("rain", seconds=2.0, seed=1)
        assert audio.ndim == 2 and audio.shape[0] == 1 and audio.shape[1] > 0
    finally:
        backend.shutdown()
