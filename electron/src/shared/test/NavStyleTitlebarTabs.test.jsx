/**
 * Navigation style — icon rail vs titlebar tabs (Settings → Appearance).
 *
 * The two skins share one workspace list (`components/navItems.js`) and are
 * mutually exclusive: whichever renders, the OTHER must not, and the shell
 * grid must reserve a column only for what's actually there. The bug class
 * being pinned:
 *
 *  • a workspace added to the rail but missing from the tabs (or vice versa)
 *    — silently unreachable in one skin;
 *  • `rail-right` surviving into tabs mode — the grid keeps a 48px column for
 *    a rail that isn't rendered, which shows up as a dead black gutter, not
 *    as an error;
 *  • the tab strip losing its connection to the active workspace (no
 *    `is-active`), which is the entire visual affordance of the skin.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import TitleTabs from '../components/TitleTabs';
import NavRail from '../components/NavRail';
import { NAV_ITEMS, NAV_FOOTER_ITEMS } from '../components/navItems';
import { appShellClasses } from '../utils/appShellClasses';
import i18n from '../i18n';

const ALL = [...NAV_ITEMS, ...NAV_FOOTER_ITEMS];

describe('TitleTabs — the titlebar tab strip', () => {
  it('renders a tab for every workspace the rail offers', () => {
    render(<TitleTabs mode="launchpad" setMode={() => {}} />);
    for (const item of ALL) {
      const tab = screen.getByTestId(`titletab-${item.id}`);
      expect(tab).toHaveTextContent(i18n.t(`nav.${item.tKey}`));
    }
  });

  it('marks exactly the current workspace as active and current', () => {
    render(<TitleTabs mode="dub" setMode={() => {}} />);
    const active = ALL.filter((i) =>
      screen.getByTestId(`titletab-${i.id}`).classList.contains('is-active'),
    );
    expect(active.map((i) => i.id)).toEqual(['dub']);
    expect(screen.getByTestId('titletab-dub')).toHaveAttribute('aria-current', 'page');
    expect(screen.getByTestId('titletab-studio')).not.toHaveAttribute('aria-current');
  });

  it('carries the workspace accent so the active tab can wear it', () => {
    render(<TitleTabs mode="dub" setMode={() => {}} />);
    expect(screen.getByTestId('titletab-dub').style.getPropertyValue('--tab-accent')).toBe(
      '#fe8019',
    );
  });

  it('switches workspace on click', () => {
    const setMode = vi.fn();
    render(<TitleTabs mode="launchpad" setMode={setMode} />);
    fireEvent.click(screen.getByTestId('titletab-gallery'));
    expect(setMode).toHaveBeenCalledWith('gallery');
  });

  it('never draws a separator against the raised (active) tab', () => {
    // Chrome's hairlines exist to divide RESTING tabs; one touching the active
    // tab cuts across the curl that connects it to the page below.
    render(<TitleTabs mode="dub" setMode={() => {}} />);
    const dubIndex = NAV_ITEMS.findIndex((i) => i.id === 'dub');
    const before = NAV_ITEMS[dubIndex - 1];
    expect(screen.getByTestId(`titletab-${before.id}`).className).not.toMatch(/has-separator/);
    expect(screen.getByTestId('titletab-dub').className).not.toMatch(/has-separator/);
    // …but resting neighbours still get one.
    expect(screen.getByTestId(`titletab-${NAV_ITEMS[0].id}`).className).toMatch(/has-separator/);
  });
});

describe('NavRail — same list, other skin', () => {
  it('offers every workspace the tab strip does', () => {
    render(<NavRail mode="launchpad" setMode={() => {}} side="left" onFlipSide={() => {}} />);
    for (const item of ALL) {
      expect(screen.getByRole('button', { name: i18n.t(`nav.${item.tKey}`) })).toBeInTheDocument();
    }
  });

  it('keeps the compact icon rail airy below the titlebar', () => {
    const { container } = render(
      <NavRail mode="launchpad" setMode={() => {}} side="left" onFlipSide={() => {}} />,
    );
    const rail = container.querySelector('.nav-rail');
    expect(rail.className).toContain('pt-[18px]');
    expect(rail.querySelector('.nav-rail-links').className).toContain('gap-[9px]');
  });
});

describe('appShellClasses — the grid only reserves what renders', () => {
  const base = { isSidebarCollapsed: false, hideSidebar: false, shellSizeClass: '' };

  it('keeps the rail column and its side in rail mode', () => {
    expect(appShellClasses({ ...base, navStyle: 'rail', navRailSide: 'right' })).toContain(
      'rail-right',
    );
    expect(appShellClasses({ ...base, navStyle: 'rail', navRailSide: 'left' })).not.toContain(
      'rail-right',
    );
  });

  it('drops rail-right in tabs mode even when the rail side is persisted right', () => {
    const cls = appShellClasses({ ...base, navStyle: 'tabs', navRailSide: 'right' });
    expect(cls).toContain('nav-tabs');
    expect(cls).not.toContain('rail-right');
  });

  it('never emits nav-tabs in rail mode', () => {
    expect(appShellClasses({ ...base, navStyle: 'rail', navRailSide: 'left' })).not.toContain(
      'nav-tabs',
    );
  });

  it('preserves the sidebar + shell-size classes in both skins', () => {
    for (const navStyle of ['rail', 'tabs']) {
      const cls = appShellClasses({
        navStyle,
        navRailSide: 'left',
        isSidebarCollapsed: true,
        hideSidebar: true,
        shellSizeClass: 'shell-narrow',
      });
      expect(cls).toContain('sidebar-collapsed');
      expect(cls).toContain('sidebar-hidden');
      expect(cls).toContain('shell-narrow');
    }
  });
});

describe('TitleTabs — label policy (all-or-nothing, measured)', () => {
  /**
   * jsdom has no layout, so the strip's own measurements are stubbed: the
   * point under test is the DECISION, not the browser's box maths.
   */
  function stubStripWidths({ needed, available }) {
    const isStrip = function () {
      return this.classList?.contains('tabstrip');
    };
    for (const [prop, value] of [
      ['scrollWidth', needed],
      ['clientWidth', available],
    ]) {
      Object.defineProperty(HTMLElement.prototype, prop, {
        configurable: true,
        get() {
          return isStrip.call(this) ? value : 0;
        },
      });
    }
  }

  afterEach(() => {
    for (const prop of ['scrollWidth', 'clientWidth']) {
      Object.defineProperty(HTMLElement.prototype, prop, { configurable: true, value: 0 });
    }
  });

  it('keeps every label when the full set fits', () => {
    stubStripWidths({ needed: 800, available: 1000 });
    const { container } = render(<TitleTabs mode="dub" setMode={() => {}} />);
    expect(container.querySelector('.tabstrip').className).not.toMatch(/is-compact/);
  });

  it('drops resting labels when it does not', () => {
    stubStripWidths({ needed: 1200, available: 1000 });
    const { container } = render(<TitleTabs mode="dub" setMode={() => {}} />);
    expect(container.querySelector('.tabstrip').className).toMatch(/is-compact/);
  });

  it('ignores a sub-pixel overflow rather than stripping the whole row', () => {
    stubStripWidths({ needed: 1001, available: 1000 });
    const { container } = render(<TitleTabs mode="dub" setMode={() => {}} />);
    expect(container.querySelector('.tabstrip').className).not.toMatch(/is-compact/);
  });

  it('expands again once the room comes back', () => {
    // The regression: `.is-compact` out-specifying `.is-measuring` made the
    // strip measure its own compact width, agree with itself, and never
    // recover — turning the metrics cluster off would leave icons forever.
    stubStripWidths({ needed: 1200, available: 1000 });
    const { container, rerender } = render(<TitleTabs mode="dub" setMode={() => {}} />);
    expect(container.querySelector('.tabstrip').className).toMatch(/is-compact/);

    stubStripWidths({ needed: 1200, available: 1400 });
    rerender(<TitleTabs mode="stories" setMode={() => {}} />);
    expect(container.querySelector('.tabstrip').className).not.toMatch(/is-compact/);
  });

  it('lets the measuring pass out-rank the compact rule in CSS', () => {
    // The stub above cannot see the cascade, and the cascade is where this bug
    // actually lived: without `!important` the measured labels stay hidden.
    const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');
    const rule = css.match(/\.tabstrip\.is-measuring \.tabstrip__label\s*\{([^}]*)\}/);
    expect(rule, 'no .tabstrip.is-measuring label rule').not.toBeNull();
    expect(rule[1]).toMatch(/display:\s*inline\s*!important/);
  });
});
