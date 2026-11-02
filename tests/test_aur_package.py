"""Arch launchers must identify the same app as Electron's canonical brand."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _desktop_entry():
    recipe = (ROOT / "packaging/aur/voicestudio-git/PKGBUILD").read_text(encoding="utf-8")
    match = re.search(r"applications/([^\"]+)[^\n]*<<'EOF'\n(.*?)\nEOF", recipe, re.S)
    assert match, "Missing installed desktop entry"
    fields = dict(line.split("=", 1) for line in match[2].splitlines() if "=" in line)
    return match[1], fields


def test_desktop_entry_matches_app_identity():
    name = json.loads((ROOT / "config/app.json").read_text(encoding="utf-8"))["productName"]
    filename, fields = _desktop_entry()
    assert filename == f"{name}.desktop"
    assert fields["Name"] == name
    assert fields["StartupWMClass"] == name


def test_ci_validates_the_installed_desktop_entry():
    filename, _ = _desktop_entry()
    workflow = (ROOT / ".github/workflows/aur-package.yml").read_text(encoding="utf-8")
    assert f"desktop-file-validate pkg/voicestudio-git/usr/share/applications/{filename}" in workflow
