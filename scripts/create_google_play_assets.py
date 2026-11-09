#!/usr/bin/env python3
"""Render the Google Play icon and feature graphic from the repo's brand sources.

Inputs are the app logo, the phone Home artwork, the generated dark theme and the
bundled Inter font, so the store graphics follow the app instead of a hand edit.
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "store/google-play"
FONT = ROOT / "mobile/android/core/designsystem/src/main/res/font/inter_variable.ttf"
FEATURE_SIZE = (1024, 500)


def dark_colors():
    palettes = json.loads((ROOT / "mobile/contract/tokens.json").read_text())["palettes"]
    colors = next(p for p in palettes if p["id"] == "default")["colors"]["dark"]
    return {k: "#" + v[1:7] for k, v in colors.items()}


def font(size, weight):
    face = ImageFont.truetype(str(FONT), size)
    axes = face.get_variation_axes()
    face.set_variation_by_axes([weight if axis["name"] in (b"Weight", "Weight") else axis["default"] for axis in axes])
    return face


def icon():
    # Play masks a full-bleed square, so draw what the adaptive launcher icon shows
    # (mobile/android/scripts/generate_brand_resources.py): the logo scaled to 0.9
    # of 64 units, 25.2 units in from each edge of 108, on the theme canvas.
    size, scale = 512, 512 / 108
    image = Image.new("RGB", (size, size), dark_colors()["canvas"])
    side = round(64 * 0.9 * scale)
    logo = Image.open(ROOT / "docs/logo.png").convert("RGBA").resize((side, side), Image.LANCZOS)
    image.paste(logo, (round(25.2 * scale),) * 2, logo)
    return image


def feature_graphic():
    colors = dark_colors()
    art = Image.open(ROOT / "electron/public/signal-field.webp").convert("RGB")
    art = ImageOps.fit(art, FEATURE_SIZE, Image.LANCZOS)
    # Darken the left side so the wordmark stays readable over the artwork.
    shade = Image.linear_gradient("L").rotate(90).resize(FEATURE_SIZE)
    image = Image.composite(art, Image.new("RGB", FEATURE_SIZE, colors["canvas"]), shade)
    logo = Image.open(ROOT / "docs/logo.png").convert("RGBA").resize((132, 132), Image.LANCZOS)
    image.paste(logo, (72, 112), logo)
    draw = ImageDraw.Draw(image)
    draw.text((72, 270), "VoiceStudio", font=font(76, 700), fill=colors["text"])
    draw.text((74, 366), "Voice cloning, dictation and dubbing on your phone",
              font=font(28, 500), fill=colors["text"])
    draw.text((74, 408), "Runs offline once models are downloaded", font=font(24, 400), fill=colors["textMuted"])
    return image


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    icon().save(OUT / "icon-512.png", optimize=True)
    feature_graphic().save(OUT / "feature-graphic-1024x500.png", optimize=True)


if __name__ == "__main__":
    main()
