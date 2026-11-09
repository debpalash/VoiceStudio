#!/usr/bin/env python3
"""Resource regressions runnable with CLT; no iOS SDK or image dependency."""
import json
import pathlib
import plistlib
import struct
import tempfile
import unittest

import prepare_resources as resources


class BrandResources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parent = resources.IOS / ".generated"
        parent.mkdir(exist_ok=True)
        cls.directory = tempfile.TemporaryDirectory(dir=parent)
        cls.root = pathlib.Path(cls.directory.name)
        cls.assets = cls.root / "Brand.xcassets"
        resources.prepare(cls.root / "bundle", cls.root / "Info.plist")
        resources.prepare_brand_assets(cls.assets)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_extension_plists_have_required_installer_metadata(self):
        for name in ("VoiceStudioShare", "VoiceStudioWidget"):
            info = plistlib.loads((resources.IOS / name / "Info.plist").read_bytes())
            for key in ("CFBundleName", "CFBundleIdentifier", "CFBundleExecutable", "CFBundleVersion", "CFBundleShortVersionString"):
                self.assertTrue(info.get(key), f"{name}: missing {key}")
            self.assertEqual(info["CFBundlePackageType"], "XPC!")
            self.assertTrue(info["NSExtension"]["NSExtensionPointIdentifier"])

    def test_icon_is_opaque_1024_and_registered(self):
        icon = resources.IOS / "VoiceStudio/Resources/Assets.xcassets/AppIcon.appiconset"
        self.assertFalse((self.assets / "AppIcon.appiconset").exists())
        entry = json.loads((icon / "Contents.json").read_text())["images"][0]
        data = (icon / entry["filename"]).read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", data[16:24]), (1024, 1024))
        self.assertEqual(data[25], 2)  # RGB, no alpha channel (App Store requirement).
        self.assertEqual(entry["platform"], "ios")
        self.assertEqual(entry["size"], "1024x1024")

    def test_launch_uses_repo_logo_and_generated_canvas(self):
        info = plistlib.loads((self.root / "Info.plist").read_bytes())["UILaunchScreen"]
        self.assertEqual(info["UIImageName"], "LaunchLogo")
        self.assertEqual(info["UIColorName"], "LaunchCanvas")
        images = json.loads((self.assets / "LaunchLogo.imageset/Contents.json").read_text())["images"]
        self.assertEqual(len(images), 3)
        for scale, image in enumerate(images, start=1):
            png = (self.assets / "LaunchLogo.imageset" / image["filename"]).read_bytes()
            self.assertEqual(struct.unpack(">II", png[16:24]), (128 * scale, 128 * scale))
        colors = json.loads((self.assets / "LaunchCanvas.colorset/Contents.json").read_text())["colors"]
        tokens = json.loads((resources.ROOT / "mobile/contract/tokens.json").read_text())
        palette = next(p for p in tokens["palettes"] if p["id"] == "default")["colors"]
        for value, appearance in zip(colors, ["light", "dark"]):
            actual = value["color"]["components"]
            expected = palette[appearance]["canvas"].lstrip("#")
            for index, channel in enumerate(["red", "green", "blue", "alpha"]):
                self.assertAlmostEqual(float(actual[channel]), int(expected[2 * index:2 * index + 2], 16) / 255)

    def test_info_plist_carries_full_root_version_for_content_reports(self):
        info = plistlib.loads((self.root / "Info.plist").read_bytes())
        version = json.loads((resources.ROOT / "package.json").read_text())["version"]
        self.assertEqual(info["VoiceStudioAppVersion"], version)
        self.assertEqual(info["CFBundleShortVersionString"], resources.marketing_version(version))

    def test_catalogue_bundles_names_and_phone_consent_note(self):
        catalogue = json.loads((self.root / "bundle/Catalogs/models.json").read_text())
        self.assertTrue(all(model["name"].strip() for model in catalogue["models"]))
        self.assertEqual(json.loads((self.root / "bundle/Catalogs/design.json").read_text()), json.loads((resources.ROOT / "mobile/contract/design.json").read_text()))
        shared = json.loads((self.root / "bundle/Catalogs/strings.mobile.en.json").read_text())
        self.assertEqual(shared["mobile"]["consent"]["phone_note"], "Saved on this phone. Nothing is sent.")

    def test_microphone_reason_reuses_shared_local_and_computer_privacy(self):
        catalogs = self.root / "bundle/Catalogs"
        desktop = json.loads((catalogs / "en.json").read_text())
        shared = json.loads((catalogs / "strings.mobile.en.json").read_text())
        expected = "\n".join([desktop["permissions"]["microphone_why"], shared["mobile"]["local"]["privacy"]])
        info = plistlib.loads((self.root / "Info.plist").read_bytes())
        self.assertEqual(info["NSMicrophoneUsageDescription"], expected)
        self.assertIn(json.dumps(expected), (self.root / "bundle/en.lproj/InfoPlist.strings").read_text())

    def test_native_licences_are_bundled_without_xcode_artifacts(self):
        legal = self.root / "bundle/NativeLicences"
        entries = json.loads((legal / "sources.json").read_text())
        names = {entry["name"] for entry in entries}
        self.assertTrue({"espeak-ng-for-piper", "piper-phonemize", "kaldi-native-fbank",
                         "kissfft", "kaldifst", "eigen", "llama.cpp", "llama-ggml",
                         "omnivoice.cpp", "omnivoice-ggml"} <= names)
        self.assertIn("GNU GENERAL PUBLIC LICENSE", (legal / "espeak-ng-for-piper.txt").read_text())
        corresponding = (legal / "Corresponding-source.txt").read_text()
        self.assertIn("ed530aa113046142eb5115cf2fc9157854d0ffe1", corresponding)
        for entry in entries:
            self.assertIn(entry["source"], corresponding)
        pins = json.loads((resources.ROOT / "mobile/native/pins.json").read_text())
        resources.validate_native_notices(legal,pins)
        (legal / "llama.cpp.txt").write_text("broken")
        with self.assertRaisesRegex(ValueError,"Native licence changed"):
            resources.validate_native_notices(legal,pins)



if __name__ == "__main__":
    unittest.main()
