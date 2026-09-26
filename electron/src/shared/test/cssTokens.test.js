/**
 * Guard: every `var(--token)` referenced from JSX must actually be defined.
 *
 * An undefined custom property fails *silently* and invisibly. `text-[var(--nope)]`
 * compiles to a declaration with an invalid value, the browser drops it, and the
 * element quietly inherits — so the styling you wrote simply doesn't happen and
 * nothing anywhere says so. This is exactly how the Storage panels shipped paths
 * that were meant to be dim and rendered at full body weight
 * (`--chrome-fg-subtle` — never defined; the real token is `--chrome-fg-dim`).
 *
 * Tokens legitimately injected at RUNTIME (by Radix, or by an inline `style` that
 * sets the property on an ancestor) can't be found in CSS and are allowlisted
 * below with the reason.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

// vitest runs with the frontend package as cwd.
const SRC = resolve(process.cwd(), 'src');

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules') continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

const FILES = walk(SRC);
const read = (p) => readFileSync(p, 'utf8');

/** Custom properties defined anywhere in our stylesheets. */
const DEFINED = new Set(
  FILES.filter((f) => f.endsWith('.css')).flatMap((f) =>
    [...read(f).matchAll(/(--[a-zA-Z0-9_-]+)\s*:/g)].map((m) => m[1]),
  ),
);

/**
 * Set at runtime, so they never appear in a stylesheet. Each needs a reason —
 * "it's failing the test" is not one. If you're tempted to add a `--chrome-*` or
 * `--color-*` token here, you almost certainly mistyped an existing one instead.
 */
const RUNTIME_INJECTED = new Map([
  ['--radix-select-trigger-height', 'Radix sets this on the select content element'],
  ['--radix-select-trigger-width', 'Radix sets this on the select content element'],
  ['--audio-dock-height', 'set inline by the audio dock as it mounts/resizes'],
  ['--logs-footer-height', 'set inline by LogsFooter as the user drags it'],
  ['--card-accent', 'per-card hue, set inline from the item being rendered'],
  ['--card-hue', 'per-card hue, set inline from the item being rendered'],
  ['--goal-accent', 'per-goal color, set inline by GoalBar'],
  ['--rail-accent', 'per-item color, set inline by NavRail'],
]);

/**
 * Only BARE `var(--token)` is checked. `var(--token, #1d1d22)` supplies a
 * fallback, so an undefined token still renders the fallback — that is valid CSS
 * and several components rely on it deliberately. It is the bare form that fails
 * silently, and only the bare form this guard forbids.
 */
const BARE_VAR = /var\(\s*(--[a-zA-Z0-9_-]+)\s*\)/g;

describe('CSS custom properties referenced from JSX', () => {
  it('are all actually defined somewhere (or explicitly runtime-injected)', () => {
    const offenders = [];
    for (const file of FILES.filter((f) => /\.(jsx|tsx)$/.test(f))) {
      for (const [, token] of read(file).matchAll(BARE_VAR)) {
        if (DEFINED.has(token) || RUNTIME_INJECTED.has(token)) continue;
        offenders.push(`${relative(SRC, file)} → var(${token})`);
      }
    }
    expect(
      [...new Set(offenders)],
      'Undefined CSS custom property with no fallback — the declaration is invalid\n' +
        'and silently does nothing (the element just inherits). Fix the token name,\n' +
        'give it a fallback, or add it to RUNTIME_INJECTED with the reason.',
    ).toEqual([]);
  });

  it('sanity: the scan actually found our design tokens', () => {
    // If a refactor moves the stylesheets, this guard must fail loudly rather
    // than pass vacuously by finding nothing to check.
    expect(DEFINED.size).toBeGreaterThan(50);
    expect(DEFINED.has('--chrome-fg-dim')).toBe(true);
  });

  it('catches the exact bug that motivated it', () => {
    // --chrome-fg-subtle was used in two Storage panels and defined nowhere, so
    // the folder paths meant to recede rendered at full body weight. There is no
    // grandfather list here: every bare var() in the app resolves today, and this
    // asserts the guard would still notice if one stopped.
    expect(DEFINED.has('--chrome-fg-subtle')).toBe(false);
    expect(DEFINED.has('--chrome-input-bg')).toBe(false);
  });
});
