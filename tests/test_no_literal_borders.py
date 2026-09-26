"""Hard rule: no decorative literal-color borders in the frontend.

The app-wide border/divider removal (owner-approved) stripped every decorative
border, hairline, divider, and panel frame. Perceivability is preserved through
background/elevation cues (selection, inputs, chips) and the kept
``:focus-visible`` / ``--color-ring`` / ``--focus-ring`` focus indicators.

This guard fails if the *regression class* reappears — the hardcoded neutral
hairlines and inline border colors that ``--color-border`` token-zeroing can't
reach:

  1. Neutral (white / black) literal ``border[-x]:`` colors in ``index.css``.
  2. ``border-white/…`` / ``border-black/…`` Tailwind utilities, or
     ``border-[#…]`` / ``border-[rgb(a)(…)]`` / ``border-[hsl(…)]`` literal-color
     arbitrary utilities, in ``*.jsx`` / ``*.tsx``.
  3. Inline ``style={{ borderColor: … }}`` (non-transparent) in ``*.jsx`` /
     ``*.tsx``.
  4. Token-based STRUCTURAL border utilities in ``*.jsx`` / ``*.tsx`` —
     ``border[-trbl]-[var(--chrome-border…)]`` and ``…-[var(--color-border…)]``
     (incl. ``-strong`` / ``-warm``). These render a hairline the moment the
     ``--*-border`` token doesn't resolve transparent (a theme re-declare, or a
     bare ``border`` with no color under Tailwind v4's currentColor default), so
     the panel/aside/card/row frames were physically removed and converted to
     ``border-transparent``. This catches their reappearance.

It deliberately does NOT flag: ``:focus-visible`` / ring rules, the
``--color-ring`` / ``--focus-ring`` focus tokens, ``border-transparent`` /
``border-0``; the intentional ``--chrome-accent-border`` accent state cue and
severity token borders; the ``border-border`` / ``border-input`` design-system
aliases (also zeroed) and the arbitrary ``[border:…var(--…-border)…]`` property
form; the dashed drop-zone affordance; or borders kept in the functional
waveform / segment editor and shadcn form-control primitives (allowlisted).
"""
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "electron" / "src" / "renderer" / "src"
_GLOBAL_CSS = _SRC / "styles" / "globals.css"

# This enforcement file's own pattern literals must not trip the scan.
_SELF = Path(__file__).name

# ── 1) Neutral literal border colors in index.css ────────────────────────────
# `(?<![\w-])` keeps `--color-border` / `--chrome-border` custom-property
# *definitions* (which are token-zeroed by the trailing override block) from
# matching; only real `border[-x]:` declarations using a white/black rgba fill.
_CSS_NEUTRAL_BORDER = re.compile(
    r"(?<![\w-])border[a-z-]*\s*:[^;{}]*?"
    r"rgba\(\s*(?:255\s*,\s*255\s*,\s*255|0\s*,\s*0\s*,\s*0)\s*,",
)

# ── 2) Literal-color border utilities in JSX/TSX ─────────────────────────────
_JSX_BORDER_UTIL = re.compile(
    r"border(?:-[trblxy])?-(?:white|black)(?:/|\b)"
    r"|border(?:-[trblxy])?-\[(?:#|rgba?\(|hsl\()",
)

# ── 3) Inline borderColor (non-transparent) ──────────────────────────────────
_JSX_BORDERCOLOR = re.compile(r"borderColor\s*:")

# ── 4) Token-based structural border utilities in JSX/TSX ────────────────────
# `border[-trbl]-[var(--chrome-border…)]` / `…-[var(--color-border…)]` (any
# direction, incl. `-strong` / `-warm`, and any variant prefix). NOT matched:
# `--chrome-accent-border` (intentional accent state cue) — the char after
# `--chrome-`/`--color-` must be `border`.
_JSX_TOKEN_BORDER = re.compile(
    r"border(?:-[trblxy])?-\[var\(--(?:chrome|color)-border",
)

# Functional-affordance files whose remaining borders are intentional and must
# NOT be stripped: the waveform / segment editor (selection ring, drag handles,
# segment boundaries) and the shadcn form-control primitives (the border IS the
# control's own outline — removing it makes the field invisible).
_BORDER_ALLOW = {
    "components/AudioTrimmer.jsx",
    "components/WaveformPlayer.jsx",
    "components/SegmentTrack.jsx",
    "components/ui/input.tsx",
    "components/ui/textarea.tsx",
    "components/ui/select.tsx",
    "components/ui/slider.tsx",
    "components/ui/table.tsx",
}

# Media controls and palette swatches intentionally need contrast against
# image/video content rather than the surrounding theme.
_LITERAL_BORDER_ALLOW = {
    "components/video-player.tsx",
    "components/app-shell/workspace-header.tsx",
    "features/settings/palette-picker.tsx",
}


def _iter_frontend_files(suffixes):
    for p in _SRC.rglob("*"):
        if p.suffix in suffixes and p.name != _SELF:
            yield p


def test_no_neutral_literal_borders_in_renderer_css():
    offenders = []
    for path in _SRC.rglob("*.css"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _CSS_NEUTRAL_BORDER.search(line):
                offenders.append(f"{path.relative_to(_REPO)}:{i}: {line.strip()}")
    assert not offenders, (
        "Decorative neutral (white/black) literal borders reappeared in "
        "index.css. Use `border: … transparent` (or drop the border) — the "
        "app-wide border removal zeroed these.\n" + "\n".join(offenders)
    )


def test_no_literal_color_border_utilities_in_jsx():
    offenders = []
    for p in _iter_frontend_files({".jsx", ".tsx"}):
        if p.relative_to(_SRC).as_posix() in _LITERAL_BORDER_ALLOW:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in _JSX_BORDER_UTIL.finditer(line):
                token = m.group(0)
                if "border-transparent" in token:
                    continue
                offenders.append(f"{p.relative_to(_REPO)}:{i}: …{token}…")
    assert not offenders, (
        "Literal-color border utilities reappeared. Use `border-transparent` "
        "(keep the width to suppress the no-Preflight UA border) or a "
        "background tint for active/selected state.\n" + "\n".join(offenders)
    )


def test_no_inline_border_color_in_jsx():
    offenders = []
    for p in _iter_frontend_files({".jsx", ".tsx"}):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _JSX_BORDERCOLOR.search(line) and "transparent" not in line:
                offenders.append(f"{p.relative_to(_REPO)}:{i}: {line.strip()}")
    assert not offenders, (
        "Inline `borderColor` reappeared. Convey selection/error via a "
        "background tint (see WorkspaceHistory / DubSegmentRow) instead.\n"
        + "\n".join(offenders)
    )


def test_no_token_border_utilities_in_jsx():
    offenders = []
    for p in _iter_frontend_files({".jsx", ".tsx"}):
        if p.relative_to(_SRC).as_posix() in _BORDER_ALLOW:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _JSX_TOKEN_BORDER.search(line):
                offenders.append(f"{p.relative_to(_REPO)}:{i}: {line.strip()[:120]}")
    assert not offenders, (
        "Token-based structural border utilities reappeared. A "
        "`border-[var(--chrome-border…)]` / `border-[var(--color-border…)]` "
        "renders a stray frame the moment the token isn't transparent. Drop the "
        "border or use `border-transparent` (keep the width to suppress the "
        "no-Preflight UA border); convey active/selected state with a background "
        "tint (`--chrome-accent-bg` / `--chrome-hover-bg` / `--color-bg-elev-*`)."
        "\n" + "\n".join(offenders)
    )


def test_focus_indicators_are_preserved():
    """Regression guard: the border removal must not strip focus a11y."""
    css = _GLOBAL_CSS.read_text(encoding="utf-8")
    assert "--color-ring:" in css, "--color-ring focus token was removed"
    assert ":focus-visible" in css, ":focus-visible ring rules were removed"
    # The token-zeroing override must NOT have zeroed the focus ring.
    assert "--color-ring: transparent" not in css


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
