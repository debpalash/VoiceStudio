"""Pull requests package the desktop app the way the release does, minus installers.

v0.5.8-beta.4's packaging broke only at tag time: no pull request built the bundled
CLI inside a packaged app (macOS kept a link into a deleted build folder; the Linux
public entry rejected the switches the packaged smoke test passes). ci.yml's
release smoke builds the unpacked app with electron-builder and runs the release's
packaged-app checks on the release's own Linux and macOS runners.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


CI = _workflow("ci.yml")
JOB = CI["jobs"]["release-smoke"]
RELEASE = _workflow("electron-release.yml")["jobs"]["package"]


def _runs(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def _legs(job: dict) -> dict[str, dict]:
    return {leg["platform"]: leg for leg in job["strategy"]["matrix"]["include"]}


def test_release_smoke_uses_the_release_runners_and_targets():
    legs = _legs(JOB)
    release = {leg["target"]: leg for leg in RELEASE["strategy"]["matrix"]["include"]}
    assert set(legs) == {"linux", "darwin"}
    for leg in legs.values():
        # Jammy on Linux: the glibc floor of the Snap base the .deb is repackaged onto.
        shipped = release[leg["target"]]
        assert (leg["runner"], leg["platform"], leg["arch"], leg["flags"]) == (
            shipped["runner"], shipped["platform"], shipped["arch"], shipped["flags"])
    assert JOB["env"]["VOICESTUDIO_RUST_TARGET"] == "${{ matrix.target }}"
    assert JOB["env"]["CSC_IDENTITY_AUTO_DISCOVERY"] == "false"
    assert JOB["timeout-minutes"] <= 20


def _mentions_secrets(value) -> bool:
    """Whether a workflow fragment reads the `secrets` context in any form."""
    return re.search(r"\bsecrets\b", yaml.safe_dump(value), re.IGNORECASE) is not None


def test_release_smoke_signs_ad_hoc_and_never_sees_a_certificate():
    # electron-builder skips signing on pull requests unless told otherwise; the
    # macOS bundle then keeps Electron's stale signature and fails `codesign --verify`.
    assert JOB["env"]["CSC_FOR_PULL_REQUEST"] == "true"
    # ...which is only safe because no signing material can reach the job: neither
    # its own definition nor the workflow-level env it inherits may read the
    # `secrets` context in any form (secrets.X, secrets['X'], toJSON(secrets)),
    # and it has no environment (whose secrets would otherwise be in scope).
    assert "environment" not in JOB and "secrets" not in JOB
    inherited = CI.get("env") or {}
    for scope, value in (("job", JOB), ("workflow env", inherited)):
        text = yaml.safe_dump(value)
        assert not _mentions_secrets(value), scope
        for name in ("CSC_LINK", "CSC_KEY_PASSWORD", "CSC_NAME", "WIN_CSC_LINK", "APPLE_"):
            assert name not in text, (scope, name)
    assert set(inherited) == {"FORCE_JAVASCRIPT_ACTIONS_TO_NODE24"}


def test_the_secret_scan_catches_every_reference_form():
    for expression in ("${{ secrets.CSC_LINK }}", "${{ secrets['CSC_LINK'] }}", '${{secrets [ "X" ]}}',
                       "${{ toJSON(secrets) }}", "${{ toJson( secrets ) }}", "${{ SECRETS.X }}"):
        assert _mentions_secrets({"env": {"X": expression}}), expression
    assert not _mentions_secrets({"env": {"X": "${{ github.sha }}"}})


def test_release_smoke_is_gated_like_the_other_electron_jobs():
    assert JOB["needs"] == "changes"
    assert JOB["if"] == CI["jobs"]["electron-ui"]["if"]
    assert "outputs.electron != 'false'" in JOB["if"] and "outputs.runtime != 'false'" in JOB["if"]
    assert "release-smoke" in CI["jobs"]["test"]["needs"]


def test_release_smoke_packages_the_unpacked_app_and_runs_the_release_checks():
    runs = _runs(JOB)
    release_runs = _runs(RELEASE)
    # electron-builder with the release config and flags, unpacked, never published.
    assert ("bun x electron-builder --config electron-builder.config.mjs ${{ matrix.flags }} --dir --publish never"
            in runs)
    # The release's own checks, unchanged.
    for check in ("node tests/packaging-contract.mjs --artifact",
                  "python3 scripts/check_glibc_floor.py electron/release/linux-unpacked/resources/native",
                  "codesign --verify --deep --strict \"$APP\""):
        assert check in runs and check in release_runs, check
    assert "node tests/packaged-smoke.mjs --setup" in runs
    # Linux starts the public entry (the bundled CLI), as the release's AppImage does.
    assert ("VOICESTUDIO_PACKAGED_EXE=electron/release/linux-unpacked/voicestudio xvfb-run -a "
            "node tests/packaged-smoke.mjs --setup") in runs
    assert "node electron/tests/cli-bundle-smoke.mjs" in runs
    # Builds the inputs electron-builder packages: the app and the web UI.
    assert "bun run --cwd electron build\n" in runs and "bun run --cwd electron build:web" in runs


def test_the_bundled_uv_is_staged_by_one_script_everywhere():
    script = "electron/scripts/stage-bundled-uv.mjs"
    assert (ROOT / script).is_file()
    assert f"node {script}" in _runs(JOB)
    assert f"node {script}" in _runs(_workflow("electron-build.yml")["jobs"]["package"])
    # The release may rebuild an older tag, so it loads the helper from its own commit.
    release = _runs(RELEASE)
    assert f"contents/{script}?ref=$GITHUB_SHA" in release
    assert 'node "$RUNNER_TEMP/stage-bundled-uv.mjs"' in release
    for job in (JOB, RELEASE, _workflow("electron-build.yml")["jobs"]["package"]):
        assert "<<'NODE'" not in _runs(job)


def test_each_desktop_os_runs_the_cli_bundle_smoke_exactly_once():
    smoke = "node electron/tests/cli-bundle-smoke.mjs"
    desktop = next(s for s in CI["jobs"]["electron-ui"]["steps"] if smoke in s.get("run", ""))
    assert "runner.os == 'Windows'" in desktop["if"]
    assert {leg["label"] for leg in CI["jobs"]["electron-ui"]["strategy"]["matrix"]["include"]} == {
        "macOS", "Windows", "Linux"}
    assert {leg["label"] for leg in _legs(JOB).values()} == {"macOS", "Linux"}
