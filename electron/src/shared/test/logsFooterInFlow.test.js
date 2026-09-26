// Bottom-chrome in-flow guard — "buttons hidden under the footer on small
// windows" (owner report, 2026-07-02; same clipping class as #476/#504) and
// the #1032 stop pill's fixed-overlay overlap at 1440×900 (covered the
// studio's Production Overrides row).
//
// The footer AND the global audio mini-player must be real grid rows of
// .app-container (rows: auto 1fr auto auto), NOT fixed overlays compensated
// by padding-bottom on .main-content. A fixed overlay + padding reservation
// lets any nested page scroller that misses the padding (or any
// absolutely-positioned bottom bar) slide underneath / on top of content at
// small window sizes. As grid rows, row-2 content physically ends at the
// bars' top edge — overlap is impossible by construction.
//
// Static CSS-string assertions, same style as appShellScale.test.js (jsdom
// does no layout; the real 900x600 geometry check lives in
// e2e/footer-clipping.spec.ts).
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8');

const appContainerBlock = css.match(/\.app-container\s*\{[^}]*\}/s)?.[0] ?? '';
const footerInShellBlock = css.match(/\.app-container \.logs-footer\s*\{[^}]*\}/s)?.[0] ?? '';
const dockInShellBlock = css.match(/\.app-container \.global-audio-dock\s*\{[^}]*\}/s)?.[0] ?? '';

describe('LogsFooter is in the shell grid flow (not a fixed overlay)', () => {
  it('shell grid reserves rows for the audio dock and the footer', () => {
    expect(appContainerBlock).toMatch(/grid-template-rows:\s*auto\s+1fr\s+auto\s+auto/);
  });

  it('footer inside .app-container is a static grid item in row 4', () => {
    expect(footerInShellBlock).toMatch(/position:\s*static/);
    expect(footerInShellBlock).toMatch(/grid-row:\s*4/);
  });

  it('the padding-bottom footer reservation on .main-content is gone', () => {
    // The old mechanism this fix retires — its return would reintroduce the
    // overlay-clipping class.
    expect(css).not.toMatch(
      /\.app-container\s*>\s*\.main-content[^{]*\{[^}]*padding-bottom:\s*var\(--logs-footer-height/s,
    );
  });

  it('shell-mini gives the footer the full grid width (rail hidden)', () => {
    expect(css).toMatch(
      /\.app-container\.shell-mini \.logs-footer[^{]*\{[^}]*grid-column:\s*1\s*\/\s*-1/s,
    );
  });

  it('base .logs-footer stays fixed for splash/wizard (outside the shell)', () => {
    const base = css.match(/(^|\n)\.logs-footer\s*\{[^}]*\}/s)?.[0] ?? '';
    expect(base).toMatch(/position:\s*fixed/);
  });
});

describe('GlobalAudioPlayer dock is in the shell grid flow (not a fixed overlay)', () => {
  it('dock sits in grid row 3, directly above the footer', () => {
    expect(dockInShellBlock).toMatch(/grid-row:\s*3/);
    // Never a fixed overlay — that is the #1032 pill overlap class returning.
    expect(dockInShellBlock).not.toMatch(/position:\s*fixed/);
  });

  it('dock mirrors the footer column recipe (beside the rail; full width on shell-mini)', () => {
    expect(dockInShellBlock).toMatch(/grid-column:\s*2\s*\/\s*-1/);
    expect(css).toMatch(
      /\.app-container\.rail-right \.global-audio-dock[^{]*\{[^}]*grid-column:\s*1\s*\/\s*-2/s,
    );
    expect(css).toMatch(
      /\.app-container\.shell-mini \.global-audio-dock[^{]*\{[^}]*grid-column:\s*1\s*\/\s*-1/s,
    );
  });

  it('fixed overlays anchored above the footer also clear the dock', () => {
    // FloatingPill + compare drawer live in index.css; VoicePreview and
    // ExportModal carry the same calc inline (Tailwind arbitrary values).
    const anchors = css.match(/bottom:\s*calc\(var\(--logs-footer-height[^;]*/g) || [];
    expect(anchors.length).toBeGreaterThanOrEqual(2);
    for (const a of anchors) expect(a).toContain('--audio-dock-height');
  });
});
