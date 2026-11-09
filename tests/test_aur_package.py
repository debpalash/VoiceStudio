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


def test_launcher_runs_the_executable_electron_builder_produces():
    recipe = (ROOT / "packaging/aur/voicestudio-git/PKGBUILD").read_text(encoding="utf-8")
    config = (ROOT / "electron/electron-builder.config.mjs").read_text(encoding="utf-8")
    executable = re.search(r"linuxExecutableName: '([^']+)'", config)
    assert executable, "electron-builder no longer names the Linux executable"
    assert re.search(rf"^exec /opt/VoiceStudio/{re.escape(executable[1])} \"\$@\"$", recipe, re.M)


def test_ci_builds_the_checked_out_source_and_launches_the_desktop_binary():
    """The PKGBUILD's source is GitHub main; CI must build the PR's own tree with it.

    Building main with a PR's recipe tested a launcher against an executable
    the PR had renamed, and launching /usr/bin/voicestudio through Playwright
    reaches the terminal command, not Electron.
    """
    workflow = (ROOT / ".github/workflows/aur-package.yml").read_text(encoding="utf-8")
    assert "git+file://$GITHUB_WORKSPACE" in workflow
    desktop = re.search(r"join\(context\.appOutDir, '([^']+)'\)", (ROOT / "electron/cli-bundle.mjs").read_text(encoding="utf-8"))
    assert desktop, "cli-bundle.mjs no longer names the Linux desktop binary"
    assert f"VOICESTUDIO_PACKAGED_EXE=/opt/VoiceStudio/{desktop[1]}" in workflow
    assert "/usr/bin/voicestudio version" in workflow
