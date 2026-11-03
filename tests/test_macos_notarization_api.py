"""Notarization must use a complete API credential and clean up its private key."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = {"APPLE_API_KEY": "/tmp/test-notary.p8", "APPLE_API_KEY_ID": "TESTKEY123",
       "APPLE_API_ISSUER": "test-issuer"}
CERT = {"CSC_LINK": "test-certificate", "CSC_KEY_PASSWORD": "test-password"}


def clean_env():
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("APPLE_", "CSC_", "VOICESTUDIO_DISTRIBUTION"))}


@pytest.mark.parametrize("missing", [None, *API, "CSC_LINK", "CSC_KEY_PASSWORD"])
def test_api_key_enables_hardened_notarized_build_without_apple_password(missing):
    credentials = {**CERT, **API}
    if missing:
        credentials.pop(missing)
    result = subprocess.run(
        ["node", "--input-type=module", "-e",
         "import c from './electron/electron-builder.config.mjs'; "
         "console.log(JSON.stringify([c.mac.notarize, c.mac.hardenedRuntime]))"],
        cwd=ROOT, env={**clean_env(), **credentials}, text=True, capture_output=True, check=True,
    )
    assert json.loads(result.stdout) == [missing is None, missing is None]


@pytest.mark.skipif(os.name == "nt", reason="Bash key preparation runs on the macOS packaging runner")
@pytest.mark.parametrize("exit_code", [0, 7])
def test_ci_key_is_private_and_removed_after_packaging(tmp_path, exit_code):
    key = "-----BEGIN PRIVATE KEY-----\nTEST ONLY\n-----END PRIVATE KEY-----\n"
    probe = """
source scripts/prepare-apple-notarization.sh || exit
python3 - <<'CHECK'
import os, pathlib
p = pathlib.Path(os.environ['APPLE_API_KEY'])
assert p.read_text() == os.environ['EXPECTED_TEST_KEY']
assert p.stat().st_mode & 0o777 == 0o600
assert p.parent.stat().st_mode & 0o777 == 0o700
assert 'APPLE_ID' not in os.environ
assert 'APPLE_APP_SPECIFIC_PASSWORD' not in os.environ
CHECK
exit "$TEST_EXIT"
"""
    result = subprocess.run(["bash", "-e", "-c", probe], cwd=ROOT,
        env={**clean_env(), **API, "APPLE_API_KEY": key, "EXPECTED_TEST_KEY": key,
             "RUNNER_TEMP": str(tmp_path), "TEST_EXIT": str(exit_code),
             "APPLE_ID": "old-account", "APPLE_APP_SPECIFIC_PASSWORD": "old-password"},
        text=True, capture_output=True)
    assert result.returncode == exit_code, result.stderr
    assert key not in result.stdout + result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name == "nt", reason="Bash key preparation runs on the macOS packaging runner")
@pytest.mark.parametrize("missing", list(API))
def test_partial_api_credentials_are_refused_before_packaging(tmp_path, missing):
    credentials = {**API, "APPLE_API_KEY": "TEST PRIVATE KEY"}
    credentials.pop(missing)
    result = subprocess.run(["bash", "-e", "-c", "source scripts/prepare-apple-notarization.sh"],
        cwd=ROOT, env={**clean_env(), **credentials, "RUNNER_TEMP": str(tmp_path)},
        text=True, capture_output=True)
    assert result.returncode != 0
    assert "TEST PRIVATE KEY" not in result.stdout + result.stderr
    assert not list(tmp_path.iterdir())


def test_release_workflow_uses_api_secrets_and_prepares_a_key_file():
    import yaml
    workflow = ROOT / ".github/workflows/electron-release.yml"
    jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    steps = [step for job in jobs.values() for step in job.get("steps", [])]
    packaging = next(step for step in steps if "CSC_LINK" in step.get("env", {}))
    for name in API:
        assert f"secrets.{name}" in packaging["env"][name]
    assert "APPLE_ID" not in packaging["env"]
    assert "APPLE_APP_SPECIFIC_PASSWORD" not in packaging["env"]
    assert packaging["working-directory"] == "electron"
    assert 'source "$RUNNER_TEMP/prepare-apple-notarization.sh"' in packaging["run"]


@pytest.mark.skipif(os.name == "nt", reason="Exercises the macOS packaging shell")
def test_old_tag_packaging_uses_workflow_helper_and_overrides_legacy_config(tmp_path):
    import yaml
    workflow = yaml.safe_load((ROOT / ".github/workflows/electron-release.yml").read_text(encoding="utf-8"))
    steps = [s for job in workflow["jobs"].values() for s in job.get("steps", [])]
    load = next(s for s in steps if s.get("name") == "Load notarization helper from workflow")
    package = next(s for s in steps if "CSC_LINK" in s.get("env", {}))
    # Simulate a checked-out historical tag: no new helper, password-based defaults.
    checkout = tmp_path / "old-tag" / "electron"
    checkout.mkdir(parents=True)
    (checkout / "package.json").write_text('{"type":"module"}')
    (checkout / "electron-builder.config.mjs").write_text(
        'export default {mac:{notarize:false,hardenedRuntime:false}};')
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "gh").write_text(
        '#!/bin/bash\n[[ "$*" == *"?ref=workflow-sha"* ]] || exit 1\n'
        'cat "$HELPER_SOURCE"\n')
    (fake / "bun").write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$CAPTURE_ARGS"\n')
    for file in fake.iterdir():
        file.chmod(0o755)
    runner = tmp_path / "runner"
    runner.mkdir()
    args_path = tmp_path / "args.txt"
    env = {**clean_env(), **CERT, **API, "APPLE_API_KEY": "TEST PRIVATE KEY",
           "PATH": str(fake) + os.pathsep + os.environ["PATH"],
           "RUNNER_TEMP": str(runner), "RUNNER_OS": "macOS", "GITHUB_SHA": "workflow-sha",
           "GITHUB_REPOSITORY": "test/repo", "PUBLISH": "true", "ALLOW_UNSIGNED": "false",
           "HELPER_SOURCE": str(ROOT / "scripts/prepare-apple-notarization.sh"),
           "CAPTURE_ARGS": str(args_path)}
    subprocess.run(["bash", "-e", "-c", load["run"]], cwd=checkout, env=env, check=True)
    script = package["run"].split("node tests/packaging-contract.mjs")[0]
    script = script.replace("${{ matrix.platform }}", "darwin").replace("${{ matrix.flags }}", "--mac --arm64")
    subprocess.run(["bash", "-e", "-c", script], cwd=checkout, env=env, check=True)
    args = args_path.read_text(encoding="utf-8").splitlines()[2:]  # bun x electron-builder
    assert "--config.mac.notarize=true" in args
    assert "--config.mac.hardenedRuntime=true" in args
    assert not list(runner.glob("apple-notary.*"))
    # Exercise the pinned builder's real CLI normalization and schema coercion.
    js = """
const { createRequire } = await import('node:module');
const { realpathSync } = await import('node:fs');
const require = createRequire(realpathSync(process.env.BUILDER_PACKAGE));
const builder = require('./out/builder.js');
const { getConfig, validateConfiguration } = require('app-builder-lib/out/util/config/config.js');
const options = builder.normalizeOptions(builder.configureBuildCommand(builder.createYargs()).parse(JSON.parse(process.env.TEST_ARGS)));
const config = await getConfig(process.cwd(), null, options.config);
await validateConfiguration(config, {isEnabled:false});
if (config.mac.notarize !== true || config.mac.hardenedRuntime !== true) throw new Error('Legacy config was not overridden');
"""
    subprocess.run(["node", "--input-type=module", "-e", js], cwd=checkout,
        env={**clean_env(), "BUILDER_PACKAGE": str(ROOT / "electron/node_modules/electron-builder/package.json"),
             "TEST_ARGS": json.dumps(args)}, check=True, capture_output=True, text=True)
