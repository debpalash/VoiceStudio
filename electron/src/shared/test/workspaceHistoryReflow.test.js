import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Regression guard for the design-mode "Synthesize Audio" CTA clipping (#476).
 *
 * The studio workspace stacks vertically on narrow shells. That reflow MUST be
 * driven by the shell-width classes (`.shell-narrow` / `.shell-mini`, set in
 * App.jsx from `app-container.clientWidth`), NOT a raw `@media (max-width)`:
 * the shell scales via `zoom`, so a viewport media query fires at the wrong
 * threshold whenever `--ui-scale ≠ 1` and dropped the action bar below the fold
 * (the same anti-pattern documented in index.css:294). This test fails CI if a
 * future change reintroduces a `@media (max-width)` in this file or drops the
 * shell-class reflow / sticky action bar.
 */
// The former per-component WorkspaceHistory.css was consolidated into
// src/index.css (final CSS consolidation). Its rules are relocated verbatim
// under a stable provenance header, so this guard slices that exact block out
// of index.css by its markers — same rules, same guarantee, one stylesheet.
const START = '═══ from src/components/WorkspaceHistory.css ═══';
const END = '═══ from src/components/WorkspaceVoices.css ═══';
const indexRaw = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');
const startIdx = indexRaw.indexOf(START);
const endIdx = indexRaw.indexOf(END);
if (startIdx === -1 || endIdx === -1 || endIdx <= startIdx) {
  throw new Error('WorkspaceHistory provenance block not found in src/index.css');
}
const raw = indexRaw.slice(startIdx, endIdx);
// Strip /* … */ comments so the guard checks real declarations, not the
// warning comment that quotes the forbidden `@media (max-width)` pattern.
const css = raw.replace(/\/\*[\s\S]*?\*\//g, '');
const app = readFileSync(resolve(process.cwd(), 'src/App.jsx'), 'utf8');

describe('workspace narrow-shell reflow (#476 CTA-clipping guard)', () => {
  it('does NOT use a raw viewport @media (max-width) query', () => {
    expect(css).not.toMatch(/@media[^{]*max-width/);
  });

  it('stacks the workspace via the shell-width classes', () => {
    expect(css).toMatch(/\.shell-narrow\s+\.studio-with-history/);
    expect(css).toMatch(/\.shell-mini\s+\.studio-with-history/);
  });

  it('pins the action bar (Synthesize CTA) sticky so it stays on-screen', () => {
    expect(css).toMatch(/\.studio-action-bar\s*\{[^}]*position:\s*sticky/s);
  });

  it('fills narrow workspaces and bounds expanded settings above the pinned action', () => {
    for (const shell of ['shell-mini', 'shell-narrow']) {
      const selector = `.${shell} .studio-with-history__main`;
      const rule = css.split('}').find((block) => block.split('{')[0].includes(selector));
      expect(rule).toMatch(/flex:\s*1 0 100%;/);
      expect(rule).toMatch(/min-height:\s*0;/);
    }
    expect(indexRaw).toMatch(
      /\.studio-action-bar\s+\.override-content\s*\{[^}]*max-height:\s*40vh;[^}]*overflow-y:\s*auto/s,
    );
    expect(indexRaw).toMatch(/\.studio-action-bar\s*\{[^}]*max-height:\s*60%;/s);
    expect(indexRaw).toMatch(/\.studio-action-bar\s+\.override-content\s*\{[^}]*min-height:\s*0;/s);
  });

  it('keeps saved voices in the left rail and generation history alone on the right', () => {
    expect(app).toMatch(
      /className="studio-voices">\s*<WorkspaceVoices[\s\S]*?<\/div>\s*<div className="studio-with-history__main">/,
    );
    expect(app).toMatch(/<div className="studio-right">\s*<WorkspaceHistory\s+history=\{history\}/);
  });

  it('keeps the Script editor useful without pushing voice setup below the fold', () => {
    expect(indexRaw).toMatch(/\.studio-script-input\s*\{[^}]*min-height:\s*168px/s);
    expect(indexRaw).toMatch(/\.shell-narrow\s+\.studio-script-input[^}]*min-height:\s*144px/s);
    expect(indexRaw).toMatch(/\.shell-mini\s+\.studio-script-input[^}]*min-height:\s*120px/s);
  });
});
