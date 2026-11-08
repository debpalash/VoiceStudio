"""VoiceStudio release-brand and source-launch contracts."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from packaging.version import Version


ROOT = Path(__file__).resolve().parents[1]
CURRENT_VERSION = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]


def test_current_version_is_in_lockstep_everywhere() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == CURRENT_VERSION

    mirrors = {
        "pyproject.toml": r'(?m)^version = "([^"]+)"',
        "backend/core/version.py": r'(?m)^_FALLBACK_VERSION = "([^"]+)"',
    }
    for path, pattern in mirrors.items():
        match = re.search(pattern, (ROOT / path).read_text(encoding="utf-8"))
        assert match and match.group(1) == CURRENT_VERSION, path

    lock_contracts = {"uv.lock": r'name = "omnivoice"\s+version = "([^"]+)"'}
    for path, pattern in lock_contracts.items():
        match = re.search(pattern, (ROOT / path).read_text(encoding="utf-8"))
        # uv writes the PEP 440 form of a beta (0.5.9b1 for 0.5.9-beta.1).
        assert match and Version(match.group(1)) == Version(CURRENT_VERSION), path


def test_visible_brand_surfaces_say_voicestudio() -> None:
    visible_files = ("electron/build/Info.plist", "electron/src/renderer/index.html")
    product_name = json.loads((ROOT / "config/app.json").read_text(encoding="utf-8"))["productName"]
    assert product_name == "VoiceStudio"
    for path in visible_files:
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "__APP_PRODUCT_NAME__" in text, path
        assert "OmniVoice needs" not in text, path
        assert "OmniVoice may" not in text, path
        assert "OmniVoice Gallery" not in text, path

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "**VoiceStudio** (default, powered by k2-fsa/OmniVoice)" in readme
    assert "**OmniVoice** (default)" not in readme


def test_brand_mark_is_shared_and_fills_the_icon() -> None:
    mark = (ROOT / "electron/src/shared/components/brand/VoiceStudioMark.jsx").read_text(encoding="utf-8")
    header = (ROOT / "electron/src/shared/components/Header.jsx").read_text(encoding="utf-8")
    about = (ROOT / "electron/src/shared/components/settings/AboutTab.jsx").read_text(encoding="utf-8")
    logo = (ROOT / "docs/logo.svg").read_text(encoding="utf-8")
    favicon = (ROOT / "electron/public/favicon.svg").read_text(encoding="utf-8")

    signature = "M6 34c4 0 5-7 9-7"
    assert signature in mark
    assert signature in logo
    assert signature in favicon
    assert "<VoiceStudioMark" in header
    assert "<VoiceStudioMark" in about
    assert 'data-testid="voice-studio-logo"' in header

    # The previous icon devoted most of its canvas to an empty ring. The new
    # mark uses the full tile and keeps only a narrow 2-unit outer margin.
    assert 'x="2" y="2" width="60" height="60"' in logo
    assert "<circle" not in logo
    assert 'src="docs/logo.png"' in (ROOT / "README.md").read_text(encoding="utf-8")


def test_waveform_stroke_is_never_filled() -> None:
    # An SVG path fills black unless told otherwise; without fill="none" the
    # waveform's closed curves painted a black band across every icon.
    signature = "M6 34c4 0 5-7 9-7"
    tracked = subprocess.run(["git", "ls-files", "*.svg"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    svgs = [ROOT / name for name in tracked if signature in (ROOT / name).read_text(encoding="utf-8")]
    assert len(svgs) >= 3
    for svg in svgs:
        stroke = re.search(rf'<path d="{re.escape(signature)}[^>]*>', svg.read_text(encoding="utf-8"))
        assert stroke and 'fill="none"' in stroke.group(0), svg


def test_raster_icons_have_no_black_band() -> None:
    from PIL import Image

    # Regenerated from docs/logo.svg by scripts/render_brand_icons.py.
    for name in ("docs/logo.png", "docs/logo-256.png", "electron/build/icons/icon.png", "electron/build/icons/32x32.png"):
        icon = Image.open(ROOT / name).convert("RGBA")
        width, height = icon.size
        assert icon.getpixel((width // 2, int(height * 0.53)))[:3] != (0, 0, 0), name


@pytest.mark.parametrize("name", ("vs_logo", "vs_launcher_foreground", "vs_launcher_monochrome"))
def test_android_vectors_keep_unpainted_waveform_transparent(tmp_path: Path, name: str) -> None:
    import runpy
    import xml.etree.ElementTree as ET

    generator = runpy.run_path(str(ROOT / "mobile/android/scripts/generate_brand_resources.py"))
    generator["generate"](ROOT, tmp_path)
    android = "{http://schemas.android.com/apk/res/android}"
    vector = ET.parse(tmp_path / f"drawable/{name}.xml").getroot()
    waveform = next(p for p in vector.iter("path") if p.get(android + "pathData", "").startswith("M6 34c4 0 5-7 9-7"))
    # SVG's `none` is not an Android color; monochrome must preserve unpainted areas too.
    assert waveform.get(android + "fillColor") in (None, "#00000000", "@android:color/transparent")
    assert waveform.get(android + "strokeColor") or any(
        attr.get("name") == "android:strokeColor" for attr in waveform
    )


@pytest.mark.parametrize("event", ("push", "pull_request"))
def test_canonical_logo_changes_run_mobile_builds(event: str) -> None:
    from fnmatch import fnmatchcase

    import yaml

    workflow = yaml.load((ROOT / ".github/workflows/mobile.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert any(fnmatchcase("docs/logo.svg", pattern) for pattern in workflow["on"][event]["paths"])


def test_python_package_metadata_points_to_voicestudio() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'Homepage = "https://github.com/debpalash/VoiceStudio"' in pyproject
    assert 'Repository = "https://github.com/debpalash/VoiceStudio"' in pyproject
    assert '"Upstream TTS Model" = "https://github.com/k2-fsa/OmniVoice"' in pyproject


def test_engine_help_names_the_app_not_the_upstream_model() -> None:
    paths = (
        "backend/engines/confucius4/__init__.py",
        "backend/engines/confucius4/bootstrap.py",
        "backend/engines/dots_tts/__init__.py",
        "backend/engines/dots_tts/bootstrap.py",
        "backend/engines/indextts/__init__.py",
        "backend/engines/indextts/bootstrap.py",
        "backend/engines/moss_tts_v15/__init__.py",
        "backend/engines/moss_tts_v15/bootstrap.py",
    )
    stale_help = re.compile(r"(?:restart|reinstall|re-launch|Run) OmniVoice")
    for path in paths:
        text = (ROOT / path).read_text(encoding="utf-8")
        assert not stale_help.search(text), path


def test_compatibility_identifiers_stay_stable() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["name"] == "omnivoice-studio-monorepo"
    assert 'name = "omnivoice"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    app_config = json.loads((ROOT / "config/app.json").read_text(encoding="utf-8"))
    assert app_config["appId"] == "com.voicestudio.desktop"


def test_active_source_launch_is_electron_and_web_ports_clean_quietly() -> None:
    scripts = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert scripts["dev"] == "bun run --cwd electron dev"
    assert not any("tauri" in name for name in scripts)
    command = scripts["predev:web"]
    assert command.endswith("bun scripts/clear-dev-ports.mjs")
    assert "|| true" not in command


def test_icon_rail_has_no_static_section_captions_and_keeps_air_between_items() -> None:
    rail = (ROOT / "electron/src/shared/components/NavRail.jsx").read_text(encoding="utf-8")
    for stale_caption in ("Start", "Create", "Workflows", "Reference"):
        assert stale_caption not in rail
    assert "pt-[18px]" in rail
    assert "gap-[9px]" in rail


def test_electron_launch_preserves_an_existing_backend():
    scripts = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]
    for name in ("predev", "predesktop"):
        assert "clear-dev-ports" not in scripts[name]
        assert "desktop-runtime-preflight" not in scripts[name]
    assert scripts["dev"] == "bun run --cwd electron dev"
    assert scripts["desktop-prod:run"] == "bun run start"
    assert scripts["start"] == "bun run --cwd electron start"
