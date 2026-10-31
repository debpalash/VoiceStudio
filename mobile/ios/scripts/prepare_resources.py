#!/usr/bin/env python3
"""Copy authoritative catalogs; localize OS permission prompts; derive version from root."""
import argparse
import hashlib
import json
import os
import pathlib
import plistlib
import re
import runpy
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[3]
IOS = ROOT / "mobile/ios"
PRODUCTION_REPORTS_URL = "https://appapi.voicestudio.sh"
REPORTS_URL = re.compile(r"https://[A-Za-z0-9.-]+(?::\d+)?(?:/[A-Za-z0-9._~/-]*)?")


def prepare_brand_assets(output):
    """Use macOS CoreGraphics, available with CLT, without an image dependency."""
    source = IOS / "scripts/generate-brand-assets.swift"
    cache = IOS / ".cache/brand-assets"
    cache.mkdir(parents=True, exist_ok=True)
    renderer = cache / "generate-brand-assets"
    if not renderer.exists() or renderer.stat().st_mtime < source.stat().st_mtime:
        subprocess.run(["xcrun", "swiftc", "-module-cache-path", str(cache / "modules"), str(source), "-o", str(renderer)], check=True)
    subprocess.run([str(renderer), str(ROOT / "docs/logo.png"), str(ROOT / "mobile/contract/tokens.json"), str(output)], check=True)

    contributors = runpy.run_path(str(ROOT / "scripts/prepare_mobile_contributors.py"))
    contributors["prepare_ios_icons"](ROOT, output)

    artwork = output / "HomeArtwork.imageset"
    artwork.mkdir(parents=True, exist_ok=True)
    subprocess.run(["sips", "-s", "format", "png", str(ROOT / "electron/public/signal-field.webp"), "--out", str(artwork / "artwork.png")], check=True, stdout=subprocess.DEVNULL)
    (artwork / "Contents.json").write_text(json.dumps({"images": [{"filename": "artwork.png", "idiom": "universal"}], "info": {"version": 1, "author": "xcode"}}), encoding="utf-8")


def dotenv(path):
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if match := re.fullmatch(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*", line):
                values[match[1]] = match[2].strip("'\"")
    return values


def content_reports_url(env=os.environ, dotenv_path=ROOT / "mobile/.env"):
    """Production unless VOICESTUDIO_CONTENT_REPORTS_URL, from the environment or mobile/.env, overrides it."""
    key = "VOICESTUDIO_CONTENT_REPORTS_URL"
    url = ((env.get(key) or "").strip() or dotenv(dotenv_path).get(key, "")).strip().rstrip("/") or PRODUCTION_REPORTS_URL
    if not REPORTS_URL.fullmatch(url):
        raise ValueError(f"{key} must be an https URL, got {url!r}")
    return url


def merge(base, addition):
    for key, value in addition.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value


def lookup(catalog, key):
    value = catalog
    for part in key.split("."):
        value = value.get(part, {}) if isinstance(value, dict) else {}
    return value if isinstance(value, str) else None


def validate_native_notices(directory, pins):
    manifest = json.loads((directory / "sources.json").read_text(encoding="utf-8"))
    expected = {"sherpa-onnx", "omnivoice.cpp", "omnivoice-ggml", "llama.cpp", "llama-ggml",
                "espeak-ng-for-piper", "piper-phonemize", "kaldi-native-fbank", "kissfft",
                "kaldi-decoder", "kaldifst", "openfst", "simple-sentencepiece", "json", "hclust-cpp", "eigen", "onnxruntime"}
    if {entry["name"] for entry in manifest} != expected:
        raise ValueError("Native licence set is incomplete; run mobile/native/ios/prepare-notices.py")
    for entry in manifest:
        text = (directory / (entry["name"] + ".txt")).read_bytes()
        if hashlib.sha256(text).hexdigest() != entry["notice_sha256"]:
            raise ValueError("Native licence changed: " + entry["name"])
        if entry["name"] in pins:
            pin = pins[entry["name"]]["source"]
            if entry["source"] != pin["repo"] + "/tree/" + pin["commit"]:
                raise ValueError("Native licence pin drift: " + entry["name"])
    if not (directory / "Corresponding-source.txt").is_file():
        raise ValueError("Missing GPL corresponding sources")


def copy_native_notices(current, cached, output):
    # Build caches may predate a checked-in licence update. Current notices win.
    if cached.is_dir():
        shutil.copytree(cached, output, dirs_exist_ok=True)
    shutil.copytree(current, output, dirs_exist_ok=True)


def marketing_version(version):
    # App Store Connect accepts only X.Y.Z, so a beta uploads as its X.Y.Z under a new build number.
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:-beta\.\d+)?", version)
    if not match:
        raise ValueError(f"Unsupported app version for iOS: {version}")
    return match.group(1)


def build_number(value):
    # TestFlight needs a new, increasing build number per upload: up to three integers.
    if not re.fullmatch(r"\d+(?:\.\d+){0,2}", value):
        raise ValueError(f"Invalid iOS build number: {value}")
    return value


def prepare(output, info):
    output.mkdir(parents=True, exist_ok=True)
    # Load the sibling by location: release checks also import this script by file path.
    workspace = runpy.run_path(str(IOS / "scripts/prepare_workspace_resources.py"))
    workspace["prepare"](output)
    legal = IOS / "VoiceStudio/Resources/NativeLicences"
    validate_native_notices(legal,json.loads((ROOT / "mobile/native/pins.json").read_text(encoding="utf-8")))
    notices = ROOT / "mobile/native/ios/build/xcframeworks/Notices"
    copy_native_notices(legal, notices, output / "NativeLicences")
    catalogs = output / "Catalogs"
    catalogs.mkdir(exist_ok=True)
    source = ROOT / "electron/src/renderer/src/i18n/locales"
    shutil.copy2(ROOT / "electron/src/shared/languages.json", catalogs / "languages.json")
    code_source = (ROOT / "electron/src/shared/utils/languages.js").read_text(encoding="utf-8")
    codes = {label: code for code, label in re.findall(r"\{ code: '([^']+)', label: '([^']+)' \}", code_source)}
    if not codes:
        raise ValueError("Desktop LANG_CODES format changed")
    (catalogs / "language-codes.json").write_text(json.dumps(codes, ensure_ascii=True), encoding="utf-8")
    model_catalogue = ROOT / "mobile/contract/models.json"
    if not model_catalogue.is_file():
        raise ValueError("Generate mobile/contract/models.json before building the app")
    shutil.copy2(model_catalogue, catalogs / "models.json")
    shutil.copy2(ROOT / "mobile/contract/design.json", catalogs / "design.json")
    shutil.copytree(ROOT / "mobile/contract/tts", catalogs / "tts", dirs_exist_ok=True)
    shutil.copy2(ROOT / "mobile/contract/community-gallery.json", catalogs / "community-gallery.json")
    shutil.copy2(ROOT / "mobile/contract/maintainer-cards.json", catalogs / "maintainer-cards.json")
    english = json.loads((source / "en.json").read_text(encoding="utf-8"))
    additions = sorted((ROOT / "mobile/contract").glob("strings*.en.json"))
    for path in additions:
        merge(english, json.loads(path.read_text(encoding="utf-8")))
        shutil.copy2(path, catalogs / path.name)
    permissions = {
        "CFBundleDisplayName": ("mobile.ios.app_name",),
        "NSCameraUsageDescription": ("mobile.permissions.camera",),
        "NSMicrophoneUsageDescription": ("permissions.microphone_why", "mobile.local.privacy"),
        "NSLocalNetworkUsageDescription": ("mobile.permissions.local_network",),
    }
    for path in sorted(source.glob("*.json")):
        shutil.copy2(path, catalogs / path.name)
        locale = json.loads(path.read_text(encoding="utf-8"))
        localized = output / (path.stem + ".lproj")
        localized.mkdir(exist_ok=True)
        # JSON string syntax provides the escaping required by .strings files.
        descriptions = {field: "\n".join(lookup(locale, key) or lookup(english, key) for key in keys) for field, keys in permissions.items()}
        lines = [f'{json.dumps(k)} = {json.dumps(v, ensure_ascii=True)};' for k, v in descriptions.items()]
        (localized / "InfoPlist.strings").write_text("\n".join(lines) + "\n", encoding="utf-8")
    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    data = plistlib.loads((IOS / "VoiceStudio/Info.plist").read_bytes())
    data["CFBundleShortVersionString"] = marketing_version(version)
    # The full root version (X.Y.Z or X.Y.Z-beta.N), which content reports send as app_version.
    data["VoiceStudioAppVersion"] = version
    data["VoiceStudioContentReportsURL"] = content_reports_url()
    data["CFBundleVersion"] = build_number(os.environ.get("VOICESTUDIO_IOS_BUILD_NUMBER", "1"))
    data["CFBundleLocalizations"] = sorted(path.stem for path in source.glob("*.json"))
    for field, keys in permissions.items():
        data[field] = "\n".join(lookup(english, key) for key in keys)
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_bytes(plistlib.dumps(data))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--info-output", required=True, type=pathlib.Path)
    parser.add_argument("--assets-output", type=pathlib.Path, default=IOS / ".generated/Brand.xcassets")
    args = parser.parse_args()
    prepare(args.output, args.info_output)
    prepare_brand_assets(args.assets_output)
