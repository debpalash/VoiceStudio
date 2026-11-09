#!/usr/bin/env python3
"""Generate native workspace labels from the same Electron locale catalog as the apps."""
import argparse
import json
import os
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[3]
KEYS = ("mobile.integration.shortcut_speak", "mobile.integration.shortcut_dictate", "mobile.integration.widget_description", "mobile.integration.text")


def flatten(obj, prefix=""):
    out = {}
    for key, value in obj.items():
        name = prefix + "." + key if prefix else key
        if isinstance(value, dict):
            out.update(flatten(value, name))
        elif isinstance(value, str):
            out[name] = value
    return out


def prepare(output):
    source = ROOT / "electron/src/renderer/src/i18n/locales"
    english = flatten(json.loads((source / "en.json").read_text(encoding="utf-8")))
    for path in sorted((ROOT / "mobile/contract").glob("strings*.en.json")):
        english.update(flatten(json.loads(path.read_text(encoding="utf-8"))))
    for path in sorted(source.glob("*.json")):
        catalog = flatten(json.loads(path.read_text(encoding="utf-8")))
        directory = output / (path.stem + ".lproj")
        directory.mkdir(parents=True, exist_ok=True)
        lines = [f'{json.dumps(key)} = {json.dumps(catalog.get(key, english.get(key, key)), ensure_ascii=True)};' for key in KEYS]
        (directory / "Localizable.strings").write_text("\n".join(lines) + "\n", encoding="utf-8")
    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:-beta\.\d+)?", version)
    if not match:
        raise ValueError("Unsupported app version")
    build = os.environ.get("VOICESTUDIO_IOS_BUILD_NUMBER", "1")
    if not re.fullmatch(r"\d+(?:\.\d+){0,2}", build):
        raise ValueError("Invalid iOS build number")
    generated = ROOT / "mobile/ios/.generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "Version.xcconfig").write_text(f"VS_MARKETING_VERSION = {match[1]}\nVS_BUILD_NUMBER = {build}\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=pathlib.Path)
    prepare(parser.parse_args().output)
