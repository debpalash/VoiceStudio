import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("workflow_name", ["electron-build.yml", "electron-release.yml"])
def test_electron_packaging_workflows_publish_store_provenance(workflow_name):
    import yaml

    workflow = yaml.load(
        (ROOT / ".github/workflows" / workflow_name).read_text(), Loader=yaml.BaseLoader
    )
    package = workflow["jobs"]["package"]
    build_step = next(
        step
        for step in package["steps"]
        if step.get("name") == "Package without publishing"
    )
    assert "create_store_artifact_manifest.mjs" in build_step["run"]
    upload_step = next(
        step
        for step in package["steps"]
        if step.get("name") == "Save installers and updater metadata for review"
    )
    assert "electron/release/store-artifact-manifest-*.json" in upload_step["with"]["path"]


def test_snap_store_workflow_builds_strict_snap_and_uploads_only_tags_to_candidate():
    import yaml

    serialized = (ROOT / ".github/workflows/snap-store.yml").read_text()
    workflow = yaml.load(serialized, Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["on"]) == {"push", "workflow_dispatch"}
    assert workflow["on"]["push"] == {"tags": ["v*"]}
    assert workflow["env"]["SNAP_NAME"] == "voicestudio"
    assert "linuxExecutableName: 'voicestudio'" in (
        ROOT / "electron/electron-builder.config.mjs"
    ).read_text()

    jobs = workflow["jobs"]
    package = jobs["package"]
    assert package["env"]["VOICESTUDIO_DISTRIBUTION"] == "snap-store"
    assert "environment" not in package and "secrets." not in yaml.dump(package)
    tag_guard = next(s for s in package["steps"] if s.get("name") == "Match the tag to the app version")
    assert '"v$version"' in tag_guard["run"]
    import os
    import re
    import subprocess

    import json

    version = json.loads((ROOT / "package.json").read_text())["version"]
    # A beta package version (X.Y.Z-beta.N) is never a Snap release: the job is
    # skipped for beta tags and this guard rejects them if it ever runs.
    is_final = re.fullmatch(r"\d+\.\d+\.\d+", version) is not None
    for tag, error in [
        (f"v{version}", None if is_final else "is not a final"),
        (f"v{version}-rc.1", "is not a final"),
        ("v0.5", "is not a final"),
        ("v999.0.0", "does not match package.json"),
    ]:
        result = subprocess.run(
            ["bash", "-e", "-c", tag_guard["run"]],
            cwd=ROOT, capture_output=True, text=True,
            env=dict(os.environ, GITHUB_REF_NAME=tag),
        )
        assert (result.returncode == 0) == (error is None), (tag, result.stdout)
        if error:
            assert error in result.stdout, (tag, result.stdout)
    command = next(
        step["run"]
        for step in package["steps"]
        if step.get("name") == "Package without publishing"
    )
    assert "--linux snap --x64 --publish never" in command
    assert "verify_snap_artifact.mjs" in command
    assert "--distribution=snap-store --platform=linux --arch=x64" in command
    assert "applicationUpdates !== 'store'" in command

    upload, promote = jobs["upload"], jobs["promote"]
    assert upload["needs"] == "package"
    assert upload["if"].startswith("github.ref_type == 'tag' &&")
    assert upload["environment"] == promote["environment"] == "snap-store"
    upload_run = "\n".join(s.get("run", "") for s in upload["steps"])
    assert "set -o pipefail" in upload_run
    assert "concurrency" not in upload
    assert upload_run.index("snapcraft upload") < upload_run.index("newest=$(newest_tag)")
    assert upload_run.index("set -o pipefail") < upload_run.index("git ls-remote")
    assert 'if [ -z "$newest" ]' in upload_run
    promote_run = "\n".join(s.get("run", "") for s in promote["steps"])
    assert 'snapcraft release "$SNAP_NAME" "$REVISION" stable' in promote_run
    assert '"$GITHUB_TRIGGERING_ACTOR" != "$GITHUB_REPOSITORY_OWNER"' in promote["steps"][0]["run"]
    # Stable is reachable only through the explicit promote dispatch.
    assert "--release=stable" not in serialized and "candidate,stable" not in serialized
    assert "stable" not in upload_run
    # Credentials appear only in the environment-protected store jobs.
    assert serialized.count("secrets.SNAPCRAFT_STORE_CREDENTIALS") == 2

@pytest.mark.parametrize(
    "tag,newest,released",
    [("v0.5.8", "v0.5.8", True), ("v0.5.8", "v0.5.10", False), ("v0.5.8", "", None)],
)
def test_snap_upload_releases_candidate_only_for_the_newest_tag(tmp_path, tag, newest, released):
    """An older tag's build must never move candidate back (checked after upload)."""
    import os
    import subprocess
    import yaml

    workflow = yaml.load(
        (ROOT / ".github/workflows/snap-store.yml").read_text(), Loader=yaml.BaseLoader
    )
    step = next(s for s in workflow["jobs"]["upload"]["steps"] if s.get("name") == "Upload to candidate")
    (tmp_path / "artifact").mkdir()
    (tmp_path / "artifact" / "VoiceStudio-0.5.8-linux-x64.snap").write_text("")
    log = tmp_path / "calls"
    tags = "".join(f"abc\trefs/tags/{t}\n" for t in ["v0.5.7", newest] if t)
    stubs = {
        "sudo": "exit 0",
        "git": f"printf '{tags}'" if newest else "exit 0",
        "snapcraft": 'echo "snapcraft $*" >> "$LOG"; '
        'case "$1" in upload) echo "Revision 42 created for \'voicestudio\'";; esac',
    }
    for name, body in stubs.items():
        stub = tmp_path / name
        stub.write_text("#!/bin/bash\n" + body + "\n")
        stub.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        LOG=str(log),
        SNAP_NAME="voicestudio",
        SNAPCRAFT_STORE_CREDENTIALS="x",
        GITHUB_REF_NAME=tag,
        GITHUB_REPOSITORY="o/r",
        GITHUB_SHA="0" * 40,
        GITHUB_STEP_SUMMARY=str(tmp_path / "summary"),
    )
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    calls = log.read_text().splitlines()
    assert calls[0].startswith("snapcraft upload artifact/")
    assert "--release" not in calls[0]
    if released is None:
        assert result.returncode != 0 and len(calls) == 1
        return
    assert result.returncode == 0, result.stderr
    assert calls[1:] == (["snapcraft release voicestudio 42 candidate"] if released else [])
    assert "Store revision: 42" in (tmp_path / "summary").read_text()


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
    Path("package.json").write_text(json.dumps({"version": "1.2.3"}))
    notes = "**A new desktop**\n\n![UI](https://example.com/ui.png)\n\n### Contributors\n\n- @contributor — thanks!\n"
    Path("CHANGELOG.md").write_text("## [Unreleased]\n\n- Later\n\n## [1.2.3] — 2026-09-17\n\n" + notes + "\n## [1.2.2] — 2026-09-10\n\n- Older\n")
    assets = tmp_path / "assets"
    assets.mkdir()
    for suffix in ("mac-arm64.dmg", "mac-x64.dmg", "linux-x64.deb"):
        (assets / f"VoiceStudio-1.2.3-{suffix}").write_bytes(b"fixture")
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
    # Who may publish unsigned is exercised in test_unsigned_publish_needs_owner_or_listed_publisher.
    assert 'may_publish_unsigned "$DISPATCH_ACTOR" && may_publish_unsigned "$RERUN_ACTOR"' in guard["run"]
    assert guard["env"]["OWNER"] == "${{ github.repository_owner }}"
    assert guard["env"]["DISPATCH_ACTOR"] == "${{ github.actor }}"
    assert guard["env"]["RERUN_ACTOR"] == "${{ github.triggering_actor }}"
    publish = next(step for step in jobs["release"]["steps"] if step.get("id") == "publish_release")
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
    env.update(PATH=str(tmp_path) + os.pathsep + env['PATH'], CAPTURE_PATH=str(capture), RUNNER_TEMP=str(tmp_path))
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


@pytest.mark.parametrize('publishers,dispatcher,rerunner,ok', [
    ('', 'owner', 'owner', True),
    ('', 'maintainer', 'maintainer', False),
    ('maintainer', 'maintainer', 'maintainer', True),
    (' other , maintainer ', 'maintainer', 'maintainer', True),
    ('maintainer', 'maintainer', 'stranger', False),
    ('maintainer', 'stranger', 'maintainer', False),
    ('maint', 'maintainer', 'maintainer', False),
    ('maintainer,', '', '', False),
])
def test_unsigned_publish_needs_owner_or_listed_publisher(tmp_path, publishers, dispatcher, rerunner, ok):
    import os
    import subprocess
    import yaml
    workflow = yaml.load((ROOT / '.github/workflows/electron-release.yml').read_text(), Loader=yaml.BaseLoader)
    for name, body in [('node', 'echo 1.2.3'), ('git', 'echo tagged')]:
        executable = tmp_path / name
        executable.write_text('#!/bin/sh\n' + body + '\n')
        executable.chmod(0o755)
    guard = next(s for s in workflow['jobs']['validate']['steps'] if 'run' in s)
    assert guard['env']['UNSIGNED_PUBLISHERS'] == '${{ vars.UNSIGNED_RELEASE_PUBLISHERS }}'
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'], REF='refs/tags/v1.2.3',
               WORKFLOW_REF='refs/tags/v1.2.3', RELEASE_TAG_OVERRIDE='', ALLOW_UNSIGNED='true',
               OWNER='owner', UNSIGNED_PUBLISHERS=publishers, DISPATCH_ACTOR=dispatcher, RERUN_ACTOR=rerunner)
    result = subprocess.run(['bash', '-e', '-c', guard['run']], env=env, capture_output=True)
    assert (result.returncode == 0) == ok, result.stderr
