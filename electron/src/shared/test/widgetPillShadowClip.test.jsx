/**
 * The widget window's dictation pill must not have its drop shadow clipped
 * into a hard-edged rectangle.
 *
 * The `widget` window (tauri.conf.json, label `widget`) is exactly 300x64.
 * `html[data-window='widget'] body:has(.capture-pill)` (index.css ~3766)
 * gives that body a flush `padding: 8px` on every edge and `overflow:
 * hidden`. That leaves only an 8px gutter around the 48px-tall pill for
 * anything painted outside its box before the clip kicks in.
 *
 * `.capture-pill`'s shared (main-window) shadow — `0 8px 32px` + `0 2px 8px`
 * — needs roughly 40px of clearance. In the widget window it has 8px, so the
 * blur is cut off in a straight line at the window edge: a rounded capsule
 * sitting inside a hard-edged dark rectangle instead of floating free.
 *
 * The fix scopes a tighter shadow (and a matching max-width) to
 * `html[data-window='widget'] .capture-pill` that actually fits the gutter.
 * This test pins that budget directly against index.css so a future edit
 * that widens the shadow — or the window without widening the padding —
 * gets caught immediately instead of shipping another visible clip.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const css = fs.readFileSync(path.join(import.meta.dirname, '..', 'index.css'), 'utf8');

// Window content box: 300px window width - 8px padding * 2 sides.
const WINDOW_WIDTH = 300;
const GUTTER = 8; // body padding on every edge; also `overflow: hidden`'s clip boundary.

/** Extracts a `selector { ... }` rule's body (comments stripped), or null if not found. */
function extractRule(selector) {
  const start = css.indexOf(`${selector} {`);
  if (start === -1) return null;
  const braceStart = css.indexOf('{', start);
  const braceEnd = css.indexOf('}', braceStart);
  return css.slice(braceStart + 1, braceEnd).replace(/\/\*[\s\S]*?\*\//g, '');
}

/** Asserts every non-inset box-shadow layer in `rule` fits the 8px gutter. */
function expectShadowFitsGutter(rule) {
  const shadowMatch = rule.match(/box-shadow:\s*([^;]+);/);
  expect(shadowMatch).not.toBeNull();

  // Split shadow layers on top-level commas only (not the ones inside rgba(...)).
  const layers = shadowMatch[1]
    .split(/,(?![^(]*\))/)
    .map((s) => s.trim())
    .filter(Boolean);
  const outerLayers = layers.filter((l) => !l.startsWith('inset'));
  expect(outerLayers.length).toBeGreaterThan(0);

  for (const layer of outerLayers) {
    const lengths = layer
      .replace(/rgba?\([^)]*\)/, '')
      .trim()
      .split(/\s+/)
      .map(parseFloat);
    const [, y, blur, spread = 0] = lengths;
    // Offset-x is assumed 0 (true for every layer here); blur/spread extend
    // symmetrically in every direction from the offset box, so this same
    // budget bounds top/left/right too whenever offset-y >= 0.
    const outsideExtent = Math.abs(y) + blur + spread;
    expect(outsideExtent).toBeLessThanOrEqual(GUTTER);
  }
}

describe('index.css: widget-scoped .capture-pill shadow fits the 8px gutter', () => {
  it("defines an override under html[data-window='widget'] whose outer shadow layers and max-width both fit inside the padding gutter", () => {
    // No such rule exists pre-fix: the widget window inherits the unscoped
    // .capture-pill shadow verbatim, which is what gets clipped.
    const rule = extractRule("html[data-window='widget'] .capture-pill");
    expect(rule).not.toBeNull();

    expectShadowFitsGutter(rule);

    const maxWidthMatch = rule.match(/max-width:\s*(\d+(?:\.\d+)?)px/);
    expect(maxWidthMatch).not.toBeNull();
    // Equality, not `<=`: the pill must fill the content box exactly. A
    // smaller cap would also "fit the gutter" while silently narrowing the
    // pill and clipping more of the label — the very truncation #1884 is
    // about. Pin the required width so a future tightening can't pass here.
    expect(parseFloat(maxWidthMatch[1])).toBe(WINDOW_WIDTH - GUTTER * 2);
  });

  // .capture-pill--recording / --transcribing fully override box-shadow
  // (not just add to it) and win the cascade over the base .capture-pill
  // rule while active, so the base override alone isn't enough — the clip
  // would reappear the instant the user starts recording, which is the
  // pill's single most common state.
  it.each(['recording', 'transcribing'])(
    "also fits the %s state's own colored-glow shadow inside the gutter",
    (state) => {
      const rule = extractRule(`html[data-window='widget'] .capture-pill--${state}`);
      expect(rule).not.toBeNull();
      expectShadowFitsGutter(rule);
    },
  );

  it('leaves the main-window .capture-pill-host shadow untouched', () => {
    // The main window has real clearance for the shadow; only the widget
    // window's tight 8px gutter forces the smaller override above.
    expect(css).toContain('0 8px 32px rgba(0, 0, 0, 0.4)');
    expect(css).toContain('0 2px 8px rgba(0, 0, 0, 0.2)');
  });
});
