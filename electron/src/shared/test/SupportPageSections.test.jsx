// Support, commercial licensing and contact share one compact tabbed page.
// App.jsx renders SupportPage in the SAME tree position for all three routes,
// so a changed `initialView` must update the active tab after mount too.
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('../api/external', () => ({ openExternal: vi.fn() }));
// Partial mock: only the network call is stubbed. GoalBar imports the real
// progressPct / isGoalMet, and a hand-written module object would silently
// drop whichever helper it gains next.
vi.mock('../api/donation', async (importOriginal) => ({
  ...(await importOriginal()),
  loadDonationProgress: () => Promise.resolve({ raised: 0, goal: 100, sponsorCount: 0 }),
}));

import SupportPage from '../pages/SupportPage';

beforeEach(() => {
  vi.clearAllMocks();
});

// Radix Tabs activate on pointer down; drive them like a real pointer.
const clickTab = (name) => {
  const tab = screen.getByRole('tab', { name });
  fireEvent.pointerDown(tab, { button: 0, ctrlKey: false, pointerType: 'mouse' });
  fireEvent.mouseDown(tab, { button: 0 });
  fireEvent.click(tab);
};

describe('the compact support page', () => {
  it('shows one destination at a time behind three tabs', () => {
    render(<SupportPage onBack={() => {}} />);
    expect(screen.getAllByRole('tab')).toHaveLength(3);
    expect(screen.getByRole('tab', { name: 'Support' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('heading', { name: 'Support VoiceStudio' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Report a bug' })).toBeNull();
  });

  it('updates the active tab when the reused route changes', () => {
    const { rerender } = render(<SupportPage onBack={() => {}} initialView="support" />);
    rerender(<SupportPage onBack={() => {}} initialView="contact" />);
    expect(screen.getByRole('tab', { name: 'Contact' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('heading', { name: 'Report a bug' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Support VoiceStudio' })).toBeNull();
  });

  it('opens the licence tab for the enterprise route', () => {
    render(<SupportPage onBack={() => {}} initialView="license" />);
    expect(screen.getByRole('tab', { name: 'Commercial License' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(
      screen.getByRole('heading', { name: 'Ship AI voices in production' }),
    ).toBeInTheDocument();
  });

  it('switches destinations from the tab rail', () => {
    render(<SupportPage onBack={() => {}} />);
    clickTab('Contact');
    expect(screen.getByRole('heading', { name: 'Report a bug' })).toBeInTheDocument();
    clickTab('Support');
    expect(screen.getByRole('heading', { name: 'Support VoiceStudio' })).toBeInTheDocument();
  });
});
