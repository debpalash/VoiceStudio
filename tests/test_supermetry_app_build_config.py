"""Build metadata is private, credential-free by default, and runtime-independent."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("intake_build", ROOT / "scripts/generate_supermetry_config.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def node_config(env):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for Electron build configuration")
    result = subprocess.run([node, "--input-type=module", "-e", "import {generatedSupermetryConfig} from './config/supermetry-app.mjs';process.stdout.write(JSON.stringify(generatedSupermetryConfig(JSON.parse(process.env.INTAKE_TEST_ENV))));"],
        cwd=ROOT, env={**os.environ, "INTAKE_TEST_ENV": json.dumps(env)}, capture_output=True, text=True, check=True)
    assert not result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("host,accepted", [
    ("https://app.supermetry.com", True), ("https://collector.example:8443/", True),
    ("http://localhost:7777", True), ("http://127.0.0.1:7777", True), ("http://[::1]:7777", True),
    ("http://127.1", False), ("http://2130706433", False), ("http://localhost.", False),
    ("http://collector.example", False), ("https://collector.example/path", False),
    ("https://key@collector.example", False), ("https://collector.example?key=value", False),
    ("https://collector.example#secret", False), ("https://collector.example:invalid", False),
    ("", False),
])
def test_python_and_electron_build_metadata_agree_on_transport_policy(host, accepted):
    env = {"SUPERMETRY_APP_ID": "fixtureapp123", "SUPERMETRY_INGEST_KEY": "smk_" + "a" * 39, "SUPERMETRY_APP_HOST": host}
    python = build.generated_config(env)
    assert python == node_config(env)
    assert ("host" in python) is accepted
    assert python["diagnostics"]["sessionPings"] is False


@pytest.mark.parametrize("env", [{}, {"SUPERMETRY_APP_ID": "short"}, {"SUPERMETRY_INGEST_KEY": "smk_" + "0" * 39}, {"SUPERMETRY_APP_ID": "UPPERCASE", "SUPERMETRY_INGEST_KEY": "wrong"}])
def test_missing_or_invalid_credentials_are_absent_without_error(env):
    assert build.generated_config(env) == node_config(env) == {"diagnostics": {"sessionPings": False}}


def test_build_flag_is_explicit_and_defaults_off():
    for value in ("", "0", "true", "yes", "1"):
        env = {"SUPERMETRY_DIAGNOSTICS_SESSION_PINGS": value}
        assert build.generated_config(env) == node_config(env) == {"diagnostics": {"sessionPings": value == "1"}}


def test_cli_never_prints_key_and_credential_free_rebuild_erases_old_metadata(tmp_path):
    output = tmp_path / "supermetry-app.generated.json"
    env = {key: value for key, value in os.environ.items() if not key.startswith("SUPERMETRY_")}
    key = "smk_" + "b" * 39
    with_key = subprocess.run([shutil.which("python3") or "python", str(ROOT / "scripts/generate_supermetry_config.py"), "--output", str(output)], env={**env, "SUPERMETRY_APP_ID": "fixtureapp123", "SUPERMETRY_INGEST_KEY": key}, capture_output=True, text=True, check=True)
    assert not with_key.stdout and not with_key.stderr
    assert json.loads(output.read_text())["ingest_key"] == key
    build.write_config(output, {})
    assert json.loads(output.read_text()) == {"diagnostics": {"sessionPings": False}}
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o600


def test_generated_config_is_git_ignored_and_excluded_from_web_and_preload_defines():
    ignored = subprocess.run(["git", "check-ignore", "config/supermetry-app.generated.json"], cwd=ROOT, capture_output=True, text=True)
    assert ignored.returncode == 0
    assert "config/supermetry-app.generated.json" in (ROOT / ".dockerignore").read_text().splitlines()
    source = (ROOT / "electron/electron.vite.config.ts").read_text()
    shared = source.split("const define = {", 1)[1].split("};", 1)[0]
    assert "SUPERMETRY" not in shared
    assert "__SUPERMETRY_APP_CONFIG__" in source.split("main: {", 1)[1].split("preload: {", 1)[0]


def test_release_artifact_jobs_receive_only_build_time_intake_credentials():
    import yaml
    jobs = {"electron-release.yml": ["package"], "electron-build.yml": ["package"],
        "mobile-release.yml": ["android", "ios"], "ios-testflight.yml": ["testflight"],
        "android-play-internal.yml": ["play-internal"], "mac-app-store.yml": ["package"],
        "microsoft-store.yml": ["package"], "snap-store.yml": ["package"]}
    for filename, names in jobs.items():
        workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
        for name in names:
            env = workflow["jobs"][name]["env"]
            assert env["SUPERMETRY_APP_ID"] == "${{ secrets.SUPERMETRY_APP_ID }}"
            assert env["SUPERMETRY_INGEST_KEY"] == "${{ secrets.SUPERMETRY_INGEST_KEY }}"
            assert "vars.SUPERMETRY_APP_HOST" in env["SUPERMETRY_APP_HOST"]
            assert "SUPERMETRY_DIAGNOSTICS_SESSION_PINGS" not in env
