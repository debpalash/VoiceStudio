#!/usr/bin/env python3
# /// script
# dependencies = ["pillow>=12.1.0", "resvg-py==0.5.0"]
# ///
"""Regenerate every raster brand icon from the canonical docs/logo.svg.

Run after changing the mark: uv run scripts/render_brand_icons.py
resvg rasterizes the SVG on any OS; the macOS ICNS needs iconutil, so it is
rebuilt only on macOS.
"""
import io
import pathlib
import shutil
import subprocess
import sys
import tempfile

import resvg_py
from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/logo.svg"
ICONS = ROOT / "electron/build/icons"
ICO_SIZES = [16, 24, 32, 48, 64, 256]
RECORDING = (0xEF, 0x44, 0x44, 255)


def rasterize(svg):
    png = resvg_py.svg_to_bytes(svg_string=svg.read_text(), width=1024, height=1024)
    return Image.open(io.BytesIO(bytes(png))).convert("RGBA")


def scaled(master, size):
    return master.resize((size, size), Image.LANCZOS)


def macos_master(master):
    # Apple's icon grid: the tile spans the centred 824 px of a 1024 px canvas,
    # so the icon matches other apps in the Dock, Launchpad and Finder (#2744).
    # The logo carries its own margin, so fit its visible tile, not the canvas.
    tile = master.crop(master.getchannel("A").point(lambda a: 255 if a > 20 else 0).getbbox())
    canvas = Image.new("RGBA", master.size, (0, 0, 0, 0))
    body = round(master.width * 824 / 1024)
    tile = tile.resize((body, body), Image.LANCZOS)
    offset = (master.width - body) // 2
    canvas.paste(tile, (offset, offset))
    return canvas


def recording(master):
    # The tray shows capture state with a red dot over the mark's lower right.
    icon = scaled(master, 128)
    ImageDraw.Draw(icon).ellipse((40, 40, 124, 124), fill=RECORDING, outline=(255, 255, 255, 255), width=6)
    return icon.resize((32, 32), Image.LANCZOS)


def icns(master, work, target):
    iconset = work / "icon.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        scaled(master, size).save(iconset / f"icon_{size}x{size}.png")
        scaled(master, size * 2).save(iconset / f"icon_{size}x{size}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(target)], check=True)


def main():
    master = rasterize(SOURCE)
    scaled(master, 512).save(ROOT / "docs/logo.png")
    scaled(master, 256).save(ROOT / "docs/logo-256.png")
    scaled(master, 512).save(ICONS / "icon.png")
    scaled(master, 32).save(ICONS / "32x32.png")
    mac = macos_master(master)
    scaled(mac, 512).save(ICONS / "icon-macos.png")
    recording(master).save(ICONS / "tray-recording.png")
    master.save(ICONS / "icon.ico", sizes=[(size, size) for size in ICO_SIZES])
    if sys.platform == "darwin" and shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            icns(mac, pathlib.Path(tmp), ICONS / "icon.icns")
    else:
        print("Skipped icon.icns: rebuild it on macOS (iconutil)")


if __name__ == "__main__":
    main()
