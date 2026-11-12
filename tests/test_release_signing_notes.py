"""Release notes describe the signing each platform leg measured (v0.5.7-beta.1 follow-up)."""

import importlib.util
import json
import re
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
        "| Windows x64", "| macOS Apple Silicon", "| macOS Intel", "| Linux x64", "| Android", "| iPhone",
    ]
    assert "SmartScreen" in rows[0]
    assert "✅ Signed with Developer ID and notarized" in rows[1]
    assert rows[2].count("⚠️") == 1 and "shell installer" in rows[2]
    assert "Signed APK" in rows[4] and "own Apple ID" in rows[5]
    assert "### Download" in text and "| Platform | Download | Signing |" in text


def test_one_table_links_every_download_present_and_marks_missing_ones(tmp_path):
    """Owner, 2026-11-12: downloads, signing and phones merge into one table that opens the notes."""
    reports, assets = tmp_path / "reports", tmp_path / "assets"
    reports.mkdir()
    assets.mkdir()
    for platform, arch, status in [("win32", "x64", "unsigned"), ("win32", "arm64", "unsigned"),
                                   ("darwin", "arm64", "notarized"), ("darwin", "x64", "notarized"),
                                   ("linux", "x64", "not-applicable")]:
        write(reports, platform, arch, status)
    for name in ["win-x64.exe", "win-x64.exe.blockmap", "win-arm64.exe", "mac-arm64.dmg", "mac-x64.dmg",
                 "linux-x64.AppImage", "android.apk", "android.aab", "ios-unsigned.ipa"]:
        (assets / f"VoiceStudio-1.2.3-{name}").write_text("x")
    base = "https://github.com/o/r/releases/download/v1.2.3"
    text = notes.render(reports, assets=assets, base_url=base)
    rows = {line.split(" | ")[0][2:]: line for line in text.splitlines() if line.startswith("| ") and "Platform" not in line}
    assert list(rows) == ["Windows x64", "Windows ARM64 (experimental)", "macOS Apple Silicon", "macOS Intel",
                          "Linux x64", "Android", "iPhone"]
    assert f"[Installer]({base}/VoiceStudio-1.2.3-win-x64.exe) |" in rows["Windows x64"]
    assert f"[DMG]({base}/VoiceStudio-1.2.3-mac-x64.dmg)" in rows["macOS Intel"]
    # The .deb is missing: only the AppImage is linked, never a dead link.
    assert f"[AppImage]({base}/VoiceStudio-1.2.3-linux-x64.AppImage) |" in rows["Linux x64"] and "deb" not in rows["Linux x64"]
    assert f"[APK]({base}/VoiceStudio-1.2.3-android.apk) · [AAB]({base}/VoiceStudio-1.2.3-android.aab)" in rows["Android"]
    assert f"[Unsigned IPA]({base}/VoiceStudio-1.2.3-ios-unsigned.ipa)" in rows["iPhone"]
    assert "Installer signing" not in text and "Phone downloads" not in text


def test_a_platform_with_no_file_shows_a_dash(tmp_path):
    write(tmp_path, "linux", "x64", "not-applicable")
    rows = [line for line in notes.render(tmp_path).splitlines() if line.startswith("| Linux x64")]
    assert rows == ["| Linux x64 | — | — Linux packages are not code-signed |"]


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
    assert windows["env"]["HAS_CREDENTIALS"] == "${{ secrets.ELECTRON_WINDOWS_CSC_LINK != '' || env.SIGNPATH_ENABLED == 'true' }}"
    assert steps["Report Linux signing"]["if"] == "matrix.platform == 'linux'"
    assert steps["Save the signing report"]["with"]["name"] == "electron-signing-${{ matrix.platform }}-${{ matrix.arch }}"


def test_mac_package_discovers_the_imported_developer_id(tmp_path):
    import shutil
    shutil.copy(ROOT / "scripts/prepare-apple-notarization.sh", tmp_path)
    # electron-builder's findIdentity returns null without a lookup when discovery is
    # off and no identity is named, so a supplied certificate was silently ignored.
    import os
    import subprocess
    package = next(s for s in load_workflow()["jobs"]["package"]["steps"] if s.get("name") == "Package without publishing")
    script = package["run"].split("set -o pipefail")[0].replace("${{ matrix.platform }}", "darwin")
    for link, expected in [("base64-p12", "true"), ("", "false")]:
        env = dict(os.environ, CSC_LINK=link, CSC_KEY_PASSWORD="x", CSC_IDENTITY_AUTO_DISCOVERY="false", PUBLISH="false", RUNNER_TEMP=str(tmp_path))
        out = subprocess.run(["bash", "-e", "-c", script + 'echo "$CSC_IDENTITY_AUTO_DISCOVERY"'],
                             cwd=ROOT / "electron", env=env, capture_output=True, text=True, check=True).stdout.strip()
        assert out == expected, link


def test_release_notes_are_built_from_the_reports():
    steps = load_workflow()["jobs"]["release"]["steps"]
    describe = next(step for step in steps if step.get("name") == "Describe downloads and signing per platform")
    assert "if" not in describe
    assert not any("Electron installer trust" in step.get("run", "") for step in steps)


def test_release_notes_helper_comes_from_the_workflow_commit(tmp_path):
    # Rebuilding an older tag from main checks out a tag without the helper (#3241 review).
    import os
    import subprocess
    steps = load_workflow()["jobs"]["release"]["steps"]
    describe = next(step for step in steps if step.get("name") == "Describe downloads and signing per platform")
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
    (work / "release-assets/RELEASE_NOTES.md").write_text(
        "**Download**\n\n| Platform | Installer |\n| --- | --- |\n| Linux x64 | [AppImage](https://e.test/a) |\n\n**Highlights**\n"
    )
    write(work / "release-signing", "linux", "x64", "not-applicable")
    env = dict(os.environ, PATH=f"{fake}{os.pathsep}{os.environ['PATH']}", GITHUB_SHA="workflow-sha",
               GITHUB_REPOSITORY="debpalash/VoiceStudio", RUNNER_TEMP=str(tmp_path))
    subprocess.run(["bash", "-e", "-c", describe["run"]], cwd=work, env=env, check=True)
    text = (work / "release-assets/RELEASE_NOTES.md").read_text(encoding="utf-8")
    # The table leads the notes; the changelog follows unchanged.
    assert "| Linux x64 |" in text and "| iPhone |" in text
    assert text.index("### Download") < text.index("**Highlights**")
    assert "releases/download/v" not in text  # no assets in this checkout: no links
    assert text.count("| Linux x64 |") == 1 and "| Platform | Installer |" not in text


@pytest.mark.parametrize("failures,message,calls,ok", [
    (1, "Failed to staple your application with code: 68", 2, True),
    # v0.5.8-beta.1: the staple lookup failed twice in a row, then succeeded.
    (2, "Failed to staple your application with code: 68", 3, True),
    (3, "Failed to staple your application with code: 68", 3, False),
    # The notarization upload itself can be cut off (v0.5.8-beta.1 draft).
    (1, "Error: abortedUpload(resumeRequest: SotoS3.S3.ResumeMultipartUploadRequest", 2, True),
    (1, 'Error Domain=NSURLErrorDomain Code=-1005 "The network connection was lost."', 2, True),
    (1, "Error Domain=NSURLErrorDomain Code=-1001 timed out", 2, True),
    # A server-trust failure is not a dropped connection.
    (1, "Error Domain=NSURLErrorDomain Code=-1202 untrusted server certificate", 1, False),
    # A rejected notarization or a signing failure is not transient.
    (1, "Failed to notarize via notarytool. status: Invalid", 1, False),
    (1, "codesign failed", 1, False),
])
def test_package_builds_again_only_when_apple_drops_the_connection(tmp_path, failures, message, calls, ok):
    import shutil
    shutil.copy(ROOT / "scripts/prepare-apple-notarization.sh", tmp_path)
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
               CSC_LINK="", PUBLISH="false", APPLE_RETRY_DELAY_SECONDS="0")
    result = subprocess.run(["bash", "-e", "-c", script], cwd=ROOT / "electron", env=env, capture_output=True, text=True)
    assert (result.returncode == 0) is ok, result.stdout + result.stderr
    assert len(count.read_text(encoding="utf-8").splitlines()) == calls


def test_signpath_signing_is_gated_pinned_and_precedes_feed_and_checksums():
    build = load_workflow()["jobs"]["package"]
    gate = build["env"]["SIGNPATH_ENABLED"]
    for needed in ("secrets.SIGNPATH_API_TOKEN", "vars.SIGNPATH_ORGANIZATION_ID",
                   "vars.SIGNPATH_PROJECT_SLUG", "vars.SIGNPATH_SIGNING_POLICY_SLUG"):
        assert needed in gate
    assert "ELECTRON_WINDOWS_CSC_LINK" in gate  # an own certificate wins

    # Approval waits: the leg's timeout grows only for Windows with SignPath
    # configured (job timeouts cannot read secrets), the step fails clearly first.
    timeout = build["timeout-minutes"]
    assert "matrix.platform == 'win32'" in timeout and "vars.SIGNPATH_ORGANIZATION_ID" in timeout
    assert "150" in timeout and "75" in timeout and "secrets." not in timeout
    submit_step = next(s for s in build["steps"] if s.get("name") == "Sign the Windows installer with SignPath")
    assert submit_step["timeout-minutes"] == "70"
    assert int(submit_step["with"]["wait-for-completion-timeout-in-seconds"]) <= 70 * 60

    # SignPath's github-token needs actions: read; keep everything else read-only.
    assert build["permissions"] == {"contents": "read", "actions": "read"}

    steps = build["steps"]
    names = [s.get("name") for s in steps]
    sign_steps = [s for s in steps if s.get("name", "").startswith(("Upload the unsigned Windows", "Sign the Windows installer with SignPath", "Replace the installer with the signed"))]
    assert len(sign_steps) == 3
    for step in sign_steps:
        assert step["if"] == "env.SIGNPATH_ENABLED == 'true'"
        if "uses" in step:
            assert re.search(r"@[0-9a-f]{40}$", step["uses"].split(" ")[0]), step["uses"]
    submit = next(s for s in sign_steps if s["uses"].startswith("signpath/github-action-submit-signing-request@"))
    assert submit["with"]["wait-for-completion"] == "true"
    assert "api-token" in submit["with"] and "SIGNPATH_API_TOKEN" in submit["with"]["api-token"]

    # After packaging (which writes the feed), before verification, upload and
    # (in the release job) SHA256SUMS.
    package = names.index("Package without publishing")
    first, last = names.index(sign_steps[0]["name"]), names.index(sign_steps[-1]["name"])
    assert package < first < last < names.index("Verify Windows installer signature before publication")
    assert last < names.index("Save installers and updater metadata for review")
    replace = sign_steps[-1]["run"]
    assert "apply-signed-windows.mjs" in replace
    # Loaded from the workflow commit (older tags lack the helper), never from the checkout.
    assert "ref=$GITHUB_SHA" in replace and '"$RUNNER_TEMP/apply-signed-windows.mjs"' in replace
    assert "node scripts/apply-signed-windows.mjs" not in replace
    assert replace.index("apply-signed-windows.mjs\" release-signed") < replace.index("update-package-contract.mjs")

    # Signing is configured -> unsigned output must fail the report step.
    report = next(s for s in steps if s.get("name") == "Report and enforce Windows signing")
    assert "SIGNPATH_ENABLED" in report["env"]["HAS_CREDENTIALS"]


def test_rehearsal_without_desktop_reports_still_lists_the_phones(tmp_path):
    """Every desktop leg failed in a rehearsal, but the phone builds still get their rows (#4037 review)."""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "VoiceStudio-1.2.3-android.apk").write_text("x")
    text = notes.render(tmp_path / "reports", rehearsal=True, assets=assets, base_url="https://x/d")
    assert "No package leg reported its signing." in text
    assert "| Android | [APK](https://x/d/VoiceStudio-1.2.3-android.apk) |" in text and "| iPhone | — |" in text
    assert "| Windows x64 |" not in text


def test_an_older_tags_own_download_table_is_dropped(tmp_path):
    """Rebuilding an older tag must not show two download tables (#4037 review)."""
    section = (
        "**Headline.** The release.\n\n**Download**\n\n| Platform | Installer |\n| --- | --- |\n"
        "| Windows x64 | [Installer](https://example.test/a.exe) |\n\n**Highlights**\n\n- One\n"
    )
    out = notes.without_own_download_table(section)
    assert out == "**Headline.** The release.\n\n**Highlights**\n\n- One\n"
    # Notes without one are unchanged, tables elsewhere included.
    other = "**Highlights**\n\n- One\n\n| a | b |\n|---|---|\n"
    assert notes.without_own_download_table(other) == other
