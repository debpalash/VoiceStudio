import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Regression guard for the app-shell scale behavior (#21 black bands, #504
 * bottom-button clipping).
 *
 * Rule: the shell's layout box must be shrunk by `--ui-scale`, then magnified
 * back with `zoom`. On Chromium this gives a scaled UI that exactly fits the
 * viewport; on WebKitGTK `zoom` is a no-op so the UI renders smaller but never
 * leaves black bands.
 *
 * Forbidden pattern (caused black bands on WebKitGTK):
 *   `transform: scale(var(--ui-scale))` paired with the shrunk layout box,
 *   because WebKitGTK didn't magnify the shrunk shell.
 */
// vitest runs from the frontend/ package dir. Strip /* … */ comments so the
// guard checks real declarations, not the warning comment that quotes the
// patterns.
const raw = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');
const css = raw.replace(/\/\*[\s\S]*?\*\//g, '');

describe('app shell scale (black-band + clipping regression guard)', () => {
  it('does NOT scale the shell via transform: scale(--ui-scale)', () => {
    expect(css).not.toMatch(/transform:\s*scale\(\s*var\(--ui-scale/);
  });

  it('scales the shell via zoom', () => {
    const block = css.slice(css.indexOf('.app-container {'));
    expect(block).toMatch(/zoom:\s*var\(--ui-scale/);
  });

  it('shrinks the layout box by --ui-scale so zoomed content fits the viewport', () => {
    // width: calc(100vw / var(--ui-scale)) × zoom: var(--ui-scale) ⇒ rendered 100vw.
    const block = css.slice(css.indexOf('.app-container {'));
    expect(block).toMatch(/width:\s*calc\(\s*100vw\s*\/\s*var\(--ui-scale/);
    expect(block).toMatch(/height:\s*calc\(\s*100vh\s*\/\s*var\(--ui-scale/);
  });

  it('falls back to 100vw/100vh where zoom is a layout no-op (WebKitGTK, #523/#524)', () => {
    // The App.jsx zoom-layout probe sets data-zoom-layout=off on engines that
    // treat zoom as a layout no-op; this override must then fill the window at
    // 1.0 so the shell never leaves a black band or clips the bottom CTAs.
    expect(css).toMatch(
      /\[data-zoom-layout=['"]?off['"]?\][^{]*\.app-container\s*\{[^}]*width:\s*100vw[^}]*height:\s*100vh/,
    );
  });

  it('lets native Tauri zoom own scale while the shell still fills the viewport', () => {
    expect(css).toMatch(
      /\[data-ui-scale-engine=['"]?native['"]?\][^{]*\.app-container\s*\{[^}]*width:\s*100vw[^}]*height:\s*100vh[^}]*zoom:\s*1/,
    );
  });
});
