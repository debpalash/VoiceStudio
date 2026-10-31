"""Apple requires declarations in the executable's own bundle, including SDKs."""
import importlib.util
import plistlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(relative):
    spec = importlib.util.spec_from_file_location("privacy_packager", ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reasons(path):
    manifest = plistlib.loads(path.read_bytes())
    assert manifest["NSPrivacyTracking"] is False
    assert not manifest.get("NSPrivacyTrackingDomains", [])
    return {entry["NSPrivacyAccessedAPIType"]: set(entry["NSPrivacyAccessedAPITypeReasons"])
            for entry in manifest["NSPrivacyAccessedAPITypes"]}


def test_generated_app_bundle_declares_preferences_and_local_file_access(tmp_path):
    prepare = load("mobile/ios/scripts/prepare_resources.py")
    output = tmp_path / "VoiceStudio.app"
    prepare.prepare(output, tmp_path / "Info.plist")
    assert reasons(output / "PrivacyInfo.xcprivacy") == {
        "NSPrivacyAccessedAPICategoryUserDefaults": {"CA92.1"},
        "NSPrivacyAccessedAPICategoryDiskSpace": {"E174.1"},
        "NSPrivacyAccessedAPICategoryFileTimestamp": {"C617.1", "3B52.1"},
    }


@pytest.mark.parametrize("name", ["OmniVoice", "llama"])
def test_dynamic_framework_packager_carries_its_own_declaration(tmp_path, monkeypatch, name):
    builder = load("mobile/native/ios/build.py")
    monkeypatch.setattr(builder, "run", lambda *args, **kwargs: None)
    library = tmp_path / "runtime.dylib"
    library.write_bytes(b"packaging fixture; never linked or executed")
    bundle = builder.framework(name, library, [], tmp_path / "output", "iPhoneOS")
    assert reasons(bundle / "PrivacyInfo.xcprivacy") == {
        "NSPrivacyAccessedAPICategoryFileTimestamp": {"C617.1"},
    }


@pytest.mark.parametrize("target,expected", [
    ("VoiceStudioShare", {"NSPrivacyAccessedAPICategoryFileTimestamp": {"3B52.1"}}),
    ("VoiceStudioWidget", {}),
])
def test_extension_declarations_match_their_actual_access(target, expected):
    assert reasons(ROOT / "mobile/ios" / target / "PrivacyInfo.xcprivacy") == expected


def test_archive_gate_rejects_missing_dynamic_framework_manifest(tmp_path, monkeypatch):
    prepare = load("mobile/ios/scripts/prepare_resources.py")
    builder = load("mobile/native/ios/build.py")
    verifier = load("mobile/ios/scripts/validate_privacy_manifests.py")
    monkeypatch.setattr(builder, "run", lambda *args, **kwargs: None)
    app = tmp_path / "VoiceStudio.app"
    prepare.prepare(app, tmp_path / "Info.plist")
    library = tmp_path / "runtime.dylib"
    library.write_bytes(b"packaging fixture; never linked or executed")
    bundle = builder.framework("OmniVoice", library, [], app / "Frameworks", "iPhoneOS")
    assert verifier.validate(app) == 2
    declaration = bundle / "PrivacyInfo.xcprivacy"
    declaration.unlink()
    with pytest.raises(ValueError, match="Privacy manifest is missing: OmniVoice.framework"):
        verifier.validate(app)


def test_share_inbox_uses_one_configured_group_for_signing_and_runtime(tmp_path):
    import yaml

    project = yaml.safe_load((ROOT / "mobile/ios/VoiceStudio/project.yml").read_text())
    group = project["settings"]["base"]["VS_APP_GROUP_IDENTIFIER"]
    assert group == "group.sh.voicestudio.mobile.development"
    assert project["targets"]["VoiceStudio"]["settings"]["base"]["PRODUCT_BUNDLE_IDENTIFIER"] == "sh.voicestudio.mobile"
    assert project["targets"]["VoiceStudioShare"]["settings"]["base"]["PRODUCT_BUNDLE_IDENTIFIER"] == "sh.voicestudio.mobile.development.share"
    generated_info = tmp_path / "Info.plist"
    load("mobile/ios/scripts/prepare_resources.py").prepare(tmp_path / "app", generated_info)
    for target, info in [("VoiceStudio", generated_info),
                         ("VoiceStudioShare", ROOT / "mobile/ios/VoiceStudioShare/Info.plist")]:
        directory = ROOT / "mobile/ios" / target
        entitlement = plistlib.loads((directory / f"{target}.entitlements").read_bytes())
        runtime = plistlib.loads(info.read_bytes())["VoiceStudioAppGroupIdentifier"]
        assert entitlement["com.apple.security.application-groups"] == [runtime]
        assert runtime == "$(VS_APP_GROUP_IDENTIFIER)"
