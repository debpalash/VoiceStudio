/**
 * The Launchpad must scroll, not compress (#1859).
 *
 * `.launchpad` is grid row 2 of `.app-container` and is itself a flex column
 * with `overflow-y: auto`. Opening the log panel grows grid row 4 (up to
 * 720px), which shrinks row 2 — the shell is built for that, and row 2 is
 * supposed to hand the shortfall to its own scrollbar.
 *
 * It did not, because a flex item shrinks before its container scrolls. The
 * three content blocks under `.launchpad` had the default `flex-shrink: 1`, so
 * they absorbed the shortfall: the hero is `overflow-hidden`, so it clipped its
 * own heading mid-line, and the deck moved up into the hero's artwork.
 *
 * jsdom does no layout, so this pins the class rule. Measured for real in
 * headless Chromium 153 at a 720px window, the same nesting the shell uses,
 * log panel at 560px:
 *
 *   variant                    hero box / natural   clipped   deck top vs h1 bottom
 *   footer collapsed (28px)          145 / 145        no             +59
 *   current                           64 / 145       YES             -22   ← over the h1
 *   + min-height:0 on .launchpad      64 / 145       YES             -22   ← no change
 *   + shrink-0 on the children       145 / 145        no             +59
 *
 * The third row is worth keeping: #1859 suspected the missing `min-height: 0`
 * on `.launchpad` (its two siblings `.app-container > .main-content` and
 * `.studio-panel` both set it) and flagged it as unconfirmed. It is inert here
 * — `overflow-y: auto` already zeroes a grid item's automatic minimum size, so
 * the track was shrinking correctly all along. The damage was one level in.
 */
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import fs from 'node:fs';
import path from 'node:path';
import i18n from '../i18n';
import Launchpad from '../pages/Launchpad';

// Needs a react-query provider + live endpoints, so it is stubbed — but with a
// marker node rather than null, so the test can still find the wrapper the page
// puts around it.
vi.mock('../components/ReadinessChecklist', () => ({
  default: () => <span data-readiness="stub" />,
}));

const baseProps = {
  profiles: [],
  studioProjects: [],
  dubHistory: [],
  exportHistory: [],
  setMode: vi.fn(),
  setIsCompareModalOpen: vi.fn(),
  handleSelectProfile: vi.fn(),
  loadProject: vi.fn(),
};

// Both branches of the page, because they render DIFFERENT direct children:
// empty gives the `flex-1` empty state, populated gives the readiness
// checklist. Checking only one leaves the other's child unconstrained — which
// is how the checklist was missed on the first pass, and it is the branch the
// #1859 reporter was actually in.
const STATES = {
  empty: baseProps,
  populated: {
    ...baseProps,
    profiles: [{ id: 'p1', name: 'Test voice' }],
    studioProjects: [{ id: 's1', name: 'Test project', updated_at: Date.now() }],
  },
};

const renderShell = (props) =>
  render(
    <I18nextProvider i18n={i18n}>
      <div className="app-container">
        <Launchpad {...props} />
      </div>
    </I18nextProvider>,
  );

describe('Launchpad content blocks defer the shortfall to the scroll container', () => {
  for (const [state, props] of Object.entries(STATES)) {
    it(`labels every direct child as content or filler (${state})`, () => {
      // The invariant, stated so a new block cannot be added unlabelled: a
      // child either protects a height (`shrink-0`) or is deliberately elastic
      // (`flex-1`). The empty state is the elastic one — it centres a small
      // waveform in whatever space is left and has no height to lose. Anything
      // that is neither is the #1859 shape.
      const { container } = renderShell(props);
      const root = container.querySelector('.launchpad');
      expect(root).toBeTruthy();

      let content = 0;
      for (const el of root.children) {
        const cls = el.className || '';
        if (/\bflex-1\b/.test(cls)) continue;
        expect(
          cls,
          `a .launchpad child that is neither shrink-0 nor flex-1 absorbs the log ` +
            `panel's shortfall instead of letting the page scroll (#1859): ` +
            `<${el.tagName.toLowerCase()} class="${cls}">`,
        ).toMatch(/\bshrink-0\b/);
        content += 1;
      }
      // A refactor that reparents the blocks would otherwise turn this into a
      // no-op that still passes.
      expect(content).toBeGreaterThanOrEqual(3);
    });
  }

  it('constrains the readiness checklist, which only the populated page mounts', () => {
    // ReadinessChecklist is mocked out here (it needs react-query and live
    // endpoints), so this asserts the WRAPPER the page owns rather than the
    // component's own markup — which is the reason the fix wraps it instead of
    // reaching into a shared component for a parent-specific layout fact.
    const { container } = renderShell(STATES.populated);
    const root = container.querySelector('.launchpad');
    const wrapper = [...root.children].find((el) => el.querySelector('[data-readiness]'));
    expect(
      wrapper,
      'the checklist branch did not render — fixture no longer populated',
    ).toBeTruthy();
    expect(wrapper.className).toMatch(/\bshrink-0\b/);
  });

  it('keeps the scroll container the shrink-0 defers to', () => {
    // shrink-0 without a scrolling ancestor would just move the clipping up to
    // .app-container's own `overflow: hidden`.
    const css = fs.readFileSync(path.resolve(__dirname, '..', 'index.css'), 'utf8');
    const rule = css.slice(css.indexOf('.launchpad {'));
    const body = rule.slice(0, rule.indexOf('}'));
    expect(body).toMatch(/overflow-y:\s*auto/);
    expect(body).toMatch(/flex-direction:\s*column/);
  });
});
