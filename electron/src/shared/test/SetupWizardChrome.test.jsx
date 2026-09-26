/**
 * First-run wizard chrome: the pinned action row must stay on screen, and the
 * studio's status bar must not appear before the user reaches the studio.
 *
 * The bug: `SetupWizard`'s root was `fixed inset-0`, so it laid itself out
 * against the VIEWPORT rather than `.app-wizard-wrap` — the box App.jsx sizes
 * to stop above the fixed `LogsFooter`. Its pinned footer (Continue, Back, and
 * the "set a Hugging Face token" card) therefore rendered underneath the status
 * bar, clipped off the bottom of the window: on the Models & engines step the
 * Continue button was simply unreachable.
 *
 * Both halves are pinned here — the root must not be `fixed`, and the wizard
 * branch must render no `LogsFooter` — because either one alone reintroduces
 * the clip.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import fs from 'node:fs';
import path from 'node:path';
import i18n from '../i18n';

vi.mock('../components/WizardLibrary', () => ({ default: () => null }));
vi.mock('../components/MediaEngineCard', () => ({ default: () => null }));
vi.mock('../components/MirrorRescue', () => ({ default: () => null }));
vi.mock('../components/DictationDemo', () => ({ default: () => null }));
vi.mock('../components/HfTokenCard', () => ({
  default: ({ className }) => <div data-testid="hf-token-card" className={className} />,
}));
vi.mock('../api/external', () => ({ openExternal: vi.fn(() => Promise.resolve()) }));

vi.mock('../api/hooks', () => ({
  useSetupStatus: () => ({
    data: { models_ready: true, missing: [], hf_cache_dir: '/tmp/hf' },
    refetch: vi.fn(),
  }),
  usePreflight: () => ({
    data: { ok: true, has_warnings: false, checks: [] },
    isLoading: false,
    refetch: vi.fn(),
  }),
}));

vi.mock('../api/client', () => ({
  apiJson: vi.fn(() => Promise.resolve({ available: false, prompted: true })),
  apiFetch: vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) })),
  API: '',
}));

import SetupWizard from '../pages/SetupWizard';

const withI18n = (node) => <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;

const readSrc = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8');

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('SetupWizard — the pinned action row stays on screen', () => {
  it('does not position its root against the viewport', () => {
    const { container } = render(withI18n(<SetupWizard onReady={() => {}} />));
    const root = container.firstElementChild;

    // `fixed` ignores .app-wizard-wrap's box; `absolute` fills it.
    expect(root.className).not.toMatch(/\bfixed\b/);
    expect(root.className).toMatch(/\babsolute\b/);
    // ...and the pinned row needs clearance now that it really is the last
    // thing on screen — flush against the window edge is the same bug's
    // cosmetic tail.
    expect(root.className).toMatch(/\bpb-\d/);
  });

  it('every flex ancestor of the model-list scroller can actually shrink', async () => {
    // The bug that survived TWO earlier fixes (and shipped in 0.4.2): the
    // max-w-[1100px] wrapper was `flex-1 flex-col` WITHOUT min-h-0. Per the
    // flex spec, min-height:auto on a column-flex item resolves to its
    // min-content height — the full model list — so the wrapper grew past
    // the root, overflow-hidden clipped everything below the window, and the
    // inner overflow-y-auto clamp never engaged: Continue and the HF-token
    // card were simply unreachable. jsdom does no layout, so this pins the
    // CLASS RULE the layout depends on: every growable flex ancestor between
    // the scroller and the wizard root must also be allowed to shrink.
    // Measured for real in Chromium: footer at y=3078 in a 900px window
    // without min-h-0 on the wrapper; y=884 with it.
    const { container } = render(withI18n(<SetupWizard onReady={() => {}} />));
    fireEvent.click(await screen.findByText(/All good — continue/i));

    const scroller = await waitFor(() => {
      const el = [...container.querySelectorAll('[class*="overflow-y-auto"]')].pop();
      expect(el).toBeTruthy();
      return el;
    });
    const root = container.firstElementChild;
    let el = scroller.parentElement;
    let checked = 0;
    while (el && el !== root) {
      const cls = el.className || '';
      if (/\bflex-(1|auto)\b/.test(cls)) {
        checked += 1;
        expect(
          cls,
          `growable ancestor lacks min-h-0: <${el.tagName.toLowerCase()} class="${cls}">`,
        ).toMatch(/\bmin-h-0\b/);
      }
      el = el.parentElement;
    }
    // The walk must terminate AT the root, not fall off the document — and it
    // must actually have inspected the growable chain it exists for, or a
    // refactor that reparents the scroller silently turns this test into a
    // no-op (CodeRabbit).
    expect(el).toBe(root);
    expect(checked).toBeGreaterThanOrEqual(2);
  });

  it('keeps Continue and the HF-token card OUT of the scrolling region', async () => {
    render(withI18n(<SetupWizard onReady={() => {}} />));
    fireEvent.click(await screen.findByText(/All good — continue/i));

    // Models step: the token card and the action row are siblings of the
    // scroller, not children of it — otherwise they scroll away instead of
    // staying pinned.
    const card = await screen.findByTestId('hf-token-card');
    expect(card.className).toMatch(/shrink-0/);

    const cta = await screen.findByText(/Required models ready/i);
    const row = cta.closest('div');
    expect(row.className).toMatch(/shrink-0/);

    let el = card;
    while (el) {
      expect(el.className || '').not.toMatch(/overflow-y-auto/);
      el = el.parentElement;
    }
  });
});

describe('studio chrome does not appear before the studio', () => {
  it('renders no LogsFooter in the first-run wizard or the pre-wizard splash', () => {
    const app = readSrc('App.jsx');

    // Split App.jsx at the last pre-studio early return. Everything above is a
    // screen the user sees BEFORE the studio (awaiting_setup splash,
    // !setupChecked splash, the wizard); everything below is the studio.
    const split = app.indexOf('// Block the main UI until Rust reports the backend is ready');
    expect(split).toBeGreaterThan(0);
    const preStudio = app.slice(app.indexOf("if (bootstrapStage === 'awaiting_setup')"), split);
    const studio = app.slice(split);

    // Asserted per-branch rather than as a global count: a bare count of 1
    // would still pass if the mount MOVED from the studio into the splash.
    expect(preStudio).not.toContain('LogsFooter');
    expect(studio.match(/<LogsFooter\s*\/>/g) || []).toHaveLength(1);
  });

  it('gives the wizard the whole viewport, since nothing is reserved below it', () => {
    const css = readSrc('index.css');
    const rule = css.slice(css.indexOf('.app-wizard-wrap {'));
    const body = rule.slice(0, rule.indexOf('}'));
    // A leftover `bottom: var(--logs-footer-height)` would strand a dead 28px
    // gap under the wizard now that no footer is rendered there.
    expect(body).not.toContain('--logs-footer-height');
    // The full-viewport promise is now expressed as the #504 scale contract
    // (viewport ÷ scale, zoomed back) rather than `bottom: 0` — asserted in
    // detail below.
    expect(body).toMatch(/height:\s*calc\(100vh/);
  });

  // The UI-scale regression that shipped in 0.4.2: the wrap was `inset: 0`
  // (full-viewport box) with a bare inline `zoom` on the mount, so at any
  // UI scale > 1 the zoomed content overran the window and the pinned
  // Continue button + HF-token card were pushed below the visible edge —
  // unreachable, since overflow: hidden means no scrollbar. At scale 1
  // nothing showed, which is how it survived. The fix is the exact
  // .app-container contract (#504): shrink the box by the scale, zoom back.
  describe('UI scale cannot push the pinned row off screen', () => {
    it('the wrap is shrunk by --ui-scale and zoomed back, like .app-container', () => {
      const css = readSrc('index.css');
      const rule = css.slice(css.indexOf('.app-wizard-wrap {'));
      const body = rule.slice(0, rule.indexOf('}'));
      expect(body).toMatch(/width:\s*calc\(100vw\s*\/\s*var\(--ui-scale, 1\)\)/);
      expect(body).toMatch(/height:\s*calc\(100vh\s*\/\s*var\(--ui-scale, 1\)\)/);
      expect(body).toMatch(/zoom:\s*var\(--ui-scale, 1\)/);
      // `inset`-style pinning of the bottom edge is the broken shape: a
      // full-viewport box that zoom then magnifies past the window.
      expect(body).not.toMatch(/bottom:\s*0/);
      expect(body).not.toMatch(/right:\s*0/);
    });

    it('keeps the WebKitGTK zoom-no-op fallback, same as the studio shell', () => {
      // On engines where `zoom` does not lay out (older WebKitGTK), the
      // shrunk box cannot be magnified back — without this override the
      // wizard renders at 1/scale size with a black band around it.
      const css = readSrc('index.css');
      expect(css).toMatch(/html\[data-zoom-layout='off'\] \.app-wizard-wrap \{[^}]*zoom:\s*1/);
    });

    it('fills the viewport without CSS zoom when Tauri owns the scale', () => {
      const css = readSrc('index.css');
      expect(css).toMatch(
        /html\[data-ui-scale-engine='native'\] \.app-wizard-wrap \{[^}]*width:\s*100vw[^}]*height:\s*100vh[^}]*zoom:\s*1/,
      );
    });

    it('the mount passes --ui-scale and never a bare inline zoom', () => {
      const app = readSrc('App.jsx');
      const mount = app.slice(app.indexOf('className="app-wizard-wrap"'));
      const tag = mount.slice(0, mount.indexOf('>'));
      expect(tag).toContain("'--ui-scale': effectiveUiScale");
      // An inline `zoom:` on top of the CSS contract double-applies the
      // scale — the CSS zooms once, this would zoom again.
      expect(tag).not.toMatch(/zoom:\s*uiScale/);
    });
  });
});
