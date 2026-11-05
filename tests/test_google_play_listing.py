"""The Google Play listing stays within Play's limits and matches the Android app."""

import json
import re
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LISTING = json.loads((ROOT / "store/google-play/listing.json").read_text())


def test_text_fits_play_limits():
    text = LISTING["listing"]
    assert 0 < len(text["title"]) <= 30
    assert 0 < len(text["shortDescription"]) <= 80
    assert 0 < len("\n".join(text["fullDescription"])) <= 4000


def test_package_name_matches_the_app():
    gradle = (ROOT / "mobile/android/app/build.gradle.kts").read_text()
    assert f'applicationId = "{LISTING["packageName"]}"' in gradle


def test_every_manifest_permission_is_declared():
    # A new permission needs its Play Console reason written before it ships. Library
    # modules merge into the app, so read every module's manifest (#3257 review).
    manifests = [p for p in (ROOT / "mobile/android").rglob("src/main/AndroidManifest.xml") if "build" not in p.parts]
    used = {name for path in manifests for name in re.findall(r'uses-permission android:name="([^"]+)"', path.read_text())}
    assert "android.permission.FOREGROUND_SERVICE_MEDIA_PROCESSING" in used
    assert used == set(LISTING["permissions"])


def test_graphics_meet_play_specs():
    icon = Image.open(ROOT / LISTING["graphics"]["icon"])
    assert icon.format == "PNG" and icon.size == (512, 512)
    feature = Image.open(ROOT / LISTING["graphics"]["featureGraphic"])
    # Play rejects a feature graphic with transparency.
    assert feature.format == "PNG" and feature.size == (1024, 500) and feature.mode == "RGB"
    for path in (LISTING["graphics"]["icon"], LISTING["graphics"]["featureGraphic"]):
        assert (ROOT / path).stat().st_size <= 1024 * 1024


def languages(capability):
    models = json.loads((ROOT / "mobile/contract/models.json").read_text())["models"]
    return set().union(*(m["languages"] for m in models if m["capability"] == capability))


def test_store_languages_match_the_phone_models():
    # Each task is limited by the models it runs: dictation streams with `asr-stream`;
    # cloning transcribes the reference with `asr`; dubbing also translates (#3257 review).
    description = "\n".join(LISTING["listing"]["fullDescription"])
    clone = languages("clone") & languages("asr")
    assert clone & languages("translate") == clone
    assert f"Voice cloning and dubbing cover {len(clone)} languages" in description
    assert languages("asr-stream") == {"en"}, "phone dictation gained languages; update the listing"
    assert "Dictation on the phone is English" in description
    assert {"en", "zh"} <= languages("tts")
    assert f"Preset speech covers {len(languages('tts'))} languages, depending on the model." in description


def test_data_safety_declares_the_report_the_app_sends():
    # The Report sheet sends user-written text to VoiceStudio (mobile/contract/SPEC.md).
    spec = (ROOT / "mobile/contract/SPEC.md").read_text()
    assert "## Reporting generated content" in spec
    safety = LISTING["dataSafety"]
    assert safety["collectsData"] and not safety["sharesData"] and safety["encryptedInTransit"]
    assert [item["optional"] for item in safety["collected"]] == [True]
    assert "reports you choose to send" in "\n".join(LISTING["listing"]["fullDescription"])


def test_in_app_privacy_line_names_every_network_use():
    # The phone's Home/Settings privacy line must list what the data safety form
    # declares; it missed content reports when they shipped (#3268).
    strings = json.loads((ROOT / "mobile/contract/strings.mobile.en.json").read_text())
    line = strings["mobile"]["local"]["privacy"]
    assert LISTING["dataSafety"]["collectsData"]
    for use in ("model downloads", "reports", "paired computer"):
        assert use in line, (use, line)
