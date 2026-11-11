"""Guards for the iOS CI test harness: loopback hosts start fast, hangs fail fast and by name."""
import importlib.util
import io
import re
import shutil
import sys
import time
from pathlib import Path

import pytest
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


def _ios_step(name):
    workflow = yaml.safe_load((ROOT / ".github/workflows/mobile.yml").read_text(encoding="utf-8"))
    return next(step for step in workflow["jobs"]["ios"]["steps"] if step.get("name") == name)


def test_kit_tests_stream_per_test_output_and_fail_a_silent_hang_by_name():
    # Through a plain pipe the output stayed block-buffered: a hung run printed nothing
    # for 15 minutes and never named the test.
    step = _ios_step("VoiceStudioKit tests")
    run = step["run"]
    assert run.startswith("python3 ../scripts/test_watchdog.py --stall ")
    assert "swift test --no-parallel --skip-build" in run
    stall = int(re.search(r"--stall (\d+)", run).group(1))
    assert 0 < stall < step["timeout-minutes"] * 60
    # Building in its own step keeps compile and link time out of the stall window.
    build = _ios_step("Build VoiceStudioKit tests")
    assert build["run"] == "swift build --build-tests"
    assert build["working-directory"] == step["working-directory"]


def test_kit_tests_never_wait_for_a_child_process_without_a_deadline():
    # Process.waitUntilExit() lost its run-loop wakeup on a Swift concurrency thread and
    # blocked forever after the loopback host had exited.
    helper = ROOT / "mobile/ios/VoiceStudioKit/Tests/VoiceStudioKitTests/TestProcess.swift"
    assert "terminationHandler" in helper.read_text(encoding="utf-8")
    for path in (ROOT / "mobile/ios/VoiceStudioKit/Tests").rglob("*.swift"):
        if path == helper:
            continue
        text = path.read_text(encoding="utf-8")
        assert "waitUntilExit" not in text, path.relative_to(ROOT)
        assert not re.search(r"\.read\(upToCount:", text), path.relative_to(ROOT)


def _watchdog():
    pytest.importorskip("pty")  # POSIX only, like the macOS runner it serves
    spec = importlib.util.spec_from_file_location("test_watchdog", ROOT / "mobile/ios/scripts/test_watchdog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_watchdog_streams_output_and_keeps_the_exit_status():
    out = io.BytesIO()
    script = "import sys; print('Test fast() started.'); sys.exit(3)"
    assert _watchdog().run([sys.executable, "-c", script], stall=30, out=out) == 3
    assert b"Test fast() started." in out.getvalue()


def test_watchdog_kills_a_silent_run_and_names_the_last_started_test():
    out = io.BytesIO()
    script = "import time; print('\\x1b[90mTest stuck() started.\\x1b[0m', flush=True); time.sleep(60)"
    started = time.monotonic()
    assert _watchdog().run([sys.executable, "-c", script], stall=1, out=out) == 124
    assert time.monotonic() - started < 30
    assert b"hung after: Test stuck() started" in out.getvalue()
    if shutil.which("sample"):
        # Stacks, or an explicit per-pid failure line when `sample` cannot attach.
        assert b"===== stacks of pid" in out.getvalue() or re.search(rb"\(sample \d+ failed", out.getvalue())


def test_watchdog_still_times_out_a_child_that_closed_its_output():
    # EOF on the terminal is not exit: a child can close stdout and stderr and then hang.
    out = io.BytesIO()
    script = "import os, time; print('Test closed() started.', flush=True); os.close(1); os.close(2); time.sleep(60)"
    started = time.monotonic()
    assert _watchdog().run([sys.executable, "-c", script], stall=1, out=out) == 124
    assert time.monotonic() - started < 30
    assert b"hung after: Test closed() started" in out.getvalue()


def test_watchdog_stack_sampling_reports_failures_and_keeps_to_its_budget(monkeypatch):
    watchdog = _watchdog()
    monkeypatch.setattr(watchdog.shutil, "which", lambda name: "/usr/bin/sample")

    class Done:
        returncode, stderr = 1, b"sample: process not found"

    calls = []
    monkeypatch.setattr(watchdog.subprocess, "run", lambda *a, **kw: calls.append(kw["timeout"]) or Done())
    out = io.BytesIO()
    # A failed sample is reported as a failure, never as an empty stack section.
    watchdog.dump_stacks([11], out)
    assert b"sample 11 failed: exit 1: sample: process not found" in out.getvalue()
    assert b"===== stacks" not in out.getvalue()

    # Once the total budget is spent, later processes are skipped, not sampled.
    ticks = iter([0.0, 0.0, 95.0])
    out = io.BytesIO()
    calls.clear()
    watchdog.dump_stacks([21, 22], out, budget=90.0, clock=lambda: next(ticks))
    assert calls == [60] and b"skipped pid 22" in out.getvalue()
