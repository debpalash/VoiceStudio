/**
 * Guard: nothing inside the titlebar-tabs shell may stay pinned to a grid
 * column the rail used to own.
 *
 * The rail-mode shell is `[rail][…rest]`, so several children are placed with
 * `grid-column: 2 / -1` (footer, audio dock) or `3` (main content) to sit
 * BESIDE the rail. Tabs mode deletes that first column — and a child left
 * pointing at a column the template no longer declares does not error or
 * clip: CSS Grid invents an IMPLICIT auto-sized column for it and takes that
 * width off the content column. That shipped as "the app renders in 65% of
 * the window with a black band down the right and the footer stranded in
 * it" (#1412) — a layout bug with no stack trace and no failing test.
 *
 * So: every `.app-container` rule that places a child anywhere but column 1
 * must have a `.nav-tabs`-scoped counterpart for the same target. Rail-only
 * selectors are exempt — `rail-right` cannot co-occur with tabs mode
 * (`utils/appShellClasses.js` drops it, pinned in NavStyleTitlebarTabs).
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// Comments are stripped first: they sit between rules, so a `[^{}]+` selector
// scan would otherwise swallow the preceding comment into the selector text.
const CSS = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
);

/** [{ selector, target, column }] for every `.app-container …` grid placement. */
function gridPlacements() {
  const out = [];
  // Rule-level scan: `selector { … }` with no nested blocks in this stylesheet's
  // .app-container rules.
  for (const m of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectorList = m[1].trim();
    const body = m[2];
    const col = body.match(/grid-column:\s*([^;]+);/);
    if (!col) continue;
    for (const selector of selectorList.split(',').map((s) => s.trim())) {
      if (!selector.includes('.app-container')) continue;
      // Target = what is being placed (the part after `.app-container…`).
      const target = selector.replace(/^.*\.app-container[^\s>]*\s*>?\s*/, '').trim();
      out.push({
        selector,
        target,
        column: col[1].replace(/!important/, '').trim(),
      });
    }
  }
  return out;
}

const PLACEMENTS = gridPlacements();

// Rail-geometry selectors: meaningless in tabs mode and provably unreachable
// there (no `.nav-rail` is rendered; `rail-right` is never emitted).
const RAIL_ONLY = (p) => p.selector.includes('rail-right') || p.target.includes('nav-rail');

describe('titlebar tabs — no child left in a phantom grid column', () => {
  it('finds the rail-mode placements it is meant to guard', () => {
    // Sanity: if the shell stops using explicit columns this test is vacuous,
    // and a vacuous guard is worse than none.
    expect(PLACEMENTS.length).toBeGreaterThan(4);
  });

  it('gives every non-column-1 placement a nav-tabs counterpart', () => {
    const tabsScoped = new Set(
      PLACEMENTS.filter((p) => p.selector.includes('.nav-tabs')).map((p) => p.target),
    );
    const orphans = PLACEMENTS.filter(
      (p) =>
        !p.selector.includes('.nav-tabs') &&
        !RAIL_ONLY(p) &&
        !/^1\b/.test(p.column) &&
        !tabsScoped.has(p.target),
    );
    expect(
      orphans.map((o) => `${o.selector} { grid-column: ${o.column} }`),
      'these are placed past column 1 with no .nav-tabs override — in tabs mode ' +
        'the grid will invent an implicit column for them',
    ).toEqual([]);
  });

  it('places the footer and audio dock at column 1 in tabs mode', () => {
    for (const target of ['.logs-footer', '.global-audio-dock']) {
      const rule = PLACEMENTS.find((p) => p.selector.includes('.nav-tabs') && p.target === target);
      expect(rule, `no .nav-tabs placement for ${target}`).toBeDefined();
      expect(rule.column).toBe('1 / -1');
    }
  });
});
