"""Offline support content and native packaging share one committed catalog."""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "mobile/contract"


def lookup(data, key):
    for part in key.split("."):
        data = data[part]
    return data


def test_support_catalog_uses_existing_donation_destinations():
    data = json.loads((CONTRACT / "support.json").read_text(encoding="utf-8"))
    source = (ROOT / "electron/src/shared/utils/donateLinks.js").read_text(encoding="utf-8")
    assert {row["url"] for row in data["donations"]} == set(re.findall(r"export const \w+ = '(https://[^']+)'", source))
    assert len({row["id"] for row in data["projects"]}) == len(data["projects"])
    assert {row["name"] for row in data["projects"]} == {"OpenGTM", "Opal", "Supermetry", "Spicrawl"}
    for row in data["donations"] + data["projects"]:
        assert urlparse(row["url"]).scheme == "https"
        assert urlparse(row["url"]).hostname


def test_every_support_label_is_translated_in_every_loaded_catalog():
    support = json.loads((CONTRACT / "support.json").read_text(encoding="utf-8"))
    shared = json.loads((CONTRACT / "strings.mobile.en.json").read_text(encoding="utf-8"))
    keys = {"mobile.support." + key for key in shared["mobile"]["support"]}
    keys |= {row["detailKey"] for row in support["donations"] + support["projects"]}
    keys |= {row["tierKey"] for row in support["projects"]}
    for path in (ROOT / "electron/src/renderer/src/i18n/locales").glob("*.json"):
        catalog = json.loads(path.read_text(encoding="utf-8"))
        for key in keys:
            assert isinstance(lookup(catalog, key), str) and lookup(catalog, key).strip(), (path.name, key)
        if path.stem == "en":
            assert catalog["mobile"]["support"] == shared["mobile"]["support"]


def test_ios_resources_bundle_identical_support_content_and_offline_artwork(tmp_path):
    import runpy

    prepare = runpy.run_path(str(ROOT / "mobile/ios/scripts/prepare_resources.py"))
    output = tmp_path / "bundle"
    prepare["prepare"](output, tmp_path / "Info.plist")
    assert (output / "Catalogs/support.json").read_bytes() == (CONTRACT / "support.json").read_bytes()
    for project in json.loads((CONTRACT / "support.json").read_text(encoding="utf-8"))["projects"]:
        image = CONTRACT / "support-artwork" / project["artwork"]
        assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert int.from_bytes(image.read_bytes()[16:20], "big") >= 144


