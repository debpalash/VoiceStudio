"""Packaged Electron builds must carry the web SPA used by Network Sharing."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_electron_bundle_carries_the_lan_frontend() -> None:
    builder = (ROOT / "electron/electron-builder.config.mjs").read_text(encoding="utf-8")
    runtime = (ROOT / "electron/src/main/runtime-project.ts").read_text(encoding="utf-8")
    root_package = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "from: '../frontend/dist'" in builder
    assert "to: 'frontend/dist'" in builder
    assert "'frontend'" in runtime.split("const SOURCES =", 1)[1].split("];", 1)[0]
    assert "bun run --cwd electron build:web" in root_package
