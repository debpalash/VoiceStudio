"""scripts/bench (#3924): statistics, import-time parsing, run isolation and the bundle budget."""
from __future__ import annotations

import importlib.util
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("vs_bench", ROOT / "scripts/bench/bench.py")
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)
BUNDLE = ROOT / "scripts/bench/bundle.mjs"


def test_percentiles_interpolate():
    assert bench.percentile([3, 1, 2], 50) == 2
    assert bench.percentile([1, 2, 3, 4], 50) == 2.5
    assert bench.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90) == pytest.approx(9.1)
    assert bench.summarize([5.0]) == {"median": 5.0, "p90": 5.0, "min": 5.0, "max": 5.0, "n": 1}


def test_importtime_keeps_outermost_imports_by_cumulative_time():
    log = "\n".join([
        "import time: self [us] | cumulative | imported package",
        "import time:       100 |        100 |     torch._C",
        "import time:      2000 |    1200000 | torch",
        "import time:       300 |     300000 |   transformers.utils",
        "import time:       500 |     500000 | fastapi",
        "noise from the app",
    ])
    assert bench.parse_importtime(log) == [
        {"module": "torch", "cumulative_ms": 1200.0},
        {"module": "fastapi", "cumulative_ms": 500.0},
    ]


def test_backend_runs_are_isolated_and_offline(monkeypatch, tmp_path):
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", "/users/real/data")
    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy:8080")
    monkeypatch.setenv("VOICESTUDIO_CLOUD_ENV", "prod")
    env = bench.isolated_env(str(tmp_path))
    assert env["OMNIVOICE_DATA_DIR"] == str(tmp_path)
    assert env["OMNIVOICE_ENV_FILE"].startswith(str(tmp_path))
    assert "VOICESTUDIO_CLOUD_ENV" not in env
    for key in ("HF_HOME", "HF_HUB_CACHE", "TORCH_HOME", "OMNIVOICE_CACHE_DIR"):
        assert env[key].startswith(str(tmp_path)), key
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == env["OMNIVOICE_ANALYTICS_DISABLED"] == "1"
    # Any accidental outbound HTTP hits a dead local port; loopback stays direct.
    assert env["HTTPS_PROXY"] == env["https_proxy"] == "http://127.0.0.1:9"
    assert "127.0.0.1" in env["NO_PROXY"]


def _renderer(tmp_path: Path, entry_bytes: int) -> Path:
    assets = tmp_path / "renderer" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    # Incompressible content, so gzip size tracks raw size.
    (assets / "index-AbCd1234.js").write_bytes(random.Random(0).randbytes(entry_bytes))
    (assets / "lazy-page-ZyXw9876.js").write_text("export const x = 1;\n" * 50)
    (assets / "index-Qq11Ww22.css").write_text("body{color:red}\n" * 20)
    (tmp_path / "renderer" / "index.html").write_text(
        '<script type="module" src="./assets/index-AbCd1234.js"></script>'
        '<link rel="stylesheet" href="./assets/index-Qq11Ww22.css">'
    )
    return tmp_path / "renderer"


def _bundle(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", str(BUNDLE), *args], capture_output=True, text=True, cwd=ROOT)


def test_bundle_report_separates_startup_and_lazy_chunks(tmp_path):
    out = _bundle("--json", "--dir", str(_renderer(tmp_path, 64_000)))
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    assert result["summary"]["eager_files"] == 2
    assert {c["name"]: c["eager"] for c in result["top10"]} == {
        "index.js": True, "lazy-page.js": False, "index.css": True,
    }


def test_bundle_check_fails_only_on_large_growth(tmp_path):
    baseline = tmp_path / "baseline.json"
    dist = _renderer(tmp_path, 64_000)
    assert _bundle("--dir", str(dist), "--baseline", str(baseline), "--write-baseline").returncode == 0
    _renderer(tmp_path, 64_000 + 3_000)  # ~5% growth: within budget
    assert _bundle("--dir", str(dist), "--baseline", str(baseline), "--check").returncode == 0
    _renderer(tmp_path, 64_000 + 12_800)  # ~20% growth: over budget
    failed = _bundle("--dir", str(dist), "--baseline", str(baseline), "--check")
    assert failed.returncode == 1
    assert "eager_gzip" in failed.stderr


def test_recorded_bundle_baseline_has_every_checked_metric():
    baseline = json.loads((ROOT / "scripts/bench/bundle-baseline.json").read_text(encoding="utf-8"))
    for key in ("eager_gzip", "js_gzip", "css_gzip", "largest_eager_gzip"):
        assert baseline[key] > 0, key


def test_loopback_checks_ignore_proxy_settings(monkeypatch):
    """Readiness checks talk to 127.0.0.1 directly, never via HTTP(S)_PROXY."""
    import importlib.util
    import urllib.request
    from pathlib import Path

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("http_proxy", "http://proxy.invalid:3128")
    spec = importlib.util.spec_from_file_location(
        "bench_proxy_check", Path(__file__).resolve().parents[1] / "scripts/bench/bench.py"
    )
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    # The default opener would add a ProxyHandler built from HTTP_PROXY; the
    # loopback opener must route nothing through a proxy.
    assert urllib.request.build_opener().handlers  # sanity: default opener has handlers
    assert not any(
        isinstance(h, urllib.request.ProxyHandler) and h.proxies for h in bench._LOCAL_OPENER.handlers
    )
    assert any(
        isinstance(h, urllib.request.ProxyHandler) and h.proxies.get("http")
        for h in urllib.request.build_opener().handlers
    )  # with HTTP_PROXY set, the default opener WOULD proxy


def test_app_launch_gets_a_throwaway_home(tmp_path):
    """A packaged app links its terminal command into HOME; the bench must not touch the real one."""
    script = (
        "import { launchEnv } from './scripts/bench/launch_env.mjs';"
        "const env = launchEnv({ HOME: '/real', USERPROFILE: 'C:/real', XDG_CONFIG_HOME: '/real/.config',"
        " XDG_RUNTIME_DIR: '/run/user/1', HTTPS_PROXY: 'http://p', OMNIVOICE_DATA_DIR: '/real/data' },"
        f" {{ port: 3900, profile: {json.dumps(str(tmp_path))} }});"
        "console.log(JSON.stringify(env));"
    )
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    env = json.loads(out.stdout)
    home = tmp_path / "home"
    for key in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        assert Path(env[key]).is_relative_to(home), key
        assert Path(env[key]).is_dir(), key
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1"  # display/audio sockets stay reachable
    assert "HTTPS_PROXY" not in env and "OMNIVOICE_DATA_DIR" not in env
    assert env["OMNIVOICE_PORT"] == "3900" and env["VOICESTUDIO_DISABLE_UPDATER"] == "1"


@pytest.mark.parametrize("window", [0, 0.001])
def test_idle_reports_a_sample_for_a_zero_window(monkeypatch, window):
    monkeypatch.setattr(bench, "tree_rss_mb", lambda pid: 120.0)
    monkeypatch.setattr(bench, "tree_cpu_seconds", lambda pid: 0.0)
    monkeypatch.setattr(bench, "_tree", lambda pid: [pid])
    backend = object.__new__(bench.Backend)
    backend.proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        result = backend.idle(settle=0, window=window)
    finally:
        backend.proc.kill()
        backend.proc.wait()
    assert result == {"rss_mb": 120.0, "cpu_percent": 0.0, "children": 0}
