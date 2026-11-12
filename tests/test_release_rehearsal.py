"""A release rehearsal builds and verifies everything a release does, and publishes nothing.

`electron-release.yml` with `rehearsal: true` runs from any branch without a tag.
These guards pin the two halves of that promise: the rehearsal reaches the same
verification as a tag (prepare_electron_release.py, the feed checks, the signing
notes), and no step that writes to GitHub, dispatches a workflow or submits to
SignPath can run in it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# Anything that changes state outside the run: releases, tags, feeds, dispatches.
PUBLISHING = re.compile(
    r"gh release (create|edit|upload|delete)|gh workflow run|gh api [^\n]*-X\s*(POST|PUT|PATCH|DELETE)"
    r"|git push|git tag |docker push|gh pr (create|merge)"
)
PUBLISHING_ACTIONS = ("softprops/action-gh-release", "docker/build-push-action", "docker/login-action")
GUARD = 'if [ "$REHEARSAL" != false ]; then echo "::error::A rehearsal run reached a publishing step"; exit 1; fi'


def workflow(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))
    return data


RELEASE = workflow("electron-release.yml")


def steps(job: str) -> list[dict]:
    return RELEASE["jobs"][job]["steps"]


def step(job: str, name: str) -> dict:
    return next(s for s in steps(job) if s.get("name") == name)


def test_rehearsal_is_an_opt_in_dispatch_with_a_test_only_signpath_choice():
    inputs = RELEASE["on"]["workflow_dispatch"]["inputs"]
    assert inputs["rehearsal"] == {
        "description": inputs["rehearsal"]["description"], "type": "boolean", "default": False,
    }
    policy = inputs["signpath_policy"]
    # The release policy is never a choice: a rehearsal can only test-sign.
    assert policy["type"] == "choice" and policy["options"] == ["none", "test-signing"]
    assert policy["default"] == "none"
    assert RELEASE["env"]["REHEARSAL"] == "${{ inputs.rehearsal == true }}"
    # Every leg builds the dispatched commit, not whatever the branch moves to.
    assert "inputs.rehearsal && github.sha" in RELEASE["env"]["RELEASE_REF"]
    assert "inputs.rehearsal && github.sha" in RELEASE["jobs"]["omnivoice-tts"]["with"]["ref"]
    assert "rehearsal" in RELEASE["concurrency"]["group"]


def test_tags_keep_the_exact_tag_check_and_rehearsals_take_their_own_path():
    exact = step("validate", "Require an exact version tag")
    assert exact["if"] == "inputs.rehearsal != true"
    assert 'test "$REF" = "refs/tags/v$VERSION"' in exact["run"]
    assert "test \"$WORKFLOW_REF\" = refs/heads/main" in exact["run"]
    # A real release cannot pick a SignPath policy; it uses the configured one.
    assert "signpath_policy is for rehearsals" in exact["run"]
    rehearsal = step("validate", "Validate the rehearsal request")
    assert rehearsal["if"] == "inputs.rehearsal == true"
    # The version shape is checked for both paths by the channel step.
    channel = step("validate", "Resolve the release channel")
    assert "if" not in channel and "--print-channel" in channel["run"]


@pytest.mark.parametrize(("publish", "release_tag", "allow_unsigned", "ok"), [
    ("false", "", "false", True),
    ("true", "", "false", False),
    ("false", "v1.2.3", "false", False),
    ("false", "", "true", False),
])
def test_a_rehearsal_request_cannot_ask_to_publish(publish, release_tag, allow_unsigned, ok):
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    env = dict(os.environ, PUBLISH=publish, RELEASE_TAG_OVERRIDE=release_tag,
               ALLOW_UNSIGNED=allow_unsigned, GITHUB_SHA=head)
    result = subprocess.run(["bash", "-e", "-c", step("validate", "Validate the rehearsal request")["run"]],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert (result.returncode == 0) is ok, result.stdout + result.stderr


def _publishing_steps(data: dict) -> list[tuple[str, dict]]:
    found = []
    for job_name, job in data["jobs"].items():
        for s in job.get("steps", []):
            uses = s.get("uses", "")
            if PUBLISHING.search(s.get("run", "")) or uses.startswith(PUBLISHING_ACTIONS):
                found.append((job_name, s))
    return found


def test_every_publishing_step_is_skipped_and_refuses_a_rehearsal():
    found = _publishing_steps(RELEASE)
    names = {s["name"] for _, s in found}
    assert {"Create or update draft", "Publish only when explicitly requested",
            "Update the Android repository after stable publication",
            "Publish release container images"} <= names
    for _, s in found:
        assert "inputs.rehearsal != true" in s["if"], s["name"]
        # Second line of defense: the script itself refuses before any gh call.
        first = next(line.strip() for line in s["run"].splitlines() if line.strip() and not line.strip().startswith("#"))
        assert first == GUARD, s["name"]


def test_publishing_steps_stop_a_rehearsal_before_calling_github(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    calls = tmp_path / "calls"
    (fake / "gh").write_text(f'#!/bin/sh\necho "$*" >> "{calls}"\n', encoding="utf-8")
    (fake / "gh").chmod(0o755)
    for rehearsal in ("true", ""):
        for _, s in _publishing_steps(RELEASE):
            env = dict(os.environ, PATH=f"{fake}{os.pathsep}{os.environ['PATH']}", REHEARSAL=rehearsal,
                       CHANNEL="stable", TAG="v1.2.3", GITHUB_REPOSITORY="o/r")
            result = subprocess.run(["bash", "-e", "-c", s["run"]], cwd=tmp_path, env=env,
                                    capture_output=True, text=True)
            assert result.returncode != 0, s["name"]
            assert "A rehearsal run reached a publishing step" in result.stdout, s["name"]
    assert not calls.exists()


def test_called_workflows_publish_nothing_in_a_rehearsal():
    mobile = RELEASE["jobs"]["mobile"]
    assert mobile["with"]["rehearsal"] == "${{ inputs.rehearsal == true }}"
    assert "inputs.rehearsal && github.sha" in mobile["with"]["release_ref"]
    # Store signing stays off: electron-release never asks the phone build for it.
    assert "signed_ios" not in mobile["with"]
    called = workflow("mobile-release.yml")
    assert called["on"]["workflow_call"]["inputs"]["rehearsal"]["default"] is False
    # The only job there that writes is for mobile-v* tag pushes.
    for job_name, s in _publishing_steps(called):
        assert job_name == "release"
    assert called["jobs"]["release"]["if"] == "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/mobile-v')"
    assert not _publishing_steps(workflow("build-omnivoice-tts.yml"))


def test_rehearsal_phone_builds_never_read_signing_secrets():
    jobs = workflow("mobile-release.yml")["jobs"]
    for platform in ("android", "ios"):
        job = jobs[platform]
        # The protected environment (and its secrets) stays out of a rehearsal.
        assert job["environment"] == "${{ !inputs.rehearsal && 'mobile-release' || '' }}"
        source = next(s for s in job["steps"] if s.get("name") == "Validate release source")
        assert "!inputs.rehearsal" in source["if"]
        rehearsal = next(s for s in job["steps"] if s.get("name") == "Validate rehearsal source")
        assert rehearsal["if"] == "inputs.rehearsal"
        assert 'git rev-parse HEAD)" = "$RELEASE_REF"' in rehearsal["run"]
    android = {s.get("name"): s for s in jobs["android"]["steps"]}
    assert android["Decode keystore"]["if"] == "${{ !inputs.rehearsal }}"
    # Store signing needs the environment's secrets: skipped in a rehearsal even
    # when a caller also asks for signed_ios (#4011 review). Cleanup still runs.
    ios = jobs["ios"]["steps"]
    signing = [s for s in ios if "inputs.signed_ios" in str(s.get("if", ""))]
    assert len(signing) == 6
    for s in signing:
        assert s["if"].endswith(") && !inputs.rehearsal"), s.get("name")
    assert next(s for s in ios if s.get("name") == "Remove API key")["if"] == "always()"
    build = android["Build signed APK and AAB"]
    assert "unset VOICESTUDIO_ANDROID_KEYSTORE" in build["run"]
    assert build["run"].index("unset VOICESTUDIO_ANDROID_KEYSTORE") < build["run"].index("./gradlew")


def test_signpath_runs_in_a_rehearsal_only_on_the_test_policy():
    env = RELEASE["jobs"]["package"]["env"]
    assert "(inputs.rehearsal != true || inputs.signpath_policy == 'test-signing')" in env["SIGNPATH_ENABLED"]
    assert env["SIGNPATH_POLICY"] == "${{ inputs.rehearsal == true && inputs.signpath_policy || vars.SIGNPATH_SIGNING_POLICY_SLUG }}"
    submit = step("package", "Sign the Windows installer with SignPath")
    assert submit["with"]["signing-policy-slug"] == "${{ env.SIGNPATH_POLICY }}"
    report = step("package", "Report and enforce Windows signing")
    assert "env.REHEARSAL == 'true'" in report["env"]["TEST_SIGNED"]
    assert "test-signed" in report["run"]


_STUBS = r'''
$rsa = [System.Security.Cryptography.RSA]::Create(2048)
$request = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new("CN=test", $rsa, "SHA256", [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
$days = [int]$env:STUB_CERT_DAYS
$stubCert = $request.CreateSelfSigned([DateTimeOffset]::Now.AddDays($days - 60), [DateTimeOffset]::Now.AddDays($days))
function Get-ChildItem { [pscustomobject]@{ FullName = 'VoiceStudio-1.2.3-win-x64.exe'; Name = 'VoiceStudio-1.2.3-win-x64.exe' } }
function Get-AuthenticodeSignature {
  [pscustomobject]@{ Status = $env:STUB_STATUS; SignerCertificate = $(if ($env:STUB_SIGNED -eq 'true') { $stubCert } else { $null }) }
}
'''


@pytest.mark.skipif(not __import__("shutil").which("pwsh"), reason="needs PowerShell")
@pytest.mark.parametrize(("status", "signed", "cert_days", "ok"), [
    ("UnknownError", "true", 30, True),   # SignPath's test certificate: untrusted root only
    ("NotTrusted", "true", 30, True),
    ("Valid", "true", 30, True),
    ("HashMismatch", "true", 30, False),  # damaged after signing (#4011 review)
    ("NotSigned", "false", 30, False),
    ("UnknownError", "false", 30, False),
    ("UnknownError", "true", -1, False),  # an expired certificate is more than an untrusted root
])
def test_test_signing_accepts_only_an_intact_signature_from_an_untrusted_root(tmp_path, status, signed, cert_days, ok):
    script = step("package", "Report and enforce Windows signing")["run"].replace("${{ matrix.arch }}", "x64")
    env = dict(os.environ, TEST_SIGNED="true", HAS_CREDENTIALS="true", STUB_STATUS=status,
               STUB_SIGNED=signed, STUB_CERT_DAYS=str(cert_days))
    result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", _STUBS + script],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert (result.returncode == 0) is ok, result.stdout + result.stderr
    report = tmp_path / "electron/release-signing/signing-win32-x64.json"
    assert report.is_file() is ok
    if ok:
        assert json.loads(report.read_text(encoding="utf-8-sig"))["status"] == "test-signed"


def test_rehearsal_runs_the_same_verification_as_a_release():
    validate = step("release", "Validate all platforms before creating a release")["run"]
    assert "--assets release-assets --rehearsal --missing-report" in validate
    assert '--assets release-assets --tag "$TAG" --repo' in validate
    sunset = step("release", "Preserve the final Tauri updater feeds")
    assert "rehearsal" not in sunset.get("if", "")
    assert "--rehearsal --missing-report" in sunset["run"] and '--sunset-tag "$SUNSET_TAG"' in sunset["run"]
    describe = step("release", "Describe downloads and signing per platform")["run"]
    assert "--rehearsal" in describe and "NOTES_ARGS" in describe
    feeds = step("release", "Rehearsal - check the feeds the release would carry")
    assert feeds["if"] == "inputs.rehearsal == true"
    assert "check_electron_release_assets.py --foreign-feeds" in feeds["run"]
    tag = RELEASE["jobs"]["release"]["env"]["TAG"]
    assert tag.startswith("${{ inputs.rehearsal && format('v{0}', needs.validate.outputs.version)")
    names = [s.get("name") for s in steps("release")]
    assert names.index("Rehearsal - check the feeds the release would carry") < names.index("Create or update draft")


def test_rehearsal_artifacts_expire_within_days():
    for job_name, job in RELEASE["jobs"].items():
        for s in job.get("steps", []):
            if s.get("uses", "").startswith("actions/upload-artifact@"):
                days = str(s["with"]["retention-days"])
                assert days in ("3", "${{ env.ARTIFACT_RETENTION_DAYS }}") or "inputs.rehearsal && 3" in days, s["name"]
    assert RELEASE["jobs"]["package"]["env"]["ARTIFACT_RETENTION_DAYS"] == "${{ inputs.rehearsal && 3 || 14 }}"
    assert RELEASE["jobs"]["omnivoice-tts"]["with"]["retention_days"] == "${{ inputs.rehearsal && 3 || 0 }}"
    upload = next(s for s in workflow("build-omnivoice-tts.yml")["jobs"]["build-omnivoice-tts"]["steps"]
                  if s.get("name") == "Upload binary artifact")
    assert upload["with"]["retention-days"] == "${{ inputs.retention_days || '' }}"
    mobile = workflow("mobile-release.yml")["jobs"]
    for job_name, artifact in (("android", "VoiceStudio-android-release"), ("ios", "VoiceStudio-ios-unsigned")):
        upload = next(s for s in mobile[job_name]["steps"] if s.get("with", {}).get("name") == artifact)
        assert upload["with"]["retention-days"] == "${{ inputs.rehearsal && 3 || 30 }}"


@pytest.fixture
def release_module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("prepare_release_rehearsal_test", ROOT / "scripts/prepare_electron_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_rehearsal_verifies_as_the_package_version_and_credits_up_to_head(release_module, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    Path("package.json").write_text(json.dumps({"version": "1.2.3-beta.2"}), encoding="utf-8")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q",
                    "--allow-empty", "-m", "x"], check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    assert release_module.rehearsal_target() == ("v1.2.3-beta.2", head)

    compared = []
    def gh(*args):
        if args[0] == "pr":
            return ""
        if args[1].endswith("/releases/latest"):
            return "v1.2.2 2026-10-01T00:00:00Z\n"
        if args[1].endswith("/releases"):
            return ""
        compared.append(args[1])
        return "alice\n"
    block = release_module.contributors_section("o/r", "v1.2.3-beta.2", gh, head=head)
    assert compared == [f"repos/o/r/compare/v1.2.2...{head}"] and "@alice" in block


def test_a_rehearsal_with_failed_legs_still_verifies_the_rest_and_then_fails():
    job = RELEASE["jobs"]["release"]
    # A release keeps needing every leg; only a rehearsal goes on after a failure.
    assert job["if"] == "${{ success() || (inputs.rehearsal == true && !cancelled() && needs.validate.result == 'success') }}"
    for name in ("Validate all platforms before creating a release", "Preserve the final Tauri updater feeds"):
        assert '--rehearsal --missing-report "$RUNNER_TEMP/rehearsal-missing-desktop.txt"' in step("release", name)["run"]
    fail = step("release", "Rehearsal - fail when a build leg produced nothing")
    # Runs after any earlier failure too, so the missing legs are always listed (#4011 review).
    assert fail["if"] == "${{ !cancelled() && inputs.rehearsal == true }}" and "exit 1" in fail["run"]
    names = [s.get("name") for s in steps("release")]
    assert names.index("Rehearsal - save the release notes and checksums") < names.index(fail["name"]) < names.index("Create or update draft")


@pytest.mark.parametrize("rehearsal,ok", [("true", True), ("false", False)])
def test_missing_phone_binaries_are_listed_only_in_a_rehearsal(tmp_path, rehearsal, ok):
    android = tmp_path / "phone-assets/VoiceStudio-android-release"
    android.mkdir(parents=True)
    (android / "VoiceStudio.apk").write_bytes(b"apk")
    (android / "VoiceStudio.aab").write_bytes(b"aab")  # the iOS leg failed
    env = dict(os.environ, TAG="v1.2.3", REHEARSAL=rehearsal, RUNNER_TEMP=str(tmp_path))
    result = subprocess.run(["bash", "-e", "-c", step("release", "Collect phone binaries")["run"]],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert (result.returncode == 0) is ok, result.stdout + result.stderr
    assert (tmp_path / "release-assets/VoiceStudio-1.2.3-android.apk").is_file()
    if ok:
        assert (tmp_path / "rehearsal-missing-phone.txt").read_text(encoding="utf-8") == "phone binary VoiceStudio-1.2.3-ios-unsigned.ipa\n"


PHONE_ARTIFACTS = {"VoiceStudio-android-release": ("VoiceStudio.apk", "VoiceStudio.aab"),
                   "VoiceStudio-ios-unsigned": ("VoiceStudio-unsigned.ipa",)}


def _download(step_with: dict, available: dict, workspace: Path) -> None:
    """actions/download-artifact at the pinned v8.0.2 commit: a pattern download
    writes into `path` itself when exactly one artifact matches (or with
    merge-multiple), and into `path/<artifact name>` when several do."""
    from fnmatch import fnmatchcase
    matched = [name for name in available if fnmatchcase(name, step_with["pattern"])]
    for name in matched:
        flat = len(matched) == 1 or step_with.get("merge-multiple") in (True, "true")
        target = workspace / step_with["path"] / ("" if flat else name)
        target.mkdir(parents=True, exist_ok=True)
        for file in available[name]:
            (target / file).write_bytes(b"x")


@pytest.mark.parametrize("built", [
    ("VoiceStudio-android-release", "VoiceStudio-ios-unsigned"),
    ("VoiceStudio-android-release",),
    ("VoiceStudio-ios-unsigned",),
])
def test_a_surviving_phone_app_keeps_its_layout(tmp_path, built):
    """One phone leg failing must not move the other app out of its folder (#4011 review)."""
    available = {name: PHONE_ARTIFACTS[name] for name in built}
    downloads = [s for s in steps("release") if s.get("uses", "").startswith("actions/download-artifact@")
                 and s["with"].get("path", "").startswith("phone-assets")]
    assert {s["with"]["pattern"] for s in downloads} == set(PHONE_ARTIFACTS)
    for s in downloads:
        _download(s["with"], available, tmp_path)
    env = dict(os.environ, TAG="v1.2.3", REHEARSAL="true", RUNNER_TEMP=str(tmp_path))
    subprocess.run(["bash", "-e", "-c", step("release", "Collect phone binaries")["run"]],
                   cwd=tmp_path, env=env, check=True, capture_output=True, text=True)
    collected = {p.name for p in (tmp_path / "release-assets").iterdir()}
    expected = {"VoiceStudio-android-release": {"VoiceStudio-1.2.3-android.apk", "VoiceStudio-1.2.3-android.aab"},
                "VoiceStudio-ios-unsigned": {"VoiceStudio-1.2.3-ios-unsigned.ipa"}}
    assert collected == set().union(*(expected[name] for name in built))
    missing = tmp_path / "rehearsal-missing-phone.txt"
    assert missing.is_file() is (len(built) == 1)


def test_prepare_lists_failed_legs_only_when_asked(release_module, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path("package.json").write_text(json.dumps({"version": "1.2.3"}), encoding="utf-8")
    Path("CHANGELOG.md").write_text("## [1.2.3] — 2026-11-11\n\n- Fix\n", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "VoiceStudio-1.2.3-mac-arm64.dmg").write_bytes(b"x")
    # Nothing at all from the other legs: a strict run stops at the first.
    with pytest.raises(Exception, match="missing downloaded manifest"):
        release_module.prepare(assets, "v1.2.3")
    missing = []
    release_module.prepare(assets, "v1.2.3", missing=missing)
    assert missing == ["installer mac-x64.dmg", "installer linux-x64.deb",
                       "darwin-arm64", "darwin-x64", "linux-x64", "win32-x64"]
    assert (assets / "SHA256SUMS.txt").is_file()
    # A leg with some of its files is broken, not missing: it still fails at once.
    (assets / "VoiceStudio-1.2.3-win-x64.exe").write_bytes(b"x")
    with pytest.raises(Exception, match="electron-stable-win32-x64.yml"):
        release_module.prepare(assets, "v1.2.3", missing=[])


def test_a_broken_leg_still_fails_but_every_absent_leg_is_reported(tmp_path):
    """One leg with an incomplete feed must not hide the legs that built nothing (#4011 review)."""
    work = tmp_path / "work"
    (work / "assets").mkdir(parents=True)
    (work / "package.json").write_text(json.dumps({"version": "1.2.3"}), encoding="utf-8")
    (work / "CHANGELOG.md").write_text("## [1.2.3] — 2026-11-11\n\n- Fix\n", encoding="utf-8")
    # Windows x64 is broken (installer without its feed); every other leg is absent.
    (work / "assets/VoiceStudio-1.2.3-win-x64.exe").write_bytes(b"x")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], cwd=work, check=True)
    report = tmp_path / "missing.txt"
    result = subprocess.run(["python3", str(ROOT / "scripts/prepare_electron_release.py"), "--assets", "assets",
                             "--rehearsal", "--missing-report", str(report)], cwd=work, capture_output=True, text=True)
    assert result.returncode != 0 and "electron-stable-win32-x64.yml" in result.stderr
    assert report.read_text(encoding="utf-8").splitlines() == [
        "installer mac-arm64.dmg", "installer mac-x64.dmg", "installer linux-x64.deb",
        "darwin-arm64", "darwin-x64", "linux-x64"]


def test_prepare_cli_keeps_releases_strict(tmp_path):
    result = subprocess.run(["python3", str(ROOT / "scripts/prepare_electron_release.py"), "--assets", str(tmp_path),
                             "--tag", "v1.2.3", "--missing-report", str(tmp_path / "m.txt")], capture_output=True, text=True)
    assert result.returncode != 0 and "only for --rehearsal" in result.stderr


def test_prepare_rehearsal_refuses_an_unreleasable_version(release_module, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path("package.json").write_text(json.dumps({"version": "1.2.3-rc.1"}), encoding="utf-8")
    with pytest.raises(ValueError, match="stable or beta"):
        release_module.rehearsal_target()


def test_prepare_cli_takes_a_tag_or_rehearsal_not_both(tmp_path):
    result = subprocess.run(["python3", str(ROOT / "scripts/prepare_electron_release.py"), "--assets", str(tmp_path),
                             "--tag", "v1.2.3", "--rehearsal"], capture_output=True, text=True)
    assert result.returncode != 0 and "not allowed with" in result.stderr


def test_test_signed_windows_installers_are_described_only_in_a_rehearsal(tmp_path):
    spec = importlib.util.spec_from_file_location("release_signing_notes_rehearsal", ROOT / "scripts/release_signing_notes.py")
    notes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notes)
    (tmp_path / "signing-win32-x64.json").write_text(json.dumps({"platform": "win32", "arch": "x64", "status": "test-signed"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown signing status"):
        notes.render(tmp_path)
    assert "test certificate" in notes.render(tmp_path, rehearsal=True)


def test_signing_notes_without_any_report_stop_only_a_release(tmp_path):
    """omnivoice-tts failing skips every package leg: a rehearsal still reaches its missing-leg list."""
    spec = importlib.util.spec_from_file_location("release_signing_notes_empty", ROOT / "scripts/release_signing_notes.py")
    notes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notes)
    with pytest.raises(ValueError, match="Unexpected signing reports: none"):
        notes.render(tmp_path / "release-signing")
    assert "No package leg reported its signing" in notes.render(tmp_path / "release-signing", rehearsal=True)


def test_releasing_docs_explain_the_rehearsal():
    text = (ROOT / "docs/RELEASING.md").read_text(encoding="utf-8")
    assert "## Rehearse a release" in text
    assert "gh workflow run electron-release.yml --ref <branch> -f rehearsal=true" in text
