"""Plan 02-04 — API contract for /engines + /engines/{id}/health.

Asserts:
  * ``GET /engines`` returns the documented per-entry shape
    (id, display_name, available, reason, install_hint, last_error,
     isolation_mode, gpu_compat) for every backend.
  * ``GET /engines/{engine_id}/health`` round-trips for both
    SubprocessBackend (mocked health_check) and in-process backends.
  * Loopback gate is enforced on the health route — non-loopback origin
    returns 403.
  * Unknown engine id returns 404.
  * HF-shaped tokens that a backend's is_available() leaks into its
    error message do NOT reach the response body — T-02-12.

The fixture builds a minimal FastAPI app with just the engines router so
the test stays fast and doesn't require torch / whisperx / demucs to be
fully importable.
"""
from __future__ import annotations

import re
import sys

import pytest


SAMPLE_HF_TOKEN = "hf_abcdefghijklmnopqrstuvwxyz01234567890abcd"
HF_TOKEN_RE = re.compile(r"hf_[A-Za-z0-9]{30,}")


# ── helpers ────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_app(monkeypatch, tmp_path):
    """Build a fresh FastAPI app instance with isolated DB.

    The full main.py app factory pulls in torch / whisperx / demucs; we
    only need the engines router for these tests so we mount it
    directly, matching the pattern in tests/backend/test_engine_spawn_token.py.
    """
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))

    # Wipe cached services so each test gets a clean _LAST_ERRORS dict +
    # _REGISTRY (the engines router imports them on first call).
    for mod in list(sys.modules):
        if (
            mod == "core" or mod.startswith("core.")
            or mod == "services" or mod.startswith("services.")
            or mod == "api" or mod.startswith("api.")
        ):
            del sys.modules[mod]

    from core import db as _db
    _db.init_db()

    from fastapi import FastAPI
    from api.routers import engines as engines_router

    app = FastAPI()
    app.include_router(engines_router.router)
    return app


def _client(app, host="127.0.0.1"):
    """TestClient anchored to a loopback (or non-loopback) client tuple.

    The admin dependency reads `request.client.host`; the default
    TestClient tuple is `('testclient', 50000)` which the dep rejects.
    """
    from fastapi.testclient import TestClient
    return TestClient(app, client=(host, 12345))


# ── /engines response shape (gpu_compat, isolation_mode, last_error) ──────


# The full 11-key shape every family now shares (TTS + ASR + LLM parity).
_REQUIRED_KEYS = {
    "id", "display_name", "available", "reason",
    "install_hint", "last_error", "isolation_mode", "gpu_compat",
    "effective_device", "routing_status", "routing_reason",
}
_TTS_ASR_STATUSES = {"accelerated", "cpu_fallback", "cpu_only", "unavailable"}
_VALID_FAMILIES = {"cuda", "rocm", "mps", "xpu", "npu", "cpu"}


def test_engines_response_includes_new_fields(fresh_app):
    client = _client(fresh_app)
    r = client.get("/engines")
    assert r.status_code == 200
    body = r.json()

    for entry in body["tts"]["backends"]:
        missing = _REQUIRED_KEYS - entry.keys()
        assert not missing, f"entry {entry.get('id')!r} missing keys: {missing}"
        assert isinstance(entry["gpu_compat"], list)
        assert all(isinstance(x, str) for x in entry["gpu_compat"])
        assert entry["isolation_mode"] in {"in-process", "subprocess"}


def test_engines_response_marks_environment_pinned_families(fresh_app, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_ASR_BACKEND", "pytorch-whisper")
    body = _client(fresh_app).get("/engines").json()

    assert body["asr"]["env_override"] is True
    assert body["tts"]["env_override"] is False


def test_all_families_share_the_11_key_shape(fresh_app):
    client = _client(fresh_app)
    body = client.get("/engines").json()
    for fam in ("tts", "asr", "llm"):
        for entry in body[fam]["backends"]:
            missing = _REQUIRED_KEYS - entry.keys()
            assert not missing, f"{fam} entry {entry.get('id')!r} missing: {missing}"


def test_tts_asr_routing_keys_are_well_formed(fresh_app):
    client = _client(fresh_app)
    body = client.get("/engines").json()
    for fam in ("tts", "asr"):
        for entry in body[fam]["backends"]:
            assert entry["routing_status"] in _TTS_ASR_STATUSES
            assert entry["effective_device"] in _VALID_FAMILIES
            # routing_reason is a scrubbed str or JSON null — never the empty
            # string (the None-vs-"" serialization contract).
            assert entry["routing_reason"] is None or isinstance(entry["routing_reason"], str)
            assert entry["routing_reason"] != ""


def test_llm_entries_are_network_n_a(fresh_app):
    client = _client(fresh_app)
    body = client.get("/engines").json()
    for entry in body["llm"]["backends"]:
        assert entry["effective_device"] == "network"
        assert entry["routing_status"] == "n/a"
        assert entry["routing_reason"] is None
        assert entry["gpu_compat"] == []


def test_diarisation_status_distinguishes_model_from_native_runtime(
    fresh_app, monkeypatch
):
    from services import diarization_runtime

    monkeypatch.setattr(
        diarization_runtime,
        "selected_backend",
        lambda: diarization_runtime.SORTFORMER,
    )
    monkeypatch.setattr(
        diarization_runtime,
        "sortformer_status",
        lambda: {
            "model": diarization_runtime.SORTFORMER_REPO,
            "model_installed": True,
            "runtime_installed": False,
            "installed": False,
            "reason": (
                "The Sortformer model is installed. Install the audio.cpp runtime to use it"
            ),
        },
    )

    response = _client(fresh_app).get("/engines/diarisation")

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == diarization_runtime.SORTFORMER_REPO
    assert body["model_installed"] is True
    assert body["runtime_installed"] is False
    assert body["installed"] is False
    assert body["reason"] == (
        "The Sortformer model is installed. Install the audio.cpp runtime to use it"
    )
    native = next(item for item in body["options"] if item["id"] == body["active"])
    assert native["model_installed"] is True
    assert native["runtime_installed"] is False


def test_audiocpp_runtime_installer_routes_are_desktop_scoped(
    fresh_app, monkeypatch
):
    from services import audiocpp_runtime_install

    payload = {
        "supported": True,
        "installed": False,
        "managed": False,
        "version": None,
        "platform": "windows-x64",
        "job": {"state": "idle", "progress": 0.0, "error": None},
    }
    monkeypatch.setattr(audiocpp_runtime_install, "status", lambda: payload)
    monkeypatch.setattr(
        audiocpp_runtime_install,
        "start_install",
        lambda: {"status": "started", **payload},
    )
    client = _client(fresh_app)

    assert client.get("/engines/audiocpp/runtime/install/status").json() == {**payload, "install_allowed": True}
    response = client.post("/engines/audiocpp/runtime/install")
    assert response.status_code == 200
    assert response.json()["status"] == "started"


def test_engine_discovery_omits_service_diagnostics(fresh_app, monkeypatch):
    from api.routers import engines

    private = "Traceback: token=private-value at /home/alice/models/engine.py"
    unsafe = {
        "id": "probe", "display_name": "Probe", "available": False,
        "reason": private, "last_error": private, "routing_reason": private,
        "routing_status": "cpu_fallback",
        "install_hint": "Install the registered dependency.",
    }
    monkeypatch.setattr(engines.tts_backend, "active_backend_id", lambda: "probe")
    monkeypatch.setattr(engines.tts_backend, "list_backends", lambda: [unsafe])

    body = _client(fresh_app).get("/engines/tts").json()
    rendered = repr(body)
    assert private not in rendered
    assert "/home/alice" not in rendered
    assert "Traceback" not in rendered
    assert "private-value" not in rendered
    assert body["backends"][0]["reason"] == (
        "Engine unavailable. Check installation and configuration."
    )
    assert body["backends"][0]["last_error"] == "A previous engine check failed."
    assert body["backends"][0]["install_hint"] == "Install the registered dependency."
    assert body["backends"][0]["routing_reason"] == (
        "GPU acceleration is unavailable; this engine will use CPU."
    )


@pytest.mark.parametrize(
    ("status", "private_reason", "expected"),
    [
        (
            "accelerated",
            "NVIDIA RTX has 4.0 GB VRAM; this engine wants about 8 GB.",
            "The accelerator may not meet this engine's recommended VRAM.",
        ),
        (
            "accelerated",
            "sm_120 is absent and may fail at kernel launch: /home/alice",
            "The selected accelerator may not be supported by this PyTorch build.",
        ),
        (
            "cpu_fallback",
            "engine has no CUDA path; running on CPU at /home/alice",
            "GPU acceleration is unavailable; this engine will use CPU.",
        ),
        (
            "cpu_only",
            "DirectML GPU present; engine routes via private CPU path",
            "This engine runs on CPU on this host.",
        ),
        (
            "unavailable",
            "requires cuda; this host probe read /home/alice/device",
            "This engine has no compatible compute device on this host.",
        ),
    ],
)
def test_engine_discovery_preserves_routing_outcome_without_diagnostics(
    fresh_app, monkeypatch, status, private_reason, expected
):
    from api.routers import engines

    unsafe = {
        "id": "probe",
        "available": status != "unavailable",
        "routing_status": status,
        "routing_reason": private_reason,
    }
    monkeypatch.setattr(engines.tts_backend, "active_backend_id", lambda: "probe")
    monkeypatch.setattr(engines.tts_backend, "list_backends", lambda: [unsafe])

    body = _client(fresh_app).get("/engines/tts").json()

    assert body["backends"][0]["routing_reason"] == expected
    assert "/home/alice" not in repr(body)


def test_indextts2_entry_has_subprocess_isolation_mode(fresh_app):
    """Cross-checks Plan 02-03's IndexTTS subprocess migration via the API."""
    client = _client(fresh_app)
    r = client.get("/engines")
    assert r.status_code == 200
    by_id = {b["id"]: b for b in r.json()["tts"]["backends"]}
    assert "indextts2" in by_id
    assert by_id["indextts2"]["isolation_mode"] == "subprocess"


def test_omnivoice_entry_has_in_process_isolation_mode(fresh_app, monkeypatch):
    _force_cpu_host(monkeypatch)
    client = _client(fresh_app)
    r = client.get("/engines")
    assert r.status_code == 200
    by_id = {b["id"]: b for b in r.json()["tts"]["backends"]}
    assert "omnivoice" in by_id
    assert by_id["omnivoice"]["isolation_mode"] == "in-process"


def test_mps_picker_hides_redundant_omnivoice_sidecar_but_api_accepts_it(
    fresh_app, monkeypatch
):
    """Keep stored/direct compatibility ids valid without advertising a duplicate."""
    from engines.omnivoice_subprocess import (
        OmniVoiceMPSSubprocessBackend,
        OmniVoiceSubprocessBackend,
    )
    from services import tts_backend

    _force_host(monkeypatch, "mps")
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)
    monkeypatch.setattr(
        OmniVoiceSubprocessBackend,
        "is_available",
        classmethod(lambda cls: (True, "ready")),
    )

    client = _client(fresh_app)
    payload = client.get("/engines/tts").json()
    rows = {row["id"] for row in payload["backends"]}
    assert "omnivoice" in rows
    assert "omnivoice-subprocess" not in rows
    assert payload["active"] == "omnivoice"

    canonical = next(
        row for row in payload["backends"] if row["id"] == "omnivoice"
    )
    assert canonical["isolation_mode"] == "subprocess"
    assert tts_backend.get_backend_class("omnivoice") is OmniVoiceMPSSubprocessBackend

    response = client.post(
        "/engines/select",
        json={"family": "tts", "backend_id": "omnivoice-subprocess"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["active"] == "omnivoice-subprocess"
    assert tts_backend.get_backend_class("omnivoice-subprocess") is OmniVoiceSubprocessBackend


def test_gpu_compat_omnivoice_variants_include_rocm(fresh_app, monkeypatch):
    """Both OmniVoice paths use torch's HIP-backed CUDA device on ROCm."""
    _force_host(monkeypatch, "rocm")
    client = _client(fresh_app)
    r = client.get("/engines")
    by_id = {b["id"]: b for b in r.json()["tts"]["backends"]}
    expected = {"cuda", "rocm", "mps", "cpu"}
    assert set(by_id["omnivoice"]["gpu_compat"]) == expected
    assert set(by_id["omnivoice-subprocess"]["gpu_compat"]) == expected


# ── select_engine host-routing gate (no silent CPU fallback) ────────────────


def _force_host(monkeypatch, family="cpu"):
    """Pin detect_host_caps() to a specific host family so routing is
    deterministic regardless of the CI runner's actual hardware."""
    from core.device_caps import HostCaps
    avail = (family, "cpu") if family != "cpu" else ("cpu",)
    caps = HostCaps(family=family, available_families=avail)
    monkeypatch.setattr("core.device_caps.detect_host_caps", lambda: caps)


def _force_cpu_host(monkeypatch):
    _force_host(monkeypatch, "cpu")


def test_omnivoice_variants_route_to_rocm(fresh_app, monkeypatch):
    _force_host(monkeypatch, "rocm")

    body = _client(fresh_app).get("/engines").json()
    by_id = {b["id"]: b for b in body["tts"]["backends"]}
    for backend_id in ("omnivoice", "omnivoice-subprocess"):
        assert by_id[backend_id]["effective_device"] == "rocm"
        assert by_id[backend_id]["routing_status"] == "accelerated"
        assert by_id[backend_id]["routing_reason"] is None


def test_select_blocks_engine_unavailable_on_this_host(fresh_app, monkeypatch):
    """A CUDA-only engine (no cpu path) on a CPU host is `unavailable` → 400."""
    from services import tts_backend as tts_mod

    class CudaOnlyBackend(tts_mod.TTSBackend):
        id = "cuda-only-test"
        display_name = "CUDA-only (test)"
        gpu_compat = ("cuda",)  # no cpu fallback

        @property
        def sample_rate(self): return 24000
        @property
        def supported_languages(self): return ["en"]
        @classmethod
        def is_available(cls): return True, "ready"   # deps fine; host is the problem
        def generate(self, text, **kw): raise NotImplementedError

    _force_cpu_host(monkeypatch)
    saved = dict(tts_mod._REGISTRY)
    try:
        tts_mod._REGISTRY["cuda-only-test"] = CudaOnlyBackend
        r = _client(fresh_app).post(
            "/engines/select", json={"family": "tts", "backend_id": "cuda-only-test"})
        assert r.status_code == 400
        assert "can't run on this machine" in r.json()["detail"]
    finally:
        tts_mod._REGISTRY.clear(); tts_mod._REGISTRY.update(saved)


def test_select_allows_cpu_fallback_engine(fresh_app, monkeypatch):
    """An MPS+CPU engine on a CUDA host is `cpu_fallback` (runs, slower) →
    allowed (not blocked), and the response echoes the routing verdict."""
    from services import tts_backend as tts_mod

    class CpuCapableBackend(tts_mod.TTSBackend):
        id = "cpu-ok-test"
        display_name = "CPU-capable (test)"
        gpu_compat = ("mps", "cpu")  # no CUDA path → cpu_fallback on a CUDA host

        @property
        def sample_rate(self): return 24000
        @property
        def supported_languages(self): return ["en"]
        @classmethod
        def is_available(cls): return True, "ready"
        def generate(self, text, **kw): raise NotImplementedError

    _force_host(monkeypatch, "cuda")
    saved = dict(tts_mod._REGISTRY)
    try:
        tts_mod._REGISTRY["cpu-ok-test"] = CpuCapableBackend
        r = _client(fresh_app).post(
            "/engines/select", json={"family": "tts", "backend_id": "cpu-ok-test"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["active"] == "cpu-ok-test"
        # #21: the response echoes the routing verdict for the picked engine.
        assert body["routing_status"] == "cpu_fallback"
        assert body["effective_device"] == "cpu"
        assert body["routing_reason"]
    finally:
        tts_mod._REGISTRY.clear(); tts_mod._REGISTRY.update(saved)


def test_select_llm_never_routing_gated(fresh_app, monkeypatch):
    """LLM entries are routing_status 'n/a' — the host gate must never fire."""
    _force_cpu_host(monkeypatch)
    # 'off' is always available and has no GPU claim.
    r = _client(fresh_app).post(
        "/engines/select", json={"family": "llm", "backend_id": "off"})
    assert r.status_code == 200, r.text


# ── ASR selection via /engines/select (Model Catalogue ASR picker) ──────
#
# The ASR family was always wired in _FAMILIES on paper, but no UI called it
# and nothing exercised it — the Settings picker now does. Lock the contract:
# a pick persists to prefs["asr_backend"], `OMNIVOICE_ASR_BACKEND` still wins
# over the pick, and unknown / not-ready ids are 400s.


def _register_fake_asr(asr_mod, engine_id, *, available=True):
    """Register a light in-process ASR stub (CPU-only so a forced-CPU host
    routes it `cpu_only`, never `unavailable`). Returns (cls, restore_fn)."""
    _avail = available

    class _FakeASR(asr_mod.ASRBackend):
        id = engine_id
        display_name = f"Fake {engine_id}"
        gpu_compat = ("cpu",)

        @classmethod
        def is_available(cls):
            return (True, "ready") if _avail else (False, "deps missing (test)")

        def transcribe(self, audio_path, *, word_timestamps=True):
            raise NotImplementedError

    saved = dict(asr_mod._REGISTRY)
    asr_mod._REGISTRY[engine_id] = _FakeASR

    def restore():
        asr_mod._REGISTRY.clear()
        asr_mod._REGISTRY.update(saved)

    return _FakeASR, restore


def test_select_asr_persists_pref_and_echoes_active(fresh_app, monkeypatch):
    from core import prefs as _prefs
    from services import asr_backend as asr_mod

    _force_cpu_host(monkeypatch)
    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    _, restore = _register_fake_asr(asr_mod, "fake-asr")
    try:
        r = _client(fresh_app).post(
            "/engines/select", json={"family": "asr", "backend_id": "fake-asr"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["family"] == "asr"
        assert body["active"] == "fake-asr"
        assert body["env_override"] is False
        assert _prefs.get("asr_backend") == "fake-asr"
    finally:
        restore()


def test_select_asr_env_var_still_wins(fresh_app, monkeypatch):
    """CRITICAL backward-compat: an existing `OMNIVOICE_ASR_BACKEND` pin keeps
    winning over a Settings pick — the pick persists to prefs (for when the
    pin is lifted) but the active id stays the env value, and the response
    says so via env_override."""
    from core import prefs as _prefs
    from services import asr_backend as asr_mod

    _force_cpu_host(monkeypatch)
    monkeypatch.setenv("OMNIVOICE_ASR_BACKEND", "pytorch-whisper")
    _, restore = _register_fake_asr(asr_mod, "fake-asr-pinned")
    try:
        r = _client(fresh_app).post(
            "/engines/select", json={"family": "asr", "backend_id": "fake-asr-pinned"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["env_override"] is True
        assert body["active"] == "pytorch-whisper"          # env wins
        assert _prefs.get("asr_backend") == "fake-asr-pinned"
    finally:
        restore()


def test_select_asr_unknown_backend_is_400(fresh_app):
    r = _client(fresh_app).post(
        "/engines/select", json={"family": "asr", "backend_id": "nope-not-real"})
    assert r.status_code == 400
    assert "Unknown asr backend" in r.json()["detail"]


def test_select_asr_unavailable_backend_is_400(fresh_app, monkeypatch):
    from services import asr_backend as asr_mod

    _force_cpu_host(monkeypatch)
    _, restore = _register_fake_asr(asr_mod, "fake-asr-down", available=False)
    try:
        r = _client(fresh_app).post(
            "/engines/select", json={"family": "asr", "backend_id": "fake-asr-down"})
        assert r.status_code == 400
        assert "not ready" in r.json()["detail"]
    finally:
        restore()


def test_get_engines_asr_family_shape(fresh_app):
    """GET /engines/asr — the ASR picker's data source: active id + one row
    per registered backend with availability, reasons and install hints."""
    r = _client(fresh_app).get("/engines/asr")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["active"], str) and body["active"]
    by_id = {b["id"]: b for b in body["backends"]}
    assert {"whisperx", "faster-whisper", "openai-compat-asr"}.issubset(by_id)
    # Install hints power the picker's tooltips (parity with TTS).
    assert by_id["openai-compat-asr"]["install_hint"]
    for entry in by_id.values():
        missing = _REQUIRED_KEYS - entry.keys()
        assert not missing, f"asr entry {entry['id']!r} missing: {missing}"


# ── #981 — mlx-audio curated-model selection via /engines/select ───────────
#
# mlx-audio multiplexes 7+ curated models behind one backend id. Before this
# fix there was NO way anywhere in the UI/API to pick which curated model
# actually loads — it always defaulted to Kokoro even if the user had
# downloaded e.g. Llama-OuteTTS via the engine's Weights list in Model Catalogue.


def _make_mlx_audio_available(monkeypatch):
    """mlx-audio is Apple-Silicon-gated; force is_available()=True + a
    CPU-friendly host so the routing gate doesn't block these tests on
    non-mac CI runners."""
    from services import tts_backend as tts_mod
    monkeypatch.setattr(
        tts_mod.MLXAudioBackend, "is_available",
        classmethod(lambda cls: (True, "ready")),
    )
    _force_cpu_host(monkeypatch)


def test_select_mlx_audio_unknown_model_id_is_400(fresh_app, monkeypatch):
    _make_mlx_audio_available(monkeypatch)
    r = _client(fresh_app).post(
        "/engines/select",
        json={"family": "tts", "backend_id": "mlx-audio", "model_id": "not-a-real-model"},
    )
    assert r.status_code == 400
    assert "Unknown mlx-audio model" in r.json()["detail"]


def test_select_mlx_audio_curated_key_persists(fresh_app, monkeypatch):
    from core import prefs as _prefs
    _make_mlx_audio_available(monkeypatch)
    r = _client(fresh_app).post(
        "/engines/select",
        json={"family": "tts", "backend_id": "mlx-audio", "model_id": "outetts"},
    )
    assert r.status_code == 200, r.text
    assert _prefs.get("mlx_audio_model_id") == "outetts"
    assert _prefs.get("tts_backend") == "mlx-audio"


def test_select_mlx_audio_raw_repo_id_accepted(fresh_app, monkeypatch):
    """MLXAudioBackend already tolerates a raw HF repo id, not just a
    curated key (tts_backend.py ~733) — the API must too."""
    from core import prefs as _prefs
    _make_mlx_audio_available(monkeypatch)
    r = _client(fresh_app).post(
        "/engines/select",
        json={
            "family": "tts", "backend_id": "mlx-audio",
            "model_id": "mlx-community/Some-Other-Model-4bit",
        },
    )
    assert r.status_code == 200, r.text
    assert _prefs.get("mlx_audio_model_id") == "mlx-community/Some-Other-Model-4bit"


def test_select_mlx_audio_repo_id_accepts_underscore_prefixes(fresh_app, monkeypatch):
    _make_mlx_audio_available(monkeypatch)
    r = _client(fresh_app).post(
        "/engines/select",
        json={
            "family": "tts", "backend_id": "mlx-audio",
            "model_id": "_owner/_repo",
        },
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize(
    "model_id",
    [
        "owner/repo/extra",
        "-owner/repo",
        "owner/.repo",
        "owner/repo--name",
        "owner/repo..name",
        "owner/repo.",
        "owner/repo.git",
        f"owner/{'a' * 97}",
        "-" * 100_000,
    ],
    ids=lambda value: value if len(value) < 100 else f"oversized-{len(value)}",
)
def test_select_mlx_audio_rejects_malformed_repo_ids(fresh_app, monkeypatch, model_id):
    _make_mlx_audio_available(monkeypatch)
    r = _client(fresh_app).post(
        "/engines/select",
        json={"family": "tts", "backend_id": "mlx-audio", "model_id": model_id},
    )
    assert r.status_code == 400
    assert len(r.content) < 256
    assert model_id[:100] not in r.text


def test_hf_repo_id_size_bound_precedes_library_validation(fresh_app, monkeypatch):
    from api.routers import engines as engines_router

    def unexpected_validation(_value):
        raise AssertionError("oversized repo id reached the library validator")

    monkeypatch.setattr(
        engines_router.hf_utils,
        "validate_repo_id",
        unexpected_validation,
    )

    assert not engines_router._is_hf_repo_id("-" * 100_000)


def test_select_mlx_audio_without_model_id_does_not_touch_pref(fresh_app, monkeypatch):
    """Selecting mlx-audio without a model_id (e.g. an older frontend) must
    leave any existing mlx_audio_model_id pref untouched."""
    from core import prefs as _prefs
    _prefs.set_("mlx_audio_model_id", "csm")
    _make_mlx_audio_available(monkeypatch)
    r = _client(fresh_app).post(
        "/engines/select", json={"family": "tts", "backend_id": "mlx-audio"})
    assert r.status_code == 200, r.text
    assert _prefs.get("mlx_audio_model_id") == "csm"


def test_select_model_id_ignored_for_non_mlx_audio_backend(fresh_app):
    """model_id is only meaningful for mlx-audio; picking a different TTS
    backend with a model_id set must not persist a stray pref."""
    from core import prefs as _prefs
    r = _client(fresh_app).post(
        "/engines/select",
        json={"family": "tts", "backend_id": "omnivoice", "model_id": "kokoro"},
    )
    assert r.status_code == 200, r.text
    assert _prefs.get("mlx_audio_model_id") is None


def test_engines_response_curated_models_only_on_mlx_audio(fresh_app):
    client = _client(fresh_app)
    body = client.get("/engines").json()
    by_id = {b["id"]: b for b in body["tts"]["backends"]}
    assert "curated_models" in by_id["mlx-audio"]
    assert "active_model_id" in by_id["mlx-audio"]
    assert "curated_models" not in by_id["omnivoice"]
    assert "active_model_id" not in by_id["omnivoice"]


# ── /engines/{id}/health round-trip ────────────────────────────────────────


def test_engine_health_subprocess_success(fresh_app, monkeypatch):
    """Mock IndexTTS2Backend.health_check so we don't spawn a real sidecar."""
    from services.tts_backend import _REGISTRY

    # Resolve the lazy entry without spawning anything heavy.
    cls = _REGISTRY["indextts2"]
    monkeypatch.setattr(cls, "health_check", lambda self: (True, "pong"))

    client = _client(fresh_app)
    r = client.get("/engines/indextts2/health")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "indextts2"
    assert body["ok"] is True
    assert body["message"] == "Healthy"
    assert isinstance(body["latency_ms"], (int, float))
    assert body["latency_ms"] >= 0.0


def test_engine_health_in_process_falls_back_to_is_available(fresh_app):
    """No health_check method on OmniVoiceBackend → fall back to is_available."""
    client = _client(fresh_app)
    r = client.get("/engines/omnivoice/health")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "omnivoice"
    assert isinstance(body["ok"], bool)
    assert isinstance(body["message"], str)
    assert isinstance(body["latency_ms"], (int, float))


def test_engine_health_unknown_id(fresh_app):
    client = _client(fresh_app)
    r = client.get("/engines/does_not_exist/health")
    assert r.status_code == 404
    assert "unknown engine id" in r.json()["detail"]


def test_engine_health_resolves_managed_tts_sidecar(monkeypatch):
    """Test engine must probe the same effective class generation uses."""
    from api.routers import engines

    class InProcessAdapter:
        pass

    class ManagedSidecar:
        pass

    monkeypatch.setitem(engines.tts_backend._REGISTRY, "managed-test", InProcessAdapter)
    monkeypatch.setattr(
        engines.tts_backend,
        "get_backend_class",
        lambda engine_id: ManagedSidecar if engine_id == "managed-test" else None,
    )

    assert engines._resolve_engine_class("managed-test") is ManagedSidecar


def test_engine_health_is_admin_gated(fresh_app):
    """Non-loopback desktop traffic is rejected by require_admin."""
    client = _client(fresh_app, host="10.0.0.5")
    r = client.get("/engines/omnivoice/health")
    assert r.status_code == 403
    assert r.json()["detail"] == "loopback origin required"


def test_server_mode_engine_mutations_require_api_key(fresh_app, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    client = _client(fresh_app, host="172.17.0.1")

    requests = [
        ("post", "/engines/translation/deep-translator/install", None),
        ("delete", "/engines/translation/deep-translator", None),
        ("post", "/engines/sidecar/indextts2/install", None),
        ("delete", "/engines/sidecar/indextts2/install", None),
        ("post", "/engines/omnivoice/selftest", None),
        ("post", "/engines/select", {}),
    ]
    for method, path, body in requests:
        response = client.request(method.upper(), path, json=body)
        assert response.status_code == 403, path


def test_server_mode_engine_health_requires_api_key(fresh_app, monkeypatch):
    """Health is a GET but can spawn a sidecar, so it is not discovery."""
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    client = _client(fresh_app, host="172.17.0.1")

    response = client.get("/engines/omnivoice/health")

    assert response.status_code == 403


def test_server_mode_sidecar_install_stays_desktop_only(fresh_app, monkeypatch):
    """An API key cannot remotely trigger the mutable-source installer."""
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    client = _client(fresh_app, host="172.17.0.1")

    response = client.post(
        "/engines/sidecar/indextts2/install",
        headers={"authorization": "Bearer s3cret"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "desktop origin required"


def test_engine_health_caches_instance_across_calls(fresh_app, monkeypatch):
    """Two health checks on the same engine reuse the same singleton.

    SubprocessBackend.__init__ registers atexit hooks; recreating it per
    request would leak handler entries and (on real engines) spawn extra
    sidecars on the first lock acquire.
    """
    from api.routers import engines as engines_router
    from services.tts_backend import _REGISTRY

    cls = _REGISTRY["indextts2"]
    call_count = {"n": 0}
    monkeypatch.setattr(cls, "health_check", lambda self: (True, "pong"))
    # Clear the cache so the first call constructs an instance.
    engines_router._ENGINE_INSTANCES.pop(cls, None)
    original_init = cls.__init__

    def _counting_init(self):
        call_count["n"] += 1
        original_init(self)

    monkeypatch.setattr(cls, "__init__", _counting_init)

    client = _client(fresh_app)
    r1 = client.get("/engines/indextts2/health")
    r2 = client.get("/engines/indextts2/health")
    assert r1.status_code == 200 and r2.status_code == 200
    assert call_count["n"] == 1, (
        f"expected exactly one IndexTTS2Backend() construction across "
        f"two health checks, got {call_count['n']}"
    )


# ── /engines/{id}/selftest — real tiny synthesis (in-process TTS) ──────────


def _register_fake_tts(tts_mod, engine_id, *, available=True, samples=100,
                       raises=None, subprocess=False):
    """Register a fresh in-process (or subprocess-marked) TTS stub whose
    generate() returns a `samples`-long list — torch-free so the shape test
    stays light. Returns (cls, restore_fn)."""
    _samples, _avail, _raises = samples, available, raises

    class _Fake(tts_mod.TTSBackend):
        id = engine_id
        display_name = f"Fake {engine_id}"
        _is_subprocess_isolated = subprocess

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self):
            return ["en"]

        @classmethod
        def is_available(cls):
            return (True, "ready") if _avail else (False, "deps missing (test)")

        def generate(self, text, **kw):
            if _raises is not None:
                raise _raises
            return [0.0] * _samples

    saved = dict(tts_mod._REGISTRY)
    tts_mod._REGISTRY[engine_id] = _Fake

    def restore():
        tts_mod._REGISTRY.clear()
        tts_mod._REGISTRY.update(saved)

    return _Fake, restore


def test_selftest_in_process_success(fresh_app):
    from services import tts_backend as tts_mod

    _, restore = _register_fake_tts(tts_mod, "fake-inproc", samples=1200)
    try:
        r = _client(fresh_app).post("/engines/fake-inproc/selftest")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == "fake-inproc"
        assert body["ok"] is True
        assert body["num_samples"] == 1200
        assert body["sample_rate"] == 24000
        # 1200 / 24000 = 0.05 s of audio.
        assert body["audio_seconds"] == 0.05
        assert isinstance(body["duration_ms"], (int, float))
        assert body["timed_out"] is False
    finally:
        restore()


def test_selftest_rejects_subprocess_engine(fresh_app):
    from services import tts_backend as tts_mod

    _, restore = _register_fake_tts(tts_mod, "fake-sub", subprocess=True)
    try:
        r = _client(fresh_app).post("/engines/fake-sub/selftest")
        assert r.status_code == 400
        assert "subprocess-isolated" in r.json()["detail"]
    finally:
        restore()


def test_selftest_unavailable_engine_is_400(fresh_app):
    from services import tts_backend as tts_mod

    _, restore = _register_fake_tts(tts_mod, "fake-down", available=False)
    try:
        r = _client(fresh_app).post("/engines/fake-down/selftest")
        assert r.status_code == 400
        assert "not available" in r.json()["detail"]
    finally:
        restore()


def test_selftest_unknown_id_is_404(fresh_app):
    r = _client(fresh_app).post("/engines/nope-not-real/selftest")
    assert r.status_code == 404
    assert "unknown TTS engine id" in r.json()["detail"]


def test_selftest_is_admin_gated(fresh_app):
    r = _client(fresh_app, host="10.0.0.9").post("/engines/omnivoice/selftest")
    assert r.status_code == 403
    assert r.json()["detail"] == "loopback origin required"


def test_selftest_captures_synth_exception_without_500(fresh_app):
    from services import tts_backend as tts_mod

    _, restore = _register_fake_tts(
        tts_mod, "fake-boom", raises=RuntimeError("model exploded"))
    try:
        r = _client(fresh_app).post("/engines/fake-boom/selftest")
        assert r.status_code == 200, r.text  # never 500s on a synth failure
        body = r.json()
        assert body["ok"] is False
        assert "model exploded" in body["message"]
        assert body["num_samples"] is None
    finally:
        restore()


def test_no_hf_token_leak_in_selftest_response(fresh_app):
    """A synth exception carrying an HF token must be redacted in the body."""
    from services import tts_backend as tts_mod

    _, restore = _register_fake_tts(
        tts_mod, "fake-tainted",
        raises=RuntimeError(f"401 for {SAMPLE_HF_TOKEN}"))
    try:
        r = _client(fresh_app).post("/engines/fake-tainted/selftest")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert not HF_TOKEN_RE.search(body["message"])
        assert "hf_***REDACTED***" in body["message"]
    finally:
        restore()


def test_selftest_timeout_returns_timed_out(fresh_app, monkeypatch):
    """A synth that outruns the bounded timeout returns ok=False/timed_out —
    the panel never hangs. Pin the timeout tiny and block generate briefly."""
    import threading as _threading

    from api.routers import engines as engines_router
    from services import tts_backend as tts_mod

    monkeypatch.setattr(engines_router, "_selftest_timeout_s", lambda: 0.05)
    gate = _threading.Event()

    class _Slow(tts_mod.TTSBackend):
        id = "fake-slow"
        display_name = "Fake slow"

        @property
        def sample_rate(self):
            return 24000

        @property
        def supported_languages(self):
            return ["en"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw):
            gate.wait(2.0)  # outruns the 50 ms timeout; released in finally
            return [0.0] * 10

    saved = dict(tts_mod._REGISTRY)
    tts_mod._REGISTRY["fake-slow"] = _Slow
    try:
        r = _client(fresh_app).post("/engines/fake-slow/selftest")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert body["timed_out"] is True
        assert "timed out" in body["message"]
    finally:
        gate.set()  # let the orphaned worker finish and exit
        tts_mod._REGISTRY.clear()
        tts_mod._REGISTRY.update(saved)


# ── setup_snippet — copy-paste-ready env-var line for opt-in engines ────────


def test_setup_snippet_present_for_path_gated_engines(fresh_app):
    client = _client(fresh_app)
    by_id = {b["id"]: b for b in client.get("/engines").json()["tts"]["backends"]}
    # Every entry carries the key (None for engines with no path gate).
    for entry in by_id.values():
        assert "setup_snippet" in entry
    # IndexTTS-2 is path-gated → exact export line, single-sourced in the backend.
    assert by_id["indextts2"]["setup_snippet"] == (
        "export OMNIVOICE_INDEXTTS_DIR=/path/to/index-tts"
    )
    # A bundled engine has no path gate → null.
    assert by_id["omnivoice"]["setup_snippet"] is None


# ── HF-token leak prevention (T-02-12) ─────────────────────────────────────


def test_no_hf_token_leak_in_engines_response(fresh_app):
    """A backend whose is_available() embeds a real HF token in its error
    must NOT leak it to the response body. The redaction lives inside
    ``tts_backend.list_backends`` via _mask_hf_tokens.
    """
    from services import tts_backend as tts_mod

    class TaintedBackend(tts_mod.TTSBackend):
        id = "tainted-test"
        display_name = "Tainted backend (test)"

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["en"]

        @classmethod
        def is_available(cls) -> tuple[bool, str]:
            return False, f"auth failed for {SAMPLE_HF_TOKEN}"

        def generate(self, text: str, **kw):
            raise NotImplementedError

    # Sandbox so the production registry shape doesn't grow permanently.
    saved = dict(tts_mod._REGISTRY)
    saved_errors = dict(tts_mod._LAST_ERRORS)
    try:
        tts_mod._REGISTRY["tainted-test"] = TaintedBackend
        client = _client(fresh_app)
        r = client.get("/engines")
        assert r.status_code == 200
        body_text = r.text
        matches = HF_TOKEN_RE.findall(body_text)
        assert matches == [], (
            f"HF tokens leaked into /engines response body: {matches}"
        )

        # The boundary emits stable metadata rather than a partially scrubbed
        # service diagnostic.
        by_id = {b["id"]: b for b in r.json()["tts"]["backends"]}
        assert "tainted-test" in by_id
        assert by_id["tainted-test"]["reason"] == (
            "Engine unavailable. Check installation and configuration."
        )
    finally:
        tts_mod._REGISTRY.clear()
        tts_mod._REGISTRY.update(saved)
        tts_mod._LAST_ERRORS.clear()
        tts_mod._LAST_ERRORS.update(saved_errors)


def test_no_hf_token_leak_in_health_response(fresh_app, monkeypatch):
    """The health route's message field runs through the same redactor."""
    from services.tts_backend import _REGISTRY

    cls = _REGISTRY["indextts2"]
    monkeypatch.setattr(
        cls, "health_check",
        lambda self: (False, f"sidecar 401 for {SAMPLE_HF_TOKEN}"),
    )

    client = _client(fresh_app)
    r = client.get("/engines/indextts2/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert not HF_TOKEN_RE.search(body["message"])
    assert body["message"] == "Engine unavailable; check the backend log for details."
