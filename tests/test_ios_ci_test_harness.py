"""Guards for the iOS CI test harness: loopback hosts start fast, UI tests cannot eat the job."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_embedded_python_http_servers_skip_reverse_dns_on_bind():
    # HTTPServer.server_bind calls socket.getfqdn(); on the macOS runners every
    # loopback host then took ~35 s to print its port, ~95% of the Swift test step.
    hosts = [
        path
        for path in (ROOT / "mobile/ios").rglob("*.swift")
        if "/build/" not in path.as_posix() and re.search(r"HTTPServer\(\(", path.read_text(encoding="utf-8"))
    ]
    assert hosts, "expected the VoiceStudioKit loopback test hosts"
    for path in hosts:
        assert "socket.getfqdn = " in path.read_text(encoding="utf-8"), path.relative_to(ROOT)


def _ui_smoke_step():
    workflow = yaml.safe_load((ROOT / ".github/workflows/mobile.yml").read_text(encoding="utf-8"))
    return next(step for step in workflow["jobs"]["ios"]["steps"] if step.get("name") == "UI smoke test")


def test_ui_smoke_bounds_each_test_instead_of_rerunning_the_suite():
    step = _ui_smoke_step()
    # A retry re-ran all UI tests (~30 min), pushing the job to its 90 minute limit.
    assert "-retry-tests-on-failure" not in step["run"]
    assert "-test-iterations" not in step["run"]
    assert "-test-timeouts-enabled YES" in step["run"]
    assert "-default-test-execution-time-allowance" in step["run"]
    assert 0 < step["timeout-minutes"] < 90
