import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Theme-cascade regression guard (P5-consolidation regression).
 *
 * In the REAL app the active theme is applied with
 * `document.documentElement.setAttribute('data-theme', id)` (see src/App.jsx
 * + src/store/prefsSlice.ts). Because <html> IS `:root`
 * (documentElement === :root), a bare `:root {…}` and a `[data-theme="x"] {…}`
 * BOTH match that same element at EQUAL specificity (0,1,0). Nothing but
 * SOURCE ORDER breaks the tie — the block declared LATER in the stylesheet
 * wins.
 *
 * The `--chrome-*` (and `--color-*`) tokens are declared in BOTH the default
 * `:root` block AND every per-theme `[data-theme]` block. So if a default
 * `:root` that sets `--chrome-*` sits AFTER the `[data-theme]` blocks, it
 * clobbers every theme's chrome overrides and the app chrome (Settings hub,
 * header, footer — everything reading `var(--chrome-bg)`) stops recoloring.
 * That is exactly what the P5 tokens-consolidation did when it inlined the
 * legacy/chrome `:root` after the `[data-theme]` blocks.
 *
 * This test SIMULATES that documentElement cascade from index.css source
 * order and fails if a theme's chrome no longer wins over the default `:root`
 * — i.e. it fails-before / passes-after the reorder fix. NOTE: the visual
 * harness sets `data-theme` on a WRAPPER div (a closer ancestor that wins by
 * proximity, not source order), which is why the visual snapshots passed
 * despite the real-app bug — so a source-order check is the right guard here.
 */

const CSS = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');

const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '');

/** Split CSS into TOP-LEVEL `<selector> { <body> }` blocks, in source order.
 *  At-statements (`@import …;`) reset the selector buffer; nested braces
 *  (e.g. @keyframes / @media / @theme) are consumed whole via depth matching,
 *  so their inner selectors are never surfaced as top-level blocks. */
function topLevelBlocks(css) {
  const blocks = [];
  let sel = '';
  for (let i = 0; i < css.length; ) {
    const c = css[i];
    if (c === '{') {
      let depth = 1;
      let j = i + 1;
      for (; j < css.length && depth > 0; j++) {
        if (css[j] === '{') depth++;
        else if (css[j] === '}') depth--;
      }
      blocks.push({ selector: sel.trim().replace(/\s+/g, ' '), body: css.slice(i + 1, j - 1) });
      i = j;
      sel = '';
    } else if (c === ';') {
      sel = '';
      i++;
    } else {
      sel += c;
      i++;
    }
  }
  return blocks;
}

/** Parse `--name: value;` custom-property declarations from a block body. */
function parseTokens(body) {
  const map = {};
  const re = /(--[a-z0-9-]+)\s*:\s*([^;]+);/gi;
  let m;
  while ((m = re.exec(body))) map[m[1]] = m[2].trim().replace(/\s+/g, ' ');
  return map;
}

const BLOCKS = topLevelBlocks(stripComments(CSS));

/** Resolve a custom property on documentElement (=== :root) for a given theme
 *  by replaying the real cascade: among all UNLAYERED blocks that match the
 *  element (`:root`, plus `[data-theme="<theme>"]` when a theme is active),
 *  the LAST declaration in source order wins. */
function resolveOnRoot(token, theme = null) {
  const matches = (selector) =>
    selector === ':root' || (theme != null && selector === `[data-theme="${theme}"]`);
  let value;
  for (const b of BLOCKS) {
    if (!matches(b.selector)) continue;
    const t = parseTokens(b.body);
    if (token in t) value = t[token];
  }
  return value;
}

describe('theme cascade on documentElement (:root) — per-theme chrome recoloring', () => {
  it('sanity: default (no data-theme) chrome resolves to the Gruvbox defaults', () => {
    expect(resolveOnRoot('--chrome-bg')).toBe('#0f1011');
    expect(resolveOnRoot('--chrome-fg')).toBe('#d5c4a1');
  });

  // Expected per-theme chrome backgrounds, straight from the [data-theme] blocks.
  const THEME_CHROME_BG = {
    midnight: '#1e293b',
    nord: '#3b4252',
    solarized: '#073642',
    'rose-pine': '#1f1d2e',
    catppuccin: '#313244',
  };

  it('each [data-theme] wins over the default :root for --chrome-bg on <html>', () => {
    const defaultBg = resolveOnRoot('--chrome-bg');
    for (const [theme, expected] of Object.entries(THEME_CHROME_BG)) {
      const resolved = resolveOnRoot('--chrome-bg', theme);
      // The theme block must actually take effect on :root...
      expect(resolved, `theme "${theme}" --chrome-bg should resolve to its own value`).toBe(
        expected,
      );
      // ...which means it must DIFFER from the default (the regression: a
      // default :root placed after the themes made these equal again).
      expect(
        resolved,
        `theme "${theme}" --chrome-bg was clobbered back to the default (${defaultBg}) — a default :root is sitting AFTER the [data-theme] blocks`,
      ).not.toBe(defaultBg);
    }
  });

  it('catppuccin overrides both --chrome-bg and --chrome-fg on <html> (fail-before/pass-after)', () => {
    expect(resolveOnRoot('--chrome-bg', 'catppuccin')).not.toBe(resolveOnRoot('--chrome-bg'));
    expect(resolveOnRoot('--chrome-fg', 'catppuccin')).not.toBe(resolveOnRoot('--chrome-fg'));
    // Exact values, for good measure.
    expect(resolveOnRoot('--chrome-bg', 'catppuccin')).toBe('#313244');
    expect(resolveOnRoot('--chrome-fg', 'catppuccin')).toBe('#cdd6f4');
  });
});
