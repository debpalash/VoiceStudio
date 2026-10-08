"""Tests for GET /setup/preflight — the first-run system health probe.

Mocks subprocess calls (nvidia-smi / rocm-smi), platform detection, and
network + torch imports so the endpoint shape + branching logic is verified
without needing a specific hardware configuration.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_prefs(monkeypatch, tmp_path):
    """Preflight now caches the endpoint-race decision in prefs — keep each
    test's writes out of the session-shared prefs.json. Also shed any
    endpoint env vars another suite may have leaked (defense in depth: a
    leaked HF_ENDPOINT flips every network check into the explicit branch)."""
    import os as _os
    from core import prefs
    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    for k in ("HF_ENDPOINT", "OMNIVOICE_HF_ENDPOINT_MODE"):
        if k in _os.environ:
            monkeypatch.delenv(k)


# ── Shape ────────────────────────────────────────────────────────────────

def test_preflight_returns_expected_shape(client):
    """Endpoint always returns {ok, has_warnings, checks[], device}."""
    r = client.get("/setup/preflight")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) >= {"ok", "has_warnings", "checks", "device"}
    assert isinstance(body["ok"], bool)
    assert isinstance(body["has_warnings"], bool)
    assert isinstance(body["checks"], list)
    assert isinstance(body["device"], dict)


def test_preflight_every_check_has_required_fields(client):
    """Each check entry must carry id/label/status/detail/fix."""
    body = client.get("/setup/preflight").json()
    for c in body["checks"]:
        assert set(c.keys()) >= {"id", "label", "status", "detail", "fix"}
        assert c["status"] in {"pass", "warn", "fail"}


def test_preflight_always_probes_core_checks(client):
    """The fixed set of checks should always be present — users need a
    consistent list regardless of platform. Genuine user facts only."""
    body = client.get("/setup/preflight").json()
    ids = {c["id"] for c in body["checks"]}
    required_ids = {
        "os", "python", "ram", "disk", "hf_cache_writable",
        "gpu", "network",
    }
    assert required_ids.issubset(ids), f"missing: {required_ids - ids}"


def test_preflight_never_lists_media_tools_as_requirements(client):
    """ffmpeg / ffprobe / yt-dlp are internal dependencies the app provisions
    for itself — they must NOT appear as system-requirement check rows (the
    old model told users to `brew install ffmpeg`)."""
    body = client.get("/setup/preflight").json()
    ids = {c["id"] for c in body["checks"]}
    assert not ids & {"ffmpeg", "ffprobe", "yt-dlp"}, ids
    joined = " ".join(f"{c['detail']} {c.get('fix') or ''}" for c in body["checks"])
    assert "brew install ffmpeg" not in joined
    assert "yt-dlp" not in joined


def test_preflight_carries_media_tools_verdict(client):
    """The wizard's quiet progress line / failure card reads a top-level
    media_tools verdict: {ready, acquire:{state, progress, error}}."""
    body = client.get("/setup/preflight").json()
    media = body.get("media_tools")
    assert media is not None
    assert isinstance(media["ready"], bool)
    assert media["acquire"]["state"] in {"idle", "running", "done", "error"}


def test_preflight_kicks_background_acquisition_when_unresolved():
    """No tier resolves → preflight itself starts the bundled download (the
    first-run self-heal) instead of telling the user to install anything."""
    import services.media_tools as mt

    calls = []
    with patch.object(mt, "status", return_value={
        "ready": False, "tools": {},
        "ops": {"acquire": {"state": "idle", "progress": 0.0, "error": None},
                "ytdlp_update": {"state": "idle"}},
        "platform_key": "test",
    }), patch.object(mt, "acquire_bundled",
                     side_effect=lambda wait=False: calls.append(1) or
                     {"state": "running", "progress": 0.0, "error": None}):
        body = client_factory().get("/setup/preflight").json()

    assert calls, "preflight must trigger acquire_bundled when unresolved"
    assert body["media_tools"] == {
        "ready": False,
        "acquire": {"state": "running", "progress": 0.0, "error": None},
    }
    # And the media engine never blocks the Continue gate.
    checks_fail = any(c["status"] == "fail" for c in body["checks"])
    assert body["ok"] is (not checks_fail)


def test_preflight_does_not_retrigger_after_failed_acquisition():
    """After a failed download the wizard's failure card owns Retry —
    a preflight recheck must not silently re-fire the download."""
    import services.media_tools as mt

    with patch.object(mt, "status", return_value={
        "ready": False, "tools": {},
        "ops": {"acquire": {"state": "error", "progress": 0.0,
                            "error": "download checksum mismatch"},
                "ytdlp_update": {"state": "idle"}},
        "platform_key": "test",
    }), patch.object(mt, "acquire_bundled") as fired:
        body = client_factory().get("/setup/preflight").json()

    fired.assert_not_called()
    assert body["media_tools"]["acquire"]["state"] == "error"
    assert "checksum" in body["media_tools"]["acquire"]["error"]


def test_preflight_device_summary(client):
    """device block must include os/arch/gpu_vendor/gpu_backend/ram_gb."""
    body = client.get("/setup/preflight").json()
    d = body["device"]
    assert set(d.keys()) >= {
        "os", "arch", "gpu_vendor", "gpu_backend", "gpu_available",
        "gpu_driver", "gpu_device_name", "ram_gb", "disk_free_gb",
    }
    assert d["gpu_backend"] in {"cuda", "rocm", "mps", "cpu"}
    assert d["gpu_vendor"] in {"nvidia", "amd", "apple", "intel", "unknown", "none"}
    # #21: canonical-probe family + VRAM joined the device summary.
    assert d["gpu_family"] in {"cuda", "rocm", "mps", "xpu", "npu", "cpu"}
    assert isinstance(d["vram_gb"], (int, float))


def test_preflight_includes_active_engine_routing(client):
    """#21: preflight surfaces a routing verdict for the active TTS engine
    (no silent CPU fallback) — both a `gpu_routing` object and a check entry."""
    body = client.get("/setup/preflight").json()
    assert "gpu_routing" in body
    gr = body["gpu_routing"]
    if gr is not None:
        assert gr["routing_status"] in {
            "accelerated", "cpu_fallback", "cpu_only", "unavailable", "none",
        }
        assert "host_family" in gr
        ids = {c["id"] for c in body["checks"]}
        assert "gpu_routing" in ids


# ── Aggregation logic ────────────────────────────────────────────────────

def test_preflight_ok_false_when_any_fail(client):
    """If any check is fail, aggregate ok must be false."""
    body = client.get("/setup/preflight").json()
    any_fail = any(c["status"] == "fail" for c in body["checks"])
    assert body["ok"] is (not any_fail)


def test_preflight_has_warnings_matches_checks(client):
    body = client.get("/setup/preflight").json()
    any_warn = any(c["status"] == "warn" for c in body["checks"])
    assert body["has_warnings"] is any_warn


# ── GPU vendor detection branches ────────────────────────────────────────

def test_preflight_detects_apple_silicon():
    """On mac-ARM, vendor → 'apple' and backend → 'mps'."""
    if sys.platform != "darwin":
        pytest.skip("apple-silicon branch only exercisable on darwin")
    from api.routers.setup.wizard import _detect_gpu
    info = _detect_gpu()
    # mac-Intel CI hosts also hit darwin; only assert vendor if arch matches.
    import platform as _p
    if _p.machine() == "arm64":
        assert info["vendor"] == "apple"
        assert info["backend"] == "mps"


def test_preflight_handles_missing_nvidia_smi():
    """When nvidia-smi is absent, vendor falls through (not nvidia)."""
    from api.routers.setup.wizard import _detect_gpu, _run_cmd  # noqa
    with patch("api.routers.setup.wizard._run_cmd", return_value=(-1, "")):
        info = _detect_gpu()
        # On mac-ARM the apple branch returns before _run_cmd; skip that case.
        import platform as _p
        if sys.platform != "darwin" or _p.machine() != "arm64":
            assert info["vendor"] != "nvidia"


def test_preflight_nvidia_driver_below_min_flags_fail():
    """An old NVIDIA driver must produce status='fail' with a driver-update fix."""
    import platform as _p
    if sys.platform == "darwin" and _p.machine() == "arm64":
        pytest.skip("apple-silicon branch returns before nvidia-smi — not reachable")
    from api.routers.setup import wizard as setup_mod

    def fake_run_cmd(args, timeout=2.0):
        if args and args[0] == "nvidia-smi":
            return 0, "520.61.05, NVIDIA GeForce RTX 3090\n"
        return -1, ""

    with patch.object(setup_mod, "_run_cmd", side_effect=fake_run_cmd):
        info = setup_mod._detect_gpu()

    assert info["vendor"] == "nvidia"
    assert info["available"] is False
    assert any("driver" in n.lower() for n in info["notes"])


@pytest.mark.parametrize(
    "platform_, driver, flagged",
    [
        ("linux", "524.99", True),
        ("linux", "525.60.13", False),
        ("linux", "550.54.14", False),
        ("win32", "527.56", True),   # 527 < 528.33 Windows floor (#2489)
        ("win32", "528.33", False),
        ("win32", "551.23", False),
    ],
)
def test_nvidia_driver_floor_is_cuda12_minor_compat(monkeypatch, platform_, driver, flagged):
    """525 (Linux) / 528.33 (Windows) is enough for the bundled CUDA 12.8 runtime."""
    from api.routers.setup import wizard as setup_mod

    monkeypatch.setattr(
        setup_mod, "_min_nvidia_driver",
        lambda platform=None, _f=setup_mod._min_nvidia_driver: _f(platform_),
    )

    def fake_run_cmd(args, timeout=2.0):
        if args and args[0] == "nvidia-smi":
            return 0, f"{driver}, NVIDIA GeForce RTX 3090\n"
        return -1, ""

    monkeypatch.setattr(setup_mod, "_run_cmd", fake_run_cmd)
    import platform as _p
    if sys.platform == "darwin" and _p.machine() == "arm64":
        pytest.skip("apple-silicon branch returns before nvidia-smi")
    info = setup_mod._detect_gpu()
    assert info["vendor"] == "nvidia"
    assert any("driver" in n.lower() for n in info["notes"]) is flagged


def test_preflight_amd_flags_warn_when_no_rocm_torch():
    """AMD GPU + torch without HIP → warn with ROCm install instructions."""
    import platform as _p
    if sys.platform == "darwin" and _p.machine() == "arm64":
        pytest.skip("apple-silicon branch returns before rocm-smi")
    from api.routers.setup import wizard as setup_mod

    def fake_run_cmd(args, timeout=2.0):
        if args and args[0] == "rocm-smi":
            return 0, "GPU[0]: Card series: AMD Radeon RX 7900 XTX\n"
        return -1, ""

    with patch.object(setup_mod, "_run_cmd", side_effect=fake_run_cmd):
        info = setup_mod._detect_gpu()

    assert info["vendor"] == "amd"
    # The bundled CUDA torch has no .version.hip → must be flagged
    if info["backend"] != "rocm":
        assert any("rocm" in n.lower() for n in info["notes"])


@pytest.mark.parametrize("platform_, expect", [
    ("win32", "audio.cpp"),
    ("linux", "OMNIVOICE_TORCH_VARIANT=rocm"),
])
def test_preflight_sees_a_radeon_without_rocm_smi(monkeypatch, platform_, expect):
    """Windows ships no rocm-smi: the old probe reported "No compatible GPU" for
    an RX 9070 XT. The OS adapter inventory must still find it, and the advice
    must name an option that exists on THAT OS."""
    import platform as _p
    if sys.platform == "darwin" and _p.machine() == "arm64":
        pytest.skip("apple-silicon branch returns before the AMD probe")
    from core import gpu_inventory
    from api.routers.setup import wizard as setup_mod
    from types import SimpleNamespace

    monkeypatch.setattr(setup_mod, "_run_cmd", lambda args, timeout=2.0: (-1, ""))
    monkeypatch.setattr(
        gpu_inventory, "detect_host_gpus",
        lambda: (gpu_inventory.HostGPU("amd", "AMD Radeon RX 9070 XT", 16.0),),
    )
    monkeypatch.setattr(setup_mod.sys, "platform", platform_)
    cuda_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
        version=SimpleNamespace(hip=None, cuda="12.8"),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
    )
    with patch.dict("sys.modules", {"torch": cuda_torch}):
        info = setup_mod._detect_gpu()

    assert info["vendor"] == "amd"
    assert info["device_name"] == "AMD Radeon RX 9070 XT"
    assert info["available"] is False
    note = " ".join(info["notes"])
    assert expect in note
    assert "rocm6.1" not in note  # the old, wrong advice


# ── Docker / container GPU fallback ──────────────────────────────────────

def test_preflight_docker_gpu_fallback_detects_cuda():
    """When nvidia-smi is absent but torch.cuda works (Docker container),
    vendor → 'unknown', backend → 'cuda', available → True, and
    device_name is populated from torch.cuda.get_device_name()."""
    import platform as _p
    if sys.platform == "darwin" and _p.machine() == "arm64":
        pytest.skip("apple-silicon branch returns before fallback")
    from api.routers.setup import wizard as setup_mod
    from types import SimpleNamespace

    def fake_run_cmd(args, timeout=2.0):
        # Neither nvidia-smi nor rocm-smi available
        return -1, ""

    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: True,
            get_device_name=lambda idx: "NVIDIA GeForce RTX 4070 Laptop GPU",
        ),
        version=SimpleNamespace(hip=None),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
    )

    with patch.object(setup_mod, "_run_cmd", side_effect=fake_run_cmd), \
         patch.dict("sys.modules", {"torch": fake_torch}):
        info = setup_mod._detect_gpu()

    assert info["vendor"] == "unknown"
    assert info["backend"] == "cuda"
    assert info["available"] is True
    assert info["device_name"] == "NVIDIA GeForce RTX 4070 Laptop GPU"


def test_preflight_docker_gpu_fallback_shows_pass_status():
    """The preflight GPU check should show status='pass' when the Docker
    fallback detects CUDA, not the old 'No compatible GPU' warning."""
    import platform as _p
    if sys.platform == "darwin" and _p.machine() == "arm64":
        pytest.skip("apple-silicon branch returns before fallback")
    from api.routers.setup import wizard as setup_mod
    from types import SimpleNamespace

    def fake_run_cmd(args, timeout=2.0):
        return -1, ""

    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: True,
            get_device_name=lambda idx: "NVIDIA GeForce RTX 4070 Laptop GPU",
        ),
        version=SimpleNamespace(hip=None),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
    )

    with patch.object(setup_mod, "_run_cmd", side_effect=fake_run_cmd), \
         patch.dict("sys.modules", {"torch": fake_torch}):
        r = client_factory().get("/setup/preflight").json()

    gpu = next(c for c in r["checks"] if c["id"] == "gpu")
    assert gpu["status"] == "pass", f"Expected 'pass' but got '{gpu['status']}': {gpu['detail']}"
    assert "CUDA ready" in gpu["detail"]
    assert r["device"]["gpu_available"] is True
    assert r["device"]["gpu_backend"] == "cuda"


def test_preflight_no_gpu_at_all_shows_warn():
    """When no GPU tools or torch.cuda, should warn (not fail)."""
    import platform as _p
    if sys.platform == "darwin" and _p.machine() == "arm64":
        pytest.skip("apple-silicon branch returns before fallback")
    from api.routers.setup import wizard as setup_mod
    from types import SimpleNamespace

    def fake_run_cmd(args, timeout=2.0):
        return -1, ""

    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: False,
            get_device_name=lambda idx: "",
        ),
        version=SimpleNamespace(hip=None),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
    )

    with patch.object(setup_mod, "_run_cmd", side_effect=fake_run_cmd), \
         patch.dict("sys.modules", {"torch": fake_torch}):
        info = setup_mod._detect_gpu()

    assert info["available"] is False
    assert info["backend"] == "cpu"


# ── Network probe ────────────────────────────────────────────────────────

def test_preflight_network_handles_offline():
    """_probe_network must gracefully return False on connection error."""
    from api.routers.setup.wizard import _probe_network
    # Deliberately unreachable host:port
    assert _probe_network(host="10.255.255.1", timeout=0.3) is False


def _patch_race_probe(latencies):
    """Patch the endpoint race's prober from {endpoint: latency|None}."""
    import services.endpoint_race as er

    def fake_probe(endpoint, timeout=None):
        lat = latencies.get(endpoint)
        if lat is None:
            return er.ProbeResult(endpoint=endpoint, reachable=False, error="timeout")
        return er.ProbeResult(endpoint=endpoint, reachable=True, latency_ms=lat)

    return patch.object(er, "probe_endpoint", fake_probe)


def test_preflight_network_auto_pass_on_canonical():
    """No explicit endpoint → preflight races both endpoints; a reachable
    huggingface.co wins and the check passes naming it."""
    import services.endpoint_race as er

    with _patch_race_probe({er.CANONICAL_ENDPOINT: 50, er.COMMUNITY_MIRROR: 80}):
        body = client_factory().get("/setup/preflight").json()

    net = next(c for c in body["checks"] if c["id"] == "network")
    assert net["status"] == "pass"
    assert "huggingface.co" in net["label"]
    assert net.get("endpoint") == er.CANONICAL_ENDPOINT


def test_preflight_network_unreachable_is_warn_not_blocker():
    """A dead network must NOT hard-block the wizard (restricted-network
    first-run, e.g. China where huggingface.co is blocked): the check is a
    warning and the aggregate `ok` is unaffected by it."""
    import services.endpoint_race as er

    with _patch_race_probe({er.CANONICAL_ENDPOINT: None, er.COMMUNITY_MIRROR: None}):
        body = client_factory().get("/setup/preflight").json()

    net = next(c for c in body["checks"] if c["id"] == "network")
    assert net["status"] == "warn", net
    assert "continue" in (net["fix"] or "").lower()
    # ok must still equal "no fail among checks" — network can't be the fail.
    any_fail = any(c["status"] == "fail" for c in body["checks"])
    assert body["ok"] is (not any_fail)


def test_preflight_network_probes_configured_mirror():
    """With HF_ENDPOINT set (explicit choice → manual mode, no auto race),
    the probe targets the mirror host — not the hardcoded official host that
    may be blocked on the user's network."""
    import os
    from api.routers.setup import wizard as setup_mod

    seen_hosts: list[str] = []

    def fake_probe(host="huggingface.co", port=443, timeout=2.0):
        seen_hosts.append(host)
        return True

    with patch.dict(os.environ, {"HF_ENDPOINT": "https://mirror.example.test"}), \
         patch.object(setup_mod, "_probe_network", side_effect=fake_probe):
        body = client_factory().get("/setup/preflight").json()

    net = next(c for c in body["checks"] if c["id"] == "network")
    assert "mirror.example.test" in net["label"]
    assert net["status"] == "pass"
    assert "mirror.example.test" in seen_hosts


def test_preflight_network_auto_selects_reachable_mirror():
    """Official endpoint blocked but hf-mirror.com reachable → with no
    explicit endpoint configured the race picks the mirror automatically, the
    check PASSES (downloads will work — no dead-end, no manual switch), and
    the copy states the outcome honestly."""
    import services.endpoint_race as er

    with _patch_race_probe({er.CANONICAL_ENDPOINT: None, er.COMMUNITY_MIRROR: 90}):
        body = client_factory().get("/setup/preflight").json()

    net = next(c for c in body["checks"] if c["id"] == "network")
    assert net["status"] == "pass"
    assert net.get("endpoint") == er.COMMUNITY_MIRROR
    assert net.get("mirror_reachable") is True
    assert "huggingface.co is unreachable" in net["detail"]
    assert "hf-mirror.com" in net["detail"]
    # The winning endpoint is cached for the actual model downloads.
    assert er.effective_endpoint() == er.COMMUNITY_MIRROR


def test_preflight_network_explicit_setting_never_raced(monkeypatch):
    """An explicit endpoint (Settings / HF_ENDPOINT) is never auto-switched:
    preflight must not race, even when the explicit endpoint is down."""
    import os
    import services.endpoint_race as er
    from api.routers.setup import wizard as setup_mod

    def boom(endpoint, timeout=None):
        raise AssertionError("explicit endpoint configured — race must not run")

    with patch.dict(os.environ, {"HF_ENDPOINT": "https://mirror.example.test"}), \
         patch.object(er, "probe_endpoint", boom), \
         patch.object(setup_mod, "_probe_network", return_value=False):
        body = client_factory().get("/setup/preflight").json()

    net = next(c for c in body["checks"] if c["id"] == "network")
    assert net["status"] == "warn"
    assert "mirror.example.test" in (net["fix"] or "")


# ── RAM thresholds ───────────────────────────────────────────────────────

def test_preflight_ram_fail_threshold():
    """Below _RAM_FAIL_GB → fail status in the RAM check."""
    from api.routers.setup import wizard as setup_mod

    with patch.object(setup_mod, "_ram_gb", return_value=4.0):
        r = client_factory().get("/setup/preflight").json()
    ram = next(c for c in r["checks"] if c["id"] == "ram")
    assert ram["status"] == "fail"


def test_preflight_ram_warn_threshold():
    """Between fail and warn thresholds → warn."""
    from api.routers.setup import wizard as setup_mod

    with patch.object(setup_mod, "_ram_gb", return_value=10.0):
        r = client_factory().get("/setup/preflight").json()
    ram = next(c for c in r["checks"] if c["id"] == "ram")
    assert ram["status"] == "warn"


# ── Helpers ──────────────────────────────────────────────────────────────

def client_factory():
    """Per-test TestClient; avoids module-scoped fixture collisions with
    ``patch()`` context managers."""
    from main import app
    return TestClient(app)


@pytest.mark.parametrize("vendor,available,expected", [
    ("none", False, "warn"),
    ("nvidia", False, "warn"),
    ("nvidia", True, "pass"),
])
def test_optional_gpu_never_blocks_cpu_setup(monkeypatch, tmp_path, vendor, available, expected):
    from api.routers.setup import wizard
    from services import media_tools
    from types import SimpleNamespace

    monkeypatch.setattr(wizard.sys, "platform", "linux")
    monkeypatch.setattr(wizard._platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(wizard, "_ram_gb", lambda: 32)
    monkeypatch.setattr(wizard, "_disk_free_gb", lambda _: 100)
    monkeypatch.setattr(wizard, "hf_cache_dir", lambda: str(tmp_path / "hf"))
    monkeypatch.setattr(wizard, "_network_check", lambda: {
        "id": "network", "label": "Network", "status": "pass", "detail": "Test", "fix": None,
    })
    monkeypatch.setattr(wizard, "_detect_gpu", lambda: {
        "vendor": vendor, "available": available, "backend": "cuda" if available else "cpu",
        "driver": "test", "device_name": "Test device", "notes": [],
    })
    monkeypatch.setattr(media_tools, "summary", lambda **_: {"ready": True})
    monkeypatch.setitem(sys.modules, "services.tts_backend", SimpleNamespace(gpu_routing_verdict=lambda: {
        "engine": "omnivoice", "routing_status": "accelerated" if available else "cpu_only",
        "effective_device": "cuda" if available else "cpu", "host_family": "cuda" if available else "cpu",
    }))
    result = wizard.preflight()
    gpu = next(check for check in result["checks"] if check["id"] == "gpu")
    assert gpu["status"] == expected
    assert result["ok"] is True
    if vendor == "nvidia" and not available:
        assert "CPU-only" in gpu["fix"]
        assert "GPU-only engines remain unavailable" in gpu["fix"]
