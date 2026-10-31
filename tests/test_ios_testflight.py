import importlib.util
import plistlib
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ios-testflight.yml"
SPEC = importlib.util.spec_from_file_location("prepare_resources", ROOT / "mobile/ios/scripts/prepare_resources.py")
prepare_resources = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_resources)


def load_workflow():
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


@pytest.mark.parametrize(("version", "expected"), [("0.5.7", "0.5.7"), ("0.5.8-beta.2", "0.5.8")])
def test_marketing_version_is_app_store_shaped(version, expected):
    assert prepare_resources.marketing_version(version) == expected


@pytest.mark.parametrize("version", ["0.5", "0.5.8-rc.1", "v0.5.8"])
def test_marketing_version_rejects_unsupported_versions(version):
    with pytest.raises(ValueError):
        prepare_resources.marketing_version(version)


def test_build_number_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICESTUDIO_IOS_BUILD_NUMBER", "42.2")
    prepare_resources.prepare(tmp_path / "bundle", tmp_path / "Info.plist")
    info = plistlib.loads((tmp_path / "Info.plist").read_bytes())
    assert info["CFBundleVersion"] == "42.2"
    assert prepare_resources.re.fullmatch(r"\d+\.\d+\.\d+", info["CFBundleShortVersionString"])


def test_resource_catalogs_keep_utf8_under_a_windows_default_codec(tmp_path, monkeypatch):
    read_text, write_text = Path.read_text, Path.write_text

    def legacy_read(path, encoding=None, errors=None):
        return read_text(path, encoding=encoding or "cp1252", errors=errors)

    def legacy_write(path, data, encoding=None, errors=None):
        return write_text(path, data, encoding=encoding or "cp1252", errors=errors)

    monkeypatch.setattr(Path, "read_text", legacy_read)
    monkeypatch.setattr(Path, "write_text", legacy_write)
    prepare_resources.prepare(tmp_path / "bundle", tmp_path / "Info.plist")
    french = prepare_resources.json.loads(read_text(ROOT / "electron/src/renderer/src/i18n/locales/fr.json", encoding="utf-8"))
    expected = prepare_resources.json.dumps(french["permissions"]["microphone_why"])[1:-1]
    actual = (tmp_path / "bundle/fr.lproj/InfoPlist.strings").read_bytes().decode("utf-8")
    assert expected in actual


@pytest.mark.parametrize("value", ["", "1.2.3.4", "12a"])
def test_build_number_rejects_values_app_store_connect_refuses(value):
    with pytest.raises(ValueError):
        prepare_resources.build_number(value)


def test_workflow_is_manual_main_only_and_read_only():
    workflow = load_workflow()
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["testflight"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["environment"] == "app-store-connect"
    assert "github.run_attempt" in job["env"]["VOICESTUDIO_IOS_BUILD_NUMBER"]


def test_workflow_signs_verifies_and_cleans_up_the_key():
    steps = load_workflow()["jobs"]["testflight"]["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Verify archive signature and version") < names.index("Upload to TestFlight")
    cleanup = steps[-1]
    assert cleanup["name"] == "Remove API key" and cleanup["if"] == "always()"
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "CODE_SIGNING_ALLOWED=NO" not in source
    assert "testFlightInternalTestingOnly bool true" in source
    # Secrets reach only the steps that need them, never the whole job.
    assert "secrets." not in yaml.dump(load_workflow()["jobs"]["testflight"].get("env", {}))


def test_clean_testflight_runner_prepares_native_dependencies_before_project_generation():
    steps = load_workflow()["jobs"]["testflight"]["steps"]
    commands = [step.get("run", "") for step in steps]
    native = next(i for i, command in enumerate(commands) if "bash mobile/native/ios/build.sh ios" in command)
    generate = next(i for i, command in enumerate(commands) if "xcodegen generate" in command)
    archive = next(i for i, command in enumerate(commands) if "-archivePath" in command)
    assert native < generate < archive
    assert "if" not in steps[native]  # A cache hit still needs generated headers restored.


def test_archive_check_matches_automatic_signing():
    # The archive carries the development identity until export re-signs it, so
    # requiring Apple Distribution here failed every run; check the team instead.
    steps = load_workflow()["jobs"]["testflight"]["steps"]
    verify = next(step for step in steps if step.get("name") == "Verify archive signature and version")
    assert "Authority=Apple Distribution" not in verify["run"]
    assert 'TeamIdentifier=$TEAM_ID' in verify["run"]
    assert verify["env"]["TEAM_ID"] == "${{ secrets.APPLE_TEAM_ID }}"


IOS = ROOT / "mobile" / "ios" / "VoiceStudio"


def test_app_store_validation_requirements():
    # Each check mirrors an App Store Connect upload rejection (run 39956020986).
    assert load_workflow()["jobs"]["testflight"]["runs-on"] == "macos-26"
    info = plistlib.loads((IOS / "Info.plist").read_bytes())
    assert info["CFBundleIconName"] == "AppIcon"
    # Only OS-provided HTTPS and hashing: exempt, so TestFlight skips the per-build question.
    assert info["ITSAppUsesNonExemptEncryption"] is False
    assert set(info["UISupportedInterfaceOrientations~ipad"]) == {
        "UIInterfaceOrientationPortrait",
        "UIInterfaceOrientationPortraitUpsideDown",
        "UIInterfaceOrientationLandscapeLeft",
        "UIInterfaceOrientationLandscapeRight",
    }
    project = yaml.safe_load((IOS / "project.yml").read_text(encoding="utf-8"))
    assert project["targets"]["VoiceStudio"]["settings"]["base"]["ASSETCATALOG_COMPILER_APPICON_NAME"] == "AppIcon"


def test_app_icon_is_an_opaque_1024_png():
    png = (IOS / "Resources/Assets.xcassets/AppIcon.appiconset/AppIcon.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    assert (width, height) == (1024, 1024)
    assert png[25] == 2, "App Store icons must be RGB without an alpha channel"


@pytest.mark.skipif(sys.platform != "darwin", reason="Brand renderer uses macOS CoreGraphics")
def test_generated_and_bundled_catalogs_have_one_app_icon(tmp_path):
    generated = tmp_path / "Brand.xcassets"
    # Simulate an incremental build made before the App Store icon was bundled.
    (generated / "AppIcon.appiconset").mkdir(parents=True)
    prepare_resources.prepare_brand_assets(generated)
    icons = list((IOS / "Resources").rglob("AppIcon.appiconset"))
    icons += list(generated.rglob("AppIcon.appiconset"))
    assert len(icons) == 1, "Duplicate AppIcon catalogs make actool's icon selection ambiguous"
    assert (generated / "LaunchLogo.imageset" / "logo-3x.png").is_file()


def test_mobile_ci_builds_ios_with_the_upload_toolchain():
    mobile = yaml.load((ROOT / ".github/workflows/mobile.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert mobile["jobs"]["ios"]["runs-on"] == load_workflow()["jobs"]["testflight"]["runs-on"]
    release = yaml.load((ROOT / ".github/workflows/mobile-release.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert release["jobs"]["ios"]["runs-on"] == load_workflow()["jobs"]["testflight"]["runs-on"]


@pytest.mark.parametrize("filename", ["mobile.yml", "mobile-release.yml"])
def test_native_runtime_cache_survives_later_app_or_ui_failures(filename):
    workflow = yaml.load((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["ios"]["steps"]
    restore = next(step for step in steps if step.get("uses", "").startswith("actions/cache/restore@"))
    save = next(step for step in steps if step.get("uses", "").startswith("actions/cache/save@"))
    build = next(i for i, step in enumerate(steps) if "bash mobile/native/ios/build.sh ios" in step.get("run", ""))
    generate = next(i for i, step in enumerate(steps) if "xcodegen generate" in step.get("run", ""))
    assert build < steps.index(save) < generate
    assert "if" not in steps[build]  # Cache hits still need generated headers restored.
    assert save["with"]["path"] == restore["with"]["path"]
    assert save["with"]["key"] == "${{ steps." + restore["id"] + ".outputs.cache-primary-key }}"


def test_native_frameworks_carry_app_store_version_keys(tmp_path, monkeypatch):
    # Upload run 40004846857 was rejected: OmniVoice.framework lacked CFBundleShortVersionString.
    spec = importlib.util.spec_from_file_location("native_ios_build", ROOT / "mobile/native/ios/build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    monkeypatch.setattr(build, "run", lambda *args, **kwargs: None)
    library = tmp_path / "libomnivoice.dylib"
    library.write_bytes(b"")
    header = tmp_path / "omnivoice.h"
    header.write_text("")
    target = build.framework("OmniVoice", library, [header], tmp_path / "out", "iPhoneOS")
    info = plistlib.loads((target / "Info.plist").read_bytes())
    assert info["CFBundleShortVersionString"] and info["CFBundleVersion"]


def test_upload_checks_every_embedded_framework_version():
    script = next(s["run"] for s in load_workflow()["jobs"]["testflight"]["steps"] if s.get("name") == "Verify archive signature and version")
    assert 'Frameworks/*.framework/Info.plist' in script
    assert "CFBundleShortVersionString CFBundleVersion" in script


def test_content_reports_go_to_production_unless_overridden(tmp_path):
    dotenv = tmp_path / ".env"
    assert prepare_resources.content_reports_url({}, dotenv) == "https://appapi.voicestudio.sh"
    dotenv.write_text("# local\nexport VOICESTUDIO_CONTENT_REPORTS_URL='https://dev-appapi.voicestudio.sh/'\n", encoding="utf-8")
    assert prepare_resources.content_reports_url({}, dotenv) == "https://dev-appapi.voicestudio.sh"
    env = {"VOICESTUDIO_CONTENT_REPORTS_URL": "https://staging.example:8443"}
    assert prepare_resources.content_reports_url(env, dotenv) == "https://staging.example:8443"
    with pytest.raises(ValueError):
        prepare_resources.content_reports_url({"VOICESTUDIO_CONTENT_REPORTS_URL": "http://dev-appapi.voicestudio.sh"}, dotenv)


def test_info_plist_carries_the_content_reports_url(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICESTUDIO_CONTENT_REPORTS_URL", "https://dev-appapi.voicestudio.sh")
    prepare_resources.prepare(tmp_path / "bundle", tmp_path / "Info.plist")
    info = plistlib.loads((tmp_path / "Info.plist").read_bytes())
    assert info["VoiceStudioContentReportsURL"] == "https://dev-appapi.voicestudio.sh"


def test_host_inference_prepares_all_runtime_headers_before_compiling_swift():
    # __has_include compiles no-runtime stubs when the generated headers are
    # absent. Adding a header later does not invalidate SwiftPM's cached C object.
    workflow = yaml.load((ROOT / ".github/workflows/mobile.yml").read_text(), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["ios-inference"]["steps"]
    prepare = next(i for i, step in enumerate(steps) if "mobile/native/ios/build.sh macos-runtimes" in step.get("run", ""))
    first_compile = next(i for i, step in enumerate(steps) if any(command in step.get("run", "") for command in ("real-inference.sh", "check-swift.sh")))
    assert prepare < first_compile, "Provision every runtime header before SwiftPM compiles VSNative.c"
    assert "if" not in steps[prepare], "Cache hits must restore generated headers too"
