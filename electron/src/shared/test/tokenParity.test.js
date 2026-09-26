import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Design-token drift guard (CSS → Tailwind v4 migration, "Solution A").
 *
 * The semantic color/radius/font tokens (`--color-*`, `--radius-*`,
 * `--font-*`) are owned by the Tailwind v4 `@theme` block in
 * `src/index.css` — that is their SINGLE source of truth. They used to be
 * re-declared in `src/ui/tokens.css`'s default `:root`, and because that
 * `:root` is unlayered while `@theme` lands in `@layer theme`, the
 * tokens.css copy silently WON. The two copies had already drifted (the
 * font stacks differed), so the `@theme` literals were dead, losing
 * duplicates.
 *
 * P5 consolidation: `ui/tokens.css` was folded into `src/index.css`, so its
 * old default `:root` is now the `:root` block in index.css that carries the
 * extra (non-@theme) token scale — identified here by `--color-muted-mono`.
 * The drift guard still holds against that block.
 *
 * After the de-dup, each of those tokens is declared exactly once. This test
 * fails if the drift ever returns: if a `--color-*` / `--radius-*` /
 * `--font-*` token is declared in BOTH the `@theme` block and the token
 * `:root` with DIFFERENT values (the silent-loser bug), or — more strictly —
 * if it is duplicated at all (re-introducing the second home).
 *
 * Note: `[data-theme=...]` theme overrides are intentionally NOT checked
 * here — those are unlayered theme overrides that are SUPPOSED to re-declare
 * `--color-*` to beat the `@theme` base.
 */

const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '');

/** Extract the body of the first `<opener> {` block via brace matching.
 *  `from` lets callers skip past earlier blocks (e.g. an earlier `:root`). */
function blockBody(css, opener, from = 0) {
  const start = css.indexOf(opener, from);
  if (start === -1) throw new Error(`could not find "${opener}" block`);
  let i = css.indexOf('{', start);
  let depth = 0;
  const bodyStart = i + 1;
  for (; i < css.length; i++) {
    if (css[i] === '{') depth++;
    else if (css[i] === '}') {
      depth--;
      if (depth === 0) return css.slice(bodyStart, i);
    }
  }
  throw new Error(`unbalanced braces after "${opener}"`);
}

/** Parse top-level `--name: value;` custom-property declarations. */
function parseTokens(body) {
  const tokens = {};
  const re = /(--[a-z0-9-]+)\s*:\s*([^;]+);/gi;
  let m;
  while ((m = re.exec(body))) {
    tokens[m[1]] = m[2].trim().replace(/\s+/g, ' ');
  }
  return tokens;
}

const TARGET = /^--(color|radius|font)-/;

const indexCss = stripComments(readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8'));

const theme = parseTokens(blockBody(indexCss, '@theme'));
// The former ui/tokens.css `:root` is now a `:root` block inside index.css.
// Locate it by its signature token so we test the right block regardless of
// how many other `:root` blocks the consolidated file contains.
const tokenRootMarker = indexCss.indexOf('--color-muted-mono');
const tokenRootStart = indexCss.lastIndexOf(':root', tokenRootMarker);
const root = parseTokens(blockBody(indexCss, ':root', tokenRootStart));

const themeTargets = Object.keys(theme).filter((k) => TARGET.test(k));
const rootTargets = Object.keys(root).filter((k) => TARGET.test(k));
const overlap = themeTargets.filter((k) => k in root);

describe('design-token parity: @theme vs token :root (consolidated in index.css)', () => {
  it('parses real tokens from both sources (sanity)', () => {
    // @theme owns the color/radius/font scale.
    expect(theme['--color-fg']).toBe('#ebdbb2');
    expect(theme['--radius-md']).toBe('4px');
    expect(theme['--font-sans']).toContain('Inter');
    // tokens.css still owns its non-overlapping tokens.
    expect(root['--color-muted-mono']).toBe('#d5c4a1');
    expect(rootTargets.length).toBeGreaterThan(0);
  });

  it('never declares a --color/--radius/--font token in BOTH with different values', () => {
    const drift = overlap
      .filter((k) => theme[k] !== root[k])
      .map((k) => `  ${k}: @theme=${theme[k]} | tokens.css=${root[k]}`);
    expect(drift, `design-token drift detected:\n${drift.join('\n')}`).toEqual([]);
  });

  it('declares each shared token exactly once (no duplicate home in tokens.css)', () => {
    expect(
      overlap,
      `these tokens are re-declared in ui/tokens.css; they belong only in @theme:\n  ${overlap.join('\n  ')}`,
    ).toEqual([]);
  });
});
