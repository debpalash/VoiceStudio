import { describe, it, expect, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

import AppearancePanel from './AppearancePanel';
import { useAppStore, FONT_OPTIONS } from '../../store';

describe('AppearancePanel — global font selection', () => {
  beforeEach(() => {
    // Deterministic start: reset font to default and clear any DOM override.
    useAppStore.getState().setFont('default');
    document.documentElement.style.removeProperty('--font-sans');
  });

  it('renders the font grid with a tile for every FONT_OPTION', () => {
    render(<AppearancePanel />);
    const group = screen.getByRole('radiogroup', { name: 'Font' });
    expect(group).toBeInTheDocument();

    for (const opt of FONT_OPTIONS) {
      const tile = screen.getByTestId(`appearance-font-${opt.id}`);
      expect(tile).toBeInTheDocument();
    }
    // Defaults to the persisted 'default' font (its tile is checked).
    expect(screen.getByTestId('appearance-font-default')).toHaveAttribute('aria-checked', 'true');
  });

  it('selecting a non-default font updates the store and sets --font-sans', () => {
    render(<AppearancePanel />);

    fireEvent.click(screen.getByTestId('appearance-font-serif'));

    // Store reflects the selection.
    expect(useAppStore.getState().font).toBe('serif');
    // The serif tile shows as checked.
    expect(screen.getByTestId('appearance-font-serif')).toHaveAttribute('aria-checked', 'true');
    // The global font override is applied on the document root.
    expect(document.documentElement.style.getPropertyValue('--font-sans')).toMatch(/Georgia/);
  });

  it('switching back to default removes the --font-sans override', () => {
    render(<AppearancePanel />);

    fireEvent.click(screen.getByTestId('appearance-font-mono'));
    expect(document.documentElement.style.getPropertyValue('--font-sans')).not.toBe('');

    fireEvent.click(screen.getByTestId('appearance-font-default'));
    expect(useAppStore.getState().font).toBe('default');
    expect(document.documentElement.style.getPropertyValue('--font-sans')).toBe('');
  });
});

describe('AppearancePanel — WAI-ARIA radio-group keyboard pattern', () => {
  const fontIds = FONT_OPTIONS.map((f) => f.id);

  beforeEach(() => {
    useAppStore.getState().setFont(fontIds[0]);
    useAppStore.getState().setTheme('gruvbox');
    document.documentElement.style.removeProperty('--font-sans');
  });

  it('roving tabindex: only the checked font tile is tabbable', () => {
    render(<AppearancePanel />);
    expect(screen.getByTestId(`appearance-font-${fontIds[0]}`)).toHaveAttribute('tabindex', '0');
    for (const id of fontIds.slice(1)) {
      expect(screen.getByTestId(`appearance-font-${id}`)).toHaveAttribute('tabindex', '-1');
    }
  });

  it('ArrowRight moves font selection and focus to the next tile', () => {
    render(<AppearancePanel />);
    const first = screen.getByTestId(`appearance-font-${fontIds[0]}`);
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowRight' });

    expect(useAppStore.getState().font).toBe(fontIds[1]);
    const second = screen.getByTestId(`appearance-font-${fontIds[1]}`);
    expect(second).toHaveFocus();
    expect(second).toHaveAttribute('aria-checked', 'true');
    // Roving tabindex followed the selection.
    expect(second).toHaveAttribute('tabindex', '0');
    expect(first).toHaveAttribute('tabindex', '-1');
  });

  it('ArrowLeft wraps from the first font to the last', () => {
    render(<AppearancePanel />);
    const first = screen.getByTestId(`appearance-font-${fontIds[0]}`);
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowLeft' });
    expect(useAppStore.getState().font).toBe(fontIds[fontIds.length - 1]);
  });

  it('arrow keys move the theme-dot selection too', () => {
    render(<AppearancePanel />);
    const gruvbox = screen.getByRole('radio', { name: 'Gruvbox' });
    gruvbox.focus();
    fireEvent.keyDown(gruvbox, { key: 'ArrowDown' });
    expect(useAppStore.getState().theme).toBe('midnight');
    expect(screen.getByRole('radio', { name: 'Midnight' })).toHaveFocus();
  });
});

describe('AppearancePanel — auto-play preview toggle (#666)', () => {
  it('defaults to ON (preserves existing auto-play behavior)', () => {
    expect(useAppStore.getState().autoPlayPreview).toBe(true);
    render(<AppearancePanel />);
    expect(screen.getByRole('switch', { name: 'Auto-play preview' })).toBeChecked();
  });

  it('toggling off updates the store so renders no longer auto-play', () => {
    render(<AppearancePanel />);
    fireEvent.click(screen.getByRole('switch', { name: 'Auto-play preview' }));
    expect(useAppStore.getState().autoPlayPreview).toBe(false);
    expect(screen.getByRole('switch', { name: 'Auto-play preview' })).not.toBeChecked();
    // restore default for other tests
    useAppStore.getState().setAutoPlayPreview(true);
  });
});

describe('AppearancePanel — navigation style picker', () => {
  beforeEach(() => {
    useAppStore.getState().setNavStyle('rail');
  });

  it('defaults to the icon rail — titlebar tabs are opt-in', () => {
    render(<AppearancePanel />);
    expect(screen.getByTestId('appearance-nav-style-rail')).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByTestId('appearance-nav-style-tabs')).toHaveAttribute(
      'aria-checked',
      'false',
    );
  });

  it('picking titlebar tabs updates the store', () => {
    render(<AppearancePanel />);
    fireEvent.click(screen.getByTestId('appearance-nav-style-tabs'));
    expect(useAppStore.getState().navStyle).toBe('tabs');
    expect(screen.getByTestId('appearance-nav-style-tabs')).toHaveAttribute('aria-checked', 'true');
    useAppStore.getState().setNavStyle('rail');
  });

  it('follows the same arrow-key radio-group pattern as the other pickers', () => {
    render(<AppearancePanel />);
    const rail = screen.getByTestId('appearance-nav-style-rail');
    rail.focus();
    fireEvent.keyDown(rail, { key: 'ArrowRight' });
    expect(useAppStore.getState().navStyle).toBe('tabs');
    expect(screen.getByTestId('appearance-nav-style-tabs')).toHaveFocus();
    useAppStore.getState().setNavStyle('rail');
  });
});

describe('AppearancePanel — theme selection', () => {
  it('renders localized labels for the Auto and Light themes', () => {
    render(<AppearancePanel />);

    // The test runner uses the `defaultValue` from our t() hooks.
    // This verifies the labels are correctly exposed to screen readers via aria-label.
    const autoTheme = screen.getByRole('radio', { name: 'System Auto' });
    const lightTheme = screen.getByRole('radio', { name: 'Light' });

    expect(autoTheme).toBeInTheDocument();
    expect(lightTheme).toBeInTheDocument();

    // Verify it generates the correct data attribute for the OS-sync theme
    expect(autoTheme).toHaveAttribute('data-radio-value', 'auto');
    expect(lightTheme).toHaveAttribute('data-radio-value', 'light');
  });
});
