import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import NavRail from './NavRail';

describe('expandable workspace rail', () => {
  it('reveals labels without duplicate tooltips and collapses on selection', () => {
    const setMode = vi.fn();
    render(<NavRail mode="launchpad" setMode={setMode} />);
    const toggle = screen.getByRole('button', { name: 'Workspaces' });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const voice = screen.getByRole('button', { name: 'Voice', exact: true });
    expect(voice).not.toHaveAttribute('title');
    expect(voice).toHaveAttribute('data-expanded', 'true');
    expect(screen.getByRole('button', { name: 'Launchpad', exact: true })).toHaveAttribute(
      'aria-current',
      'page',
    );
    fireEvent.click(voice);
    expect(setMode).toHaveBeenCalledWith('studio');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('closes on Escape and restores toggle focus', () => {
    render(<NavRail mode="studio" setMode={vi.fn()} side="right" />);
    const toggle = screen.getByRole('button', { name: 'Workspaces' });
    fireEvent.click(toggle);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveFocus();
  });
});
