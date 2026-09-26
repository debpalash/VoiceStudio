import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import SettingsSidebar from './SettingsSidebar';

describe('SettingsSidebar — zero-match search empty state', () => {
  it('renders a "no results" message with the query and a Clear action instead of a blank nav', () => {
    const onClearSearch = vi.fn();
    render(
      <SettingsSidebar
        visibleIds={new Set()}
        active="appearance"
        onSelect={() => {}}
        query="zzz-no-such-setting"
        onClearSearch={onClearSearch}
      />,
    );
    const empty = screen.getByTestId('settings-search-empty');
    expect(empty.textContent).toContain('zzz-no-such-setting');
    // Neither the (empty) narrow <select> nor any rail item renders.
    expect(screen.queryByTestId('settings-nav-select')).toBeNull();
    expect(screen.queryByTestId('settings-nav-general')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(onClearSearch).toHaveBeenCalledTimes(1);
  });

  it('renders the full grouped nav (and the narrow select) when nothing is filtered', () => {
    render(<SettingsSidebar active="appearance" onSelect={() => {}} />);
    expect(screen.queryByTestId('settings-search-empty')).toBeNull();
    expect(screen.getByTestId('settings-nav-select')).toBeInTheDocument();
    expect(screen.getByTestId('settings-nav-appearance')).toBeInTheDocument();
    expect(screen.queryByTestId('settings-nav-general')).toBeNull();
    expect(screen.getByTestId('settings-nav-about')).toBeInTheDocument();
  });

  it('keeps the wide category rail independently scrollable', () => {
    render(<SettingsSidebar active="appearance" onSelect={() => {}} />);
    expect(screen.getByTestId('settings-nav-scroll')).toHaveClass('overflow-y-auto');
    expect(screen.getByTestId('settings-nav-scroll')).toHaveClass('overscroll-contain');
  });

  it('switches navigation from scaled shell width, never raw viewport width', () => {
    render(<SettingsSidebar active="appearance" onSelect={() => {}} />);
    const nav = screen.getByRole('navigation', { name: 'Settings' });
    expect(nav.className).toContain('@min-[760px]/settings-shell:flex');
    expect(nav.className).not.toMatch(/(?:^|\s)min-\[760px\]:/);

    const settingsPage = readFileSync(resolve(process.cwd(), 'src/pages/Settings.jsx'), 'utf8');
    expect(settingsPage).toContain('[container-name:settings-shell]');
    expect(settingsPage).toContain('@min-[760px]/settings-shell:grid');
    expect(settingsPage).not.toMatch(/(?:^|\s)min-\[760px\]:/);
  });

  it('renders only the matching categories when a filter set is provided', () => {
    render(
      <SettingsSidebar
        visibleIds={new Set(['network'])}
        active="network"
        onSelect={() => {}}
        query="proxy"
        onClearSearch={() => {}}
      />,
    );
    expect(screen.getByTestId('settings-nav-network')).toBeInTheDocument();
    expect(screen.queryByTestId('settings-nav-general')).toBeNull();
    expect(screen.queryByTestId('settings-search-empty')).toBeNull();
  });
});

describe('SettingsSidebar — keyboard navigation', () => {
  const nav = () => screen.getByTestId('settings-nav-scroll');

  it('is a single tab stop: only the active category is tabbable', () => {
    render(<SettingsSidebar active="appearance" onSelect={() => {}} />);
    expect(screen.getByTestId('settings-nav-appearance')).toHaveAttribute('tabindex', '0');
    expect(screen.getByTestId('settings-nav-dictation')).toHaveAttribute('tabindex', '-1');
  });

  it('ArrowDown/ArrowUp move selection, crossing group boundaries', () => {
    const onSelect = vi.fn();
    // 'appearance' is the last item of the General group; the next one down is
    // the first item of Voice & Engines — arrowing must not stop at the seam.
    render(<SettingsSidebar active="appearance" onSelect={onSelect} />);
    fireEvent.keyDown(nav(), { key: 'ArrowDown' });
    expect(onSelect).toHaveBeenCalledWith('dictation');

    onSelect.mockClear();
    render(<SettingsSidebar active="pronunciation" onSelect={onSelect} />);
    fireEvent.keyDown(screen.getAllByTestId('settings-nav-scroll')[1], { key: 'ArrowUp' });
    expect(onSelect).toHaveBeenCalledWith('dictation');
  });

  it('Home/End jump to the first and last visible category', () => {
    const onSelect = vi.fn();
    render(<SettingsSidebar active="network" onSelect={onSelect} />);
    fireEvent.keyDown(nav(), { key: 'End' });
    expect(onSelect).toHaveBeenCalledWith('about');

    onSelect.mockClear();
    fireEvent.keyDown(nav(), { key: 'Home' });
    expect(onSelect).toHaveBeenCalledWith('appearance');
  });

  it('respects the search filter when arrowing (hidden categories are skipped)', () => {
    const onSelect = vi.fn();
    render(
      <SettingsSidebar
        visibleIds={new Set(['appearance', 'about'])}
        active="appearance"
        onSelect={onSelect}
        query=""
      />,
    );
    fireEvent.keyDown(nav(), { key: 'ArrowDown' });
    expect(onSelect).toHaveBeenCalledWith('about');
  });

  it('ArrowUp past the first category hands focus back to the search box', () => {
    const onFocusSearch = vi.fn();
    const onSelect = vi.fn();
    render(
      <SettingsSidebar active="appearance" onSelect={onSelect} onFocusSearch={onFocusSearch} />,
    );
    fireEvent.keyDown(nav(), { key: 'ArrowUp' });
    expect(onFocusSearch).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('leaves modified arrow keys to the browser', () => {
    const onSelect = vi.fn();
    render(<SettingsSidebar active="appearance" onSelect={onSelect} />);
    fireEvent.keyDown(nav(), { key: 'ArrowDown', metaKey: true });
    expect(onSelect).not.toHaveBeenCalled();
  });
});

describe('SettingsSidebar — search-match highlighting', () => {
  it('accent-highlights the matched span of a category label', () => {
    const { container } = render(
      <SettingsSidebar
        visibleIds={new Set(['network'])}
        active="network"
        onSelect={() => {}}
        query="net"
      />,
    );
    const mark = container.querySelector('[data-testid="settings-nav-network"] mark');
    expect(mark).not.toBeNull();
    expect(mark.textContent).toBe('Net');
    expect(screen.getByTestId('settings-nav-network').textContent).toContain('Network');
  });

  it('renders the label plainly when the match came from a hidden keyword', () => {
    const { container } = render(
      <SettingsSidebar
        visibleIds={new Set(['network'])}
        active="network"
        onSelect={() => {}}
        query="proxy"
      />,
    );
    expect(container.querySelector('[data-testid="settings-nav-network"] mark')).toBeNull();
    expect(screen.getByTestId('settings-nav-network').textContent).toContain('Network');
  });
});
