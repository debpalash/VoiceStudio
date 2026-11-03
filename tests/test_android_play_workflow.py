from pathlib import Path
import os
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "android-play-internal.yml"
GRADLE = ROOT / "mobile" / "android" / "app" / "build.gradle.kts"
MOBILE = ROOT / ".github" / "workflows" / "mobile.yml"


@pytest.mark.parametrize("feature", ["camera", "camera.any", "camera.autofocus", "microphone"])
def test_play_does_not_require_optional_recording_or_qr_hardware(feature):
    """Text speech, imported media and manual pairing work without capture hardware."""
    android = "{http://schemas.android.com/apk/res/android}"
    manifest = ET.parse(ROOT / "mobile/android/app/src/main/AndroidManifest.xml").getroot()
    features = {node.get(android + "name"): node.get(android + "required")
                for node in manifest.findall("uses-feature")}
    assert features.get("android.hardware." + feature) == "false", (
        "Camera permission also implies rear camera/autofocus; explicitly opt out of Play filtering"
    )


def load(path):
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def test_workflow_is_manual_main_only_and_read_only():
    workflow = load(WORKFLOW)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["play-internal"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["environment"] == "google-play"
    assert "env" not in job


def test_workflow_uploads_only_a_verified_release_bundle_to_internal():
    steps = load(WORKFLOW)["jobs"]["play-internal"]["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Verify bundle signature") < names.index("Upload to Play internal testing")
    upload = steps[names.index("Upload to Play internal testing")]
    assert upload["with"]["track"] == "internal"
    assert upload["with"]["packageName"] == "sh.voicestudio.mobile"
    cleanup = steps[-1]
    assert cleanup["name"] == "Remove upload keystore" and cleanup["if"] == "always()"
    build = steps[names.index("Build signed bundle")]
    assert "scripts/android_version_code.py" in build["run"] and ":app:bundleRelease" in build["run"]


def test_play_installs_the_native_build_toolchain():
    """The store workflow must provide the same pinned toolchain as releases."""
    steps = load(WORKFLOW)["jobs"]["play-internal"]["steps"]
    setup = next(step for step in steps if step.get("name") == "NDK and CMake")
    assert '"ndk;27.1.12297006"' in setup["run"]
    assert '"cmake;3.22.1"' in setup["run"]
    assert steps.index(setup) < next(i for i, step in enumerate(steps) if step.get("name") == "Build signed bundle")


@pytest.fixture(scope="module")
def signed_bundle_fixtures(tmp_path_factory):
    """Build real signed archives, including a second trusted co-signer."""
    if not all(shutil.which(command) for command in ("keytool", "jarsigner")):
        pytest.skip("JDK signing tools are required")
    root = tmp_path_factory.mktemp("play-bundle-signatures")
    for name, subject in (("upload", "Play upload fixture"), ("debug", "Android Debug"), ("other", "Other upload fixture")):
        subprocess.run([
            "keytool", "-genkeypair", "-keystore", str(root / f"{name}.jks"),
            "-storepass", "fixture-password", "-keypass", "fixture-password",
            "-alias", "upload", "-keyalg", "RSA", "-keysize", "2048",
            "-validity", "10000", "-dname", f"CN={subject}",
        ], check=True, capture_output=True)
    unsigned = root / "unsigned.aab"
    with zipfile.ZipFile(unsigned, "w") as bundle:
        bundle.writestr("base/manifest/AndroidManifest.xml", "fixture manifest")
    for name in ("upload", "debug", "other"):
        bundle = root / f"{name}.aab"
        shutil.copyfile(unsigned, bundle)
        subprocess.run([
            "jarsigner", "-keystore", str(root / f"{name}.jks"),
            "-storepass", "fixture-password", str(bundle), "upload",
        ], check=True, capture_output=True)
    cosigned = root / "cosigned.aab"
    shutil.copyfile(root / "upload.aab", cosigned)
    subprocess.run([
        "jarsigner", "-keystore", str(root / "other.jks"), "-storepass", "fixture-password",
        "-sigfile", "OTHER", str(cosigned), "upload",
    ], check=True, capture_output=True)
    subprocess.run([
        "keytool", "-exportcert", "-keystore", str(root / "other.jks"), "-storepass", "fixture-password",
        "-alias", "upload", "-file", str(root / "other.cer"),
    ], check=True, capture_output=True)
    shutil.copyfile(root / "upload.jks", root / "extra-trusted.jks")
    subprocess.run([
        "keytool", "-importcert", "-noprompt", "-keystore", str(root / "extra-trusted.jks"),
        "-storepass", "fixture-password", "-alias", "extra", "-file", str(root / "other.cer"),
    ], check=True, capture_output=True)
    return root


@pytest.mark.parametrize("kind", ["upload", "unsigned", "tampered", "debug", "other", "cosigned"])
def test_workflow_verifies_its_actual_upload_key(signed_bundle_fixtures, tmp_path, kind):
    """Accept only an intact archive signed solely by the configured upload key."""
    # Run the workflow's real verification against signed archives. Play upload
    # certificates are normally self-signed; the expected keystore is the trust anchor.
    fixture = signed_bundle_fixtures
    target = tmp_path / "app/build/outputs/bundle/release/app-release.aab"
    target.parent.mkdir(parents=True)
    shutil.copyfile(fixture / f"{'upload' if kind == 'tampered' else kind}.aab", target)
    keystore = {"debug": "debug", "cosigned": "extra-trusted"}.get(kind, "upload")
    shutil.copyfile(fixture / f"{keystore}.jks", tmp_path / "upload.jks")
    if kind == "tampered":
        with zipfile.ZipFile(target, "a") as bundle:
            bundle.writestr("injected.txt", "unsigned content")
    step = next(step for step in load(WORKFLOW)["jobs"]["play-internal"]["steps"] if step.get("name") == "Verify bundle signature")
    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path, capture_output=True, text=True,
        env={**os.environ, "RUNNER_TEMP": str(tmp_path), "KEYSTORE_PASSWORD": "fixture-password", "KEY_ALIAS": "upload"})
    assert (result.returncode == 0) == (kind == "upload"), result.stdout + result.stderr


def test_signed_android_workflows_share_build_number_source_and_serialization():
    play = load(WORKFLOW)["jobs"]["play-internal"]
    release = load(ROOT / ".github/workflows/mobile-release.yml")["jobs"]["android"]
    assert play["concurrency"] == release["concurrency"]
    assert play["concurrency"]["cancel-in-progress"] == "false"
    for job in (play, release):
        build = next(step for step in job["steps"] if "VOICESTUDIO_ANDROID_VERSION_CODE" in step.get("run", ""))
        assert 'python3 "$GITHUB_WORKSPACE/scripts/android_version_code.py"' in build["run"]
        assert "GITHUB_RUN_NUMBER" not in build["run"]
        assert "GITHUB_RUN_ATTEMPT" not in build["run"]


def test_build_numbers_advance_across_workflows_and_reruns(monkeypatch):
    import runpy

    code = runpy.run_path(str(ROOT / "scripts/android_version_code.py"))["version_code"]
    monkeypatch.setenv("GITHUB_RUN_NUMBER", "9000")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "9")
    monkeypatch.setattr("time.time", lambda: 1_792_771_200)
    first = code()
    monkeypatch.setenv("GITHUB_RUN_NUMBER", "1")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr("time.time", lambda: 1_792_771_260)
    assert code() == first + 1
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "10")
    monkeypatch.setattr("time.time", lambda: 1_792_771_320)
    assert code() == first + 2


def test_build_numbers_stay_far_below_plays_limit():
    # Play refuses a first upload whose code jumps toward 2.1e9; seconds gave ~2.1e8 in 2026.
    import runpy

    code = runpy.run_path(str(ROOT / "scripts/android_version_code.py"))["version_code"]
    assert code(1_792_771_200) < 5_000_000  # 2026-10-23
    assert code(2_524_608_000) < 20_000_000  # 2050-01-01


@pytest.mark.parametrize(("minutes", "accepted"), [(0, False), (1, True), (2_100_000_000, True), (2_100_000_001, False)])
def test_build_number_bounds(minutes, accepted):
    import runpy

    module = runpy.run_path(str(ROOT / "scripts/android_version_code.py"))
    if accepted:
        assert module["version_code"](module["EPOCH"] + minutes * 60) == minutes
    else:
        with pytest.raises(ValueError):
            module["version_code"](module["EPOCH"] + minutes * 60)


def test_gradle_signs_release_only_with_the_upload_key_and_parses_beta_versions():
    source = GRADLE.read_text()
    assert 'Regex("""(\\d+)\\.(\\d+)\\.(\\d+)(?:-beta\\.\\d+)?""")' in source
    assert 'appVersion.split(".")' not in source
    assert "if (uploadKeystore != null)" in source
    assert 'environmentVariable("VOICESTUDIO_ANDROID_VERSION_CODE")' in source


def test_mobile_ci_configures_a_beta_version():
    steps = load(MOBILE)["jobs"]["android"]["steps"]
    step = next(step for step in steps if step.get("name") == "Beta version configures")
    assert "-beta.1" in step["run"] and ":app:printAppVersion" in step["run"]


def test_store_app_id_matches_on_both_platforms_and_the_upload():
    # The shared mobile contract ships both apps under one store identity.
    ios = load(ROOT / "mobile" / "ios" / "VoiceStudio" / "project.yml")
    bundle_id = ios["targets"]["VoiceStudio"]["settings"]["base"]["PRODUCT_BUNDLE_IDENTIFIER"]
    assert bundle_id == "sh.voicestudio.mobile"
    assert f'applicationId = "{bundle_id}"' in GRADLE.read_text()
    steps = load(WORKFLOW)["jobs"]["play-internal"]["steps"]
    upload = next(step for step in steps if step.get("name") == "Upload to Play internal testing")
    assert upload["with"]["packageName"] == bundle_id
