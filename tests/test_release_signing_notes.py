"""Release notes describe the signing each platform leg measured (v0.5.7-beta.1 follow-up)."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_signing_notes", ROOT / "scripts/release_signing_notes.py")
notes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notes)


def write(tmp_path, platform, arch, status):
    (tmp_path / f"signing-{platform}-{arch}.json").write_text(
        json.dumps({"platform": platform, "arch": arch, "status": status})
    )


def test_table_lists_each_platform_with_its_measured_status(tmp_path):
    write(tmp_path, "linux", "x64", "not-applicable")
    write(tmp_path, "win32", "x64", "unsigned")
    write(tmp_path, "darwin", "arm64", "notarized")
    write(tmp_path, "darwin", "x64", "adhoc")
    text = notes.render(tmp_path)
    rows = [line for line in text.splitlines() if line.startswith("| ") and "Platform" not in line]
    assert [row.split(" | ")[0] for row in rows] == [
        "| macOS (Apple Silicon)", "| macOS (Intel)", "| Windows x64", "| Linux x64",
    ]
    assert "✅ Signed with Developer ID and notarized" in rows[0]
    assert rows[1].count("⚠️") == 1 and "shell installer" in rows[1]
    assert "SmartScreen" in rows[2]
    assert "### Installer signing" in text


@pytest.mark.parametrize("platform,arch,status", [("darwin", "arm64", "signed-ish"), ("plan9", "x64", "signed")])
def test_unknown_reports_fail_instead_of_rendering(tmp_path, platform, arch, status):
    write(tmp_path, platform, arch, status)
    with pytest.raises(ValueError):
        notes.render(tmp_path)


def test_missing_reports_fail(tmp_path):
    with pytest.raises(ValueError):
        notes.render(tmp_path)


def load_workflow():
    return yaml.load((ROOT / ".github/workflows/electron-release.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_every_package_leg_reports_and_mac_check_ignores_allow_unsigned():
    steps = {step.get("name"): step for step in load_workflow()["jobs"]["package"]["steps"]}
    mac = steps["Report and enforce macOS signing"]
    # Runs on every macOS build, published or not, and allow_unsigned cannot skip it.
    assert mac["if"] == "matrix.platform == 'darwin'"
    assert 'codesign --verify --deep --strict "$APP"' in mac["run"]
    assert mac["env"]["HAS_CREDENTIALS"] == "${{ secrets.ELECTRON_MACOS_CSC_LINK != '' }}"
    windows = steps["Report and enforce Windows signing"]
    assert windows["if"] == "matrix.platform == 'win32'"
    assert windows["env"]["HAS_CREDENTIALS"] == "${{ secrets.ELECTRON_WINDOWS_CSC_LINK != '' }}"
    assert steps["Report Linux signing"]["if"] == "matrix.platform == 'linux'"
    assert steps["Save the signing report"]["with"]["name"] == "electron-signing-${{ matrix.platform }}-${{ matrix.arch }}"


def test_mac_package_discovers_the_imported_developer_id(tmp_path):
    # electron-builder's findIdentity returns null without a lookup when discovery is
    # off and no identity is named, so a supplied certificate was silently ignored.
    import os
    import subprocess
    package = next(s for s in load_workflow()["jobs"]["package"]["steps"] if s.get("name") == "Package without publishing")
    script = package["run"].split("set -o pipefail")[0].replace("${{ matrix.platform }}", "darwin")
    for link, expected in [("base64-p12", "true"), ("", "false")]:
        env = dict(os.environ, CSC_LINK=link, CSC_KEY_PASSWORD="x", CSC_IDENTITY_AUTO_DISCOVERY="false", PUBLISH="false")
        out = subprocess.run(["bash", "-e", "-c", script + 'echo "$CSC_IDENTITY_AUTO_DISCOVERY"'],
                             env=env, capture_output=True, text=True, check=True).stdout.strip()
        assert out == expected, link


def test_release_notes_are_built_from_the_reports():
    steps = load_workflow()["jobs"]["release"]["steps"]
    describe = next(step for step in steps if step.get("name") == "Describe installer signing per platform")
    assert "if" not in describe
    assert not any("Electron installer trust" in step.get("run", "") for step in steps)


def test_release_notes_helper_comes_from_the_workflow_commit(tmp_path):
    # Rebuilding an older tag from main checks out a tag without the helper (#3241 review).
    import os
    import subprocess
    steps = load_workflow()["jobs"]["release"]["steps"]
    describe = next(step for step in steps if step.get("name") == "Describe installer signing per platform")
    assert "python3 scripts/" not in describe["run"]
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "gh").write_text(
        '#!/bin/sh\ncase "$*" in *"contents/scripts/release_signing_notes.py?ref=workflow-sha"*) '
        f'cat "{ROOT / "scripts/release_signing_notes.py"}";; *) exit 1;; esac\n'
    )
    (fake / "gh").chmod(0o755)
    work = tmp_path / "tag-checkout"
    (work / "release-signing").mkdir(parents=True)
    (work / "release-assets").mkdir()
    (work / "release-assets/RELEASE_NOTES.md").write_text("**Highlights**\n")
    write(work / "release-signing", "linux", "x64", "not-applicable")
    env = dict(os.environ, PATH=f"{fake}{os.pathsep}{os.environ['PATH']}", GITHUB_SHA="workflow-sha",
               GITHUB_REPOSITORY="debpalash/VoiceStudio", RUNNER_TEMP=str(tmp_path))
    subprocess.run(["bash", "-e", "-c", describe["run"]], cwd=work, env=env, check=True)
    text = (work / "release-assets/RELEASE_NOTES.md").read_text(encoding="utf-8")
    # The table leads the notes; the changelog follows unchanged.
    assert "| Linux x64 |" in text
    assert text.index("### Installer signing") < text.index("**Highlights**")


@pytest.mark.parametrize("failures,message,calls,ok", [
    (1, "Failed to staple your application with code: 68", 2, True),
    (2, "Failed to staple your application with code: 68", 2, False),
    (1, "codesign failed", 1, False),
])
def test_package_builds_again_only_when_stapling_fails(tmp_path, failures, message, calls, ok):
    # A dropped connection to Apple's ticket server failed beta.2 after notarization.
    import os
    import subprocess
    package = next(s for s in load_workflow()["jobs"]["package"]["steps"] if s.get("name") == "Package without publishing")
    script = package["run"].split('if [ "$RUNNER_OS" = Linux ]')[0]
    script = script.replace("${{ matrix.platform }}", "darwin").replace("${{ matrix.flags }}", "--mac")
    fake = tmp_path / "bin"
    fake.mkdir()
    count = tmp_path / "calls"
    (fake / "bun").write_text(
        f'#!/bin/sh\necho x >> "{count}"\n'
        f'[ "$(wc -l < "{count}")" -gt {failures} ] && exit 0\necho "{message}"; exit 1\n'
    )
    (fake / "bun").chmod(0o755)
    env = dict(os.environ, PATH=f"{fake}{os.pathsep}{os.environ['PATH']}", RUNNER_TEMP=str(tmp_path),
               CSC_LINK="", PUBLISH="false")
    result = subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, text=True)
    assert (result.returncode == 0) is ok, result.stdout + result.stderr
    assert len(count.read_text(encoding="utf-8").splitlines()) == calls
