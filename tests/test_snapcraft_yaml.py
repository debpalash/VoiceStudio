"""snap/snapcraft.yaml (Snapcraft Builds) must request exactly the release snap's interfaces."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _builder_plugs() -> set[str]:
    text = (ROOT / "electron" / "electron-builder.config.mjs").read_text(encoding="utf-8")
    block = text[text.index("plugs: [") : text.index("],", text.index("plugs: ["))]
    plugs = set(re.findall(r"^\s*'([a-z0-9-]+)',", block, re.M))
    if "'browser-support'" in block:
        plugs.add("browser-support")
    return plugs


def test_snapcraft_yaml_matches_the_release_snap():
    snap = yaml.safe_load((ROOT / "snap" / "snapcraft.yaml").read_text(encoding="utf-8"))
    assert snap["name"] == "voicestudio"
    assert snap["confinement"] == "strict" and snap["base"] == "core22"
    aliases = {name: spec["interface"] for name, spec in (snap.get("plugs") or {}).items()}
    assert snap["plugs"]["browser-sandbox"]["allow-sandbox"] is False
    app_plugs = {aliases.get(p, p) for p in snap["apps"]["voicestudio"]["plugs"]}
    assert app_plugs == _builder_plugs()
    # snapd owns updates for a Store install.
    assert snap["apps"]["voicestudio"]["environment"]["VOICESTUDIO_DISABLE_UPDATER"] == "1"
    assert (ROOT / snap["icon"]).is_file()
