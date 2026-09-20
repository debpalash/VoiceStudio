import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize("tag,allowed", [("", False), ("v0.5.4", False), ("v0.5.3", True)])
def test_release_downloads_legacy_feeds_only_from_final_tauri_tag(tmp_path, tag, allowed):
    import os
    import subprocess
    import yaml

    workflow = yaml.load((ROOT / ".github/workflows/electron-release.yml").read_text(), Loader=yaml.BaseLoader)
    step = next(s for s in workflow["jobs"]["release"]["steps"] if s.get("name") == "Preserve the final Tauri updater feeds")
    capture = tmp_path / "downloads"
    for name, body in [("gh", 'printf "%s\\n" "$*" >> "$CAPTURE_PATH"'), ("python3", "exit 0")]:
        executable = tmp_path / name
        executable.write_text("#!/bin/sh\n" + body + "\n")
        executable.chmod(0o755)
    env = dict(os.environ, SUNSET_TAG=tag, TAG="v0.5.4", CAPTURE_PATH=str(capture))
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    result = subprocess.run(["bash", "-e", "-c", step["run"]], env=env, capture_output=True)
    assert (result.returncode == 0) == allowed
    assert capture.exists() == allowed
    if allowed:
        assert capture.read_text().splitlines() == [
            "release download v0.5.3 --pattern latest.json --dir release-assets",
            "release download v0.5.3 --pattern latest-user.json --dir release-assets",
        ]

def test_release_preserves_authored_announcement_and_contributors(monkeypatch, tmp_path):
    """Publishing must not bury the release's introduction under boilerplate."""
    import json
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("release_notes_test", ROOT / "scripts/prepare_electron_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "verify_release", lambda *args, **kwargs: None)
    monkeypatch.chdir(tmp_path)
    Path("frontend").mkdir()
    Path("frontend/package.json").write_text(json.dumps({"version": "1.2.3"}))
    notes = "**A new desktop**\n\n![UI](https://example.com/ui.png)\n\n### Contributors\n\n- @contributor — thanks!\n"
    Path("CHANGELOG.md").write_text("## [Unreleased]\n\n- Later\n\n## [1.2.3] — 2026-09-17\n\n" + notes + "\n## [1.2.2] — 2026-09-10\n\n- Older\n")
    assets = tmp_path / "assets"
    assets.mkdir()
    for suffix in ("mac-arm64.dmg", "mac-x64.dmg", "linux-x64.deb"):
        (assets / f"VoiceStudio-Electron-1.2.3-{suffix}").write_bytes(b"fixture")
    module.prepare(assets, "v1.2.3")
    assert (assets / "RELEASE_NOTES.md").read_text() == notes

@pytest.fixture
def validate_sunset(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("release_helper_test", ROOT / "scripts/prepare_electron_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_sunset

def feed(url="https://github.com/debpalash/VoiceStudio/releases/download/v1.2.3/app.tar.gz"):
    return {"version": "1.2.3", "platforms": {"linux-x86_64": {"url": url, "signature": "signed"}}}

def test_sunset_feed_stays_on_immutable_tauri_release(validate_sunset):
    validate_sunset(feed(), "v1.2.3")

@pytest.mark.parametrize("url", [
    "https://github.com/debpalash/VoiceStudio/releases/latest/download/app.tar.gz",
    "https://example.com/debpalash/VoiceStudio/releases/download/v1.2.3/app.tar.gz",
    "https://github.com/debpalash/VoiceStudio/releases/download/v2.0.0/app.exe",
])
def test_sunset_rejects_redirecting_legacy_clients(url, validate_sunset):
    with pytest.raises(ValueError):
        validate_sunset(feed(url), "v1.2.3")

def test_sunset_requires_signed_payload_and_matching_version(validate_sunset):
    data = feed()
    data["platforms"]["linux-x86_64"]["signature"] = ""
    with pytest.raises(ValueError):
        validate_sunset(data, "v1.2.3")
    with pytest.raises(ValueError):
        validate_sunset(feed(), "v1.2.4")


def test_electron_release_scopes_signing_secrets_and_gates_unsigned_owner_dispatch():
    import yaml
    workflow = yaml.load((ROOT / ".github/workflows/electron-release.yml").read_text(), Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]
    assert "CSC_LINK" not in jobs["package"]["env"]
    assert "CSC_KEY_PASSWORD" not in jobs["package"]["env"]
    credentials = {"CSC_LINK", "CSC_KEY_PASSWORD"}
    scoped = [step for step in jobs["package"]["steps"] if credentials.intersection(step.get("env", {}))]
    assert [step["name"] for step in scoped] == ["Package without publishing"]
    assert credentials.issubset(scoped[0]["env"])
    guard = next(step for step in jobs["validate"]["steps"] if step.get("name") == "Require an exact version tag")
    assert 'test "$DISPATCH_ACTOR" = "$OWNER" && test "$RERUN_ACTOR" = "$OWNER"' in guard["run"]
    assert guard["env"]["DISPATCH_ACTOR"] == "${{ github.actor }}"
    assert guard["env"]["RERUN_ACTOR"] == "${{ github.triggering_actor }}"
    publish = jobs["release"]["steps"][-1]
    assert publish["if"] == "github.event_name == 'workflow_dispatch' && inputs.publish == true"


@pytest.mark.parametrize('credentials,expected', [
    ({}, '|'),
    ({'CSC_LINK': '', 'CSC_KEY_PASSWORD': ''}, '|'),
    ({'CSC_LINK': 'fake-certificate', 'CSC_KEY_PASSWORD': 'fake-password'}, 'x|x'),
])
def test_packaging_omits_empty_signing_credentials(tmp_path, credentials, expected):
    import os
    import subprocess
    import yaml
    workflow = yaml.load((ROOT / '.github/workflows/electron-release.yml').read_text(), Loader=yaml.BaseLoader)
    step = next(s for s in workflow['jobs']['package']['steps'] if s.get('name') == 'Package without publishing')
    for name, body in [('bun', 'printf "%s|%s" "${CSC_LINK+x}" "${CSC_KEY_PASSWORD+x}" > "$CAPTURE_PATH"'), ('node', 'exit 0')]:
        executable = tmp_path / name
        executable.write_text('#!/bin/sh\n' + body + '\n')
        executable.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in {'CSC_LINK', 'CSC_KEY_PASSWORD'}}
    env.update(credentials)
    capture = tmp_path / 'signing-env'
    env.update(PATH=str(tmp_path) + os.pathsep + env['PATH'], CAPTURE_PATH=str(capture))
    script = step['run'].replace('${{ matrix.flags }}', '--mac --arm64').replace('${{ matrix.platform }}', 'darwin').replace('${{ matrix.arch }}', 'arm64')
    subprocess.run(['bash', '-e', '-c', script], env=env, check=True)
    assert capture.read_text() == expected


@pytest.mark.parametrize('workflow_ref,override,ref,head_matches,ok', [
    ('refs/heads/main', 'v1.2.3', 'refs/tags/v1.2.3', True, True),
    ('refs/tags/v1.2.3', '', 'refs/tags/v1.2.3', True, True),
    ('refs/heads/feature', 'v1.2.3', 'refs/tags/v1.2.3', True, False),
    ('refs/heads/main', 'v1.2.3', 'refs/tags/v1.2.3', False, False),
    ('refs/heads/main', 'v9.9.9', 'refs/tags/v9.9.9', True, False),
])
def test_release_tag_override_preserves_exact_tag_checkout(tmp_path, workflow_ref, override, ref, head_matches, ok):
    import os
    import subprocess
    import yaml
    workflow = yaml.load((ROOT / '.github/workflows/electron-release.yml').read_text(), Loader=yaml.BaseLoader)
    for job in workflow['jobs'].values():
        for step in job.get('steps', []):
            if step.get('uses', '').startswith('actions/checkout@'):
                assert step['with']['ref'] == '${{ env.RELEASE_REF }}'
    for name, body in [('node', 'echo 1.2.3'), ('git', 'if [ "$2" = HEAD ]; then echo "$TEST_HEAD"; else echo tagged; fi')]:
        executable = tmp_path / name
        executable.write_text('#!/bin/sh\n' + body + '\n')
        executable.chmod(0o755)
    guard = next(s for s in workflow['jobs']['validate']['steps'] if 'run' in s)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'], REF=ref,
               WORKFLOW_REF=workflow_ref, RELEASE_TAG_OVERRIDE=override, ALLOW_UNSIGNED='false',
               TEST_HEAD='tagged' if head_matches else 'different')
    result = subprocess.run(['bash', '-e', '-c', guard['run']], env=env, capture_output=True)
    assert (result.returncode == 0) == ok, result.stderr
