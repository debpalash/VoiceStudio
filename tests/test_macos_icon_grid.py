"""macOS icon artwork keeps Apple's icon-grid margin (#2744).

The Dock, Launchpad and Finder scale every app's whole canvas to the same size,
so a tile that fills the canvas renders visibly larger than every other app.
Apple's grid centres the rounded-square body at 824×824 of a 1024×1024 canvas.
"""

from pathlib import Path

import pytest
from PIL import Image

ICONS = Path(__file__).resolve().parents[1] / "electron/build/icons"
APPLE_GRID_BODY = 824 / 1024


@pytest.mark.parametrize("name", ["icon.icns", "icon-macos.png"])
def test_macos_icon_body_follows_apple_grid(name):
    """The visible tile spans ~824/1024 of the canvas and sits centred."""
    with Image.open(ICONS / name) as image:
        image.load()  # ICNS opens at its largest (1024 px) representation
        alpha = image.convert("RGBA").getchannel("A").point(lambda a: 255 if a > 20 else 0)
        left, top, right, bottom = alpha.getbbox()
        width, height = image.size
    for body, canvas in ((right - left, width), (bottom - top, height)):
        assert body / canvas == pytest.approx(APPLE_GRID_BODY, abs=0.01), (
            f"{name}: tile spans {body}/{canvas} px; macOS expects ~{APPLE_GRID_BODY:.0%}"
        )
    assert left == pytest.approx(width - right, abs=2), f"{name}: tile is not centred"
    assert top == pytest.approx(height - bottom, abs=2), f"{name}: tile is not centred"
