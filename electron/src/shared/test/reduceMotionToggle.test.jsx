/**
 * #1857 Part A — an in-app motion control.
 *
 * The CSS honours `prefers-reduced-motion` in about a dozen separate blocks,
 * but that is the OS switch and nothing else. Someone who wants a calm app
 * without turning motion off system-wide had no way to ask, and someone whose
 * OS setting is not respected by their environment had no recourse at all.
 *
 * Two properties matter and both are pinned here: the toggle drives a single
 * root attribute (so it cannot miss an animation the way a per-component list
 * can — the header status dot is exactly what that list missed, Part B), and
 * it is ADDITIVE, so turning it off never disables the OS media query.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { useAppStore } from '../store';

const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');

describe('reduce-motion toggle', () => {
  beforeEach(() => {
    useAppStore.getState().setReduceMotion(false);
  });

  it('sets and clears a single root attribute', () => {
    useAppStore.getState().setReduceMotion(true);
    expect(document.documentElement.getAttribute('data-motion')).toBe('reduce');
    expect(useAppStore.getState().reduceMotion).toBe(true);

    useAppStore.getState().setReduceMotion(false);
    expect(document.documentElement.getAttribute('data-motion')).toBeNull();
    expect(useAppStore.getState().reduceMotion).toBe(false);
  });

  it('is off by default', () => {
    expect(useAppStore.getState().reduceMotion).toBe(false);
  });

  it('has a blanket rule that covers descendants and pseudo-elements', () => {
    // A per-component list is what let the header dot keep pulsing. One rule
    // over the whole tree cannot have that gap.
    expect(css).toMatch(/\[data-motion='reduce'\] \*,/);
    expect(css).toMatch(/\[data-motion='reduce'\] \*::before,/);
    expect(css).toMatch(/\[data-motion='reduce'\] \*::after/);
  });

  it('does not fold the OS media query into the toggle', () => {
    // The two must stay independent: the media query has to keep working for
    // a user who never opens Settings.
    const block = css.slice(css.indexOf("[data-motion='reduce'],"));
    expect(block).not.toContain('prefers-reduced-motion');
    expect(css).toContain('prefers-reduced-motion');
  });

  it('uses a near-zero duration rather than none, so end events still fire', () => {
    // A zero duration skips animationend/transitionend, stranding anything
    // that waits on them.
    const block = css.slice(css.indexOf("[data-motion='reduce'],"));
    expect(block).toContain('animation-duration: 0.01ms !important');
    expect(block).not.toMatch(/animation:\s*none/);
  });
});
