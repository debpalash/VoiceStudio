"""Archive signing reuses a configured private identity, never a fresh CI key."""
import base64
import importlib.util
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("archive_signing", ROOT / "mobile/ios/scripts/archive_signing.py")
signing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(signing)
TEAM = "ABCDEFGHIJ"
PASSWORD = "fixture-password"


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
    directory = tmp_path_factory.mktemp("archive-identity")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", f"/CN=Apple Development: fixture/OU={TEAM}", "-keyout", str(directory / "key.pem"),
                    "-out", str(directory / "cert.pem")], check=True, capture_output=True)
    subprocess.run(["openssl", "pkcs12", "-export", "-inkey", str(directory / "key.pem"), "-in", str(directory / "cert.pem"),
                    "-out", str(directory / "identity.p12"), "-passout", "env:FIXTURE_P12_PASSWORD"],
                   check=True, capture_output=True, env={**os.environ, "FIXTURE_P12_PASSWORD": PASSWORD})
    return directory / "identity.p12"


def test_encrypted_archive_certificate_matches_team_and_has_fingerprint(identity):
    fingerprint = signing.certificate_identity(identity, PASSWORD, TEAM)
    assert len(fingerprint) == 40 and int(fingerprint, 16)


@pytest.mark.parametrize("team,password", [("KLMNOPQRST", PASSWORD), (TEAM, "wrong-password"), ("", PASSWORD)])
def test_wrong_team_or_password_is_rejected_before_keychain_import(identity, team, password):
    with pytest.raises((ValueError, RuntimeError)):
        signing.certificate_identity(identity, password, team)


@pytest.mark.parametrize("fail_import", [False, True])
def test_install_and_cleanup_preserve_other_keychains_and_remove_decoded_identity(identity, tmp_path, monkeypatch, capsys, fail_import):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("IOS_ARCHIVE_CERTIFICATE", base64.b64encode(identity.read_bytes()).decode())
    monkeypatch.setenv("IOS_ARCHIVE_CERTIFICATE_PASSWORD", PASSWORD)
    monkeypatch.setenv("TEAM_ID", TEAM)
    actual = signing.command
    fingerprint = signing.certificate_identity(identity, PASSWORD, TEAM)
    calls = []

    def run(args, **kwargs):
        if args[0] != "security":
            return actual(args, **kwargs)
        calls.append(args)
        if args[1] == "list-keychains" and "-s" not in args:
            return b'    "/tmp/existing login.keychain-db"\n'
        if args[1] == "create-keychain":
            Path(args[-1]).touch()
        if args[1] == "delete-keychain":
            Path(args[-1]).unlink()
        if args[1] == "import" and fail_import:
            raise RuntimeError("fixture import failure")
        return fingerprint.encode() if args[1] == "find-identity" else b""

    monkeypatch.setattr(signing, "command", run)
    if fail_import:
        with pytest.raises(RuntimeError, match="fixture import failure"):
            signing.install()
    else:
        signing.install()
        signing.cleanup()
    assert not list(tmp_path.iterdir())
    assert ["security", "list-keychains", "-d", "user", "-s", "/tmp/existing login.keychain-db"] in calls
    assert any(call[1] == "delete-keychain" for call in calls)
    assert PASSWORD not in capsys.readouterr().out


@pytest.mark.parametrize("filename,job,archive_name", [("ios-testflight.yml", "testflight", "Archive signed app"), ("mobile-release.yml", "ios", "Archive")])
def test_all_signed_ios_archives_require_reusable_identity_and_cleanup(filename, job, archive_name):
    workflow = yaml.load((ROOT / ".github/workflows" / filename).read_text(), Loader=yaml.BaseLoader)
    steps = workflow["jobs"][job]["steps"]
    install = next(step for step in steps if step.get("name") == "Install reusable archive identity")
    archive = next(step for step in steps if step.get("name") == archive_name)
    assert steps.index(install) < steps.index(archive)
    assert install["env"]["IOS_ARCHIVE_CERTIFICATE"] == "${{ secrets.IOS_ARCHIVE_CERTIFICATE }}"
    assert install.get("if") == archive.get("if")
    cleanup = next(step for step in steps if step.get("name") == "Remove API key")
    assert cleanup["if"] == "always()" and "archive_signing.py" in cleanup["run"] and " cleanup" in cleanup["run"]
    assert 'CODE_SIGN_IDENTITY="Apple Development"' in archive["run"]
    export = next(step for step in steps if "-exportArchive" in step.get("run", ""))
    assert "Add :method string app-store-connect" in export["run"]
    assert '-exportOptionsPlist "$RUNNER_TEMP/ExportOptions.plist"' in export["run"]


@pytest.mark.parametrize("filename,job", [("ios-testflight.yml", "testflight"), ("mobile-release.yml", "ios")])
def test_workflow_cleanup_removes_helper_but_preserves_failure_and_recovery_marker(filename, job, tmp_path):
    workflow = yaml.load((ROOT / ".github/workflows" / filename).read_text(), Loader=yaml.BaseLoader)
    cleanup = next(step for step in workflow["jobs"][job]["steps"] if step.get("name") == "Remove API key")
    helper = tmp_path / "archive_signing.py"
    helper.write_text("raise SystemExit(17)\n")
    marker = tmp_path / "voicestudio-ios-keychains.json"
    marker.write_text('["/tmp/existing.keychain-db"]')
    api_key = tmp_path / "asc_api_key.p8"
    api_key.write_text("fixture")
    result = subprocess.run(["bash", "-e", "-c", cleanup["run"]], cwd=tmp_path,
                            env={**os.environ, "RUNNER_TEMP": str(tmp_path)}, capture_output=True, text=True)
    assert result.returncode == 17, result.stderr
    assert not helper.exists() and not api_key.exists()
    assert marker.is_file()


def test_command_timeout_is_bounded_and_does_not_expose_signing_arguments(monkeypatch):
    def timeout(args, **kwargs):
        assert kwargs["timeout"] == 300
        raise subprocess.TimeoutExpired(args, kwargs["timeout"], output=b"fixture-private-output")

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="^Signing operation timed out: security import$"):
        signing.command(["security", "import", "fixture.p12", "-P", "fixture-secret"])


def test_unsigned_older_release_cleanup_needs_no_new_signing_helper(tmp_path):
    workflow = yaml.load((ROOT / ".github/workflows/mobile-release.yml").read_text(), Loader=yaml.BaseLoader)
    cleanup = next(step for step in workflow["jobs"]["ios"]["steps"] if step.get("name") == "Remove API key")
    result = subprocess.run(["bash", "-e", "-c", cleanup["run"]], cwd=tmp_path,
                            env={**os.environ, "RUNNER_TEMP": str(tmp_path)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_signed_older_release_uses_the_workflows_own_signing_tools():
    workflow = yaml.load((ROOT / ".github/workflows/mobile-release.yml").read_text(), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["ios"]["steps"]
    tools = next(step for step in steps if step.get("name") == "Checkout signing tools")
    install = next(step for step in steps if step.get("name") == "Install reusable archive identity")
    assert tools["with"]["ref"] == "${{ job.workflow_sha }}"
    assert tools["with"]["repository"] == "${{ job.workflow_repository }}"
    assert tools["with"]["persist-credentials"] == "false"
    assert tools["if"] == install["if"]
    assert tools["with"]["path"] + "/mobile/ios/scripts/archive_signing.py" in install["run"]
    assert steps.index(tools) < steps.index(install)


def test_cleanup_deletes_private_keychain_even_when_search_list_restore_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    keychain, search_list = signing.paths()
    keychain.touch()
    decoded = keychain.with_suffix(".p12")
    decoded.write_bytes(b"fixture-private-identity")
    search_list.write_text('["/tmp/existing.keychain-db"]')

    def run(args, **kwargs):
        if args[1] == "list-keychains":
            raise RuntimeError("fixture restore failure")
        if args[1] == "delete-keychain":
            Path(args[-1]).unlink()
        return b""

    monkeypatch.setattr(signing, "command", run)
    with pytest.raises(RuntimeError, match="fixture restore failure"):
        signing.cleanup()
    assert not keychain.exists()
    assert not decoded.exists()


def test_cleanup_removes_decoded_identity_after_interrupted_install(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    keychain, _ = signing.paths()
    decoded = keychain.with_suffix(".p12")
    decoded.write_bytes(b"fixture-private-identity")
    signing.cleanup()
    assert not decoded.exists()
