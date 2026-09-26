// SupportPage → Sponsors section. Covers the two states that matter:
//   1. EMPTY roster (the shipped default) → the outlined "be the first" slot
//      renders, and "Become a sponsor" opens the prefilled sponsor issue.
//   2. Populated roster → each sponsor renders as a lazy, aria-labelled logo
//      link that opens its site via the app's external-open (never window.open).
// SPONSORS is mocked behind a getter so one file can exercise both states;
// SPONSOR_TIERS / SPONSOR_CONTACT stay real so we assert the real contact URL.
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const { openExternal } = vi.hoisted(() => ({ openExternal: vi.fn() }));
vi.mock('../api/external', () => ({ openExternal }));

// Keep the goal-bar fetch offline; leave the rest of the module real.
vi.mock('../api/donation', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, loadDonationProgress: vi.fn().mockResolvedValue(actual.BUNDLED_PROGRESS) };
});

// Controllable roster: the getter lets each test swap SPONSORS in/out.
let mockSponsors = [];
vi.mock('../config/sponsors', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SPONSORS() {
      return mockSponsors;
    },
  };
});

import SupportPage from '../pages/SupportPage';
import { SPONSOR_CONTACT } from '../config/sponsors';

const renderSupport = () => render(<SupportPage initialView="support" onBack={vi.fn()} />);

beforeEach(() => {
  mockSponsors = [];
  openExternal.mockClear();
});

describe('SupportPage — Sponsors', () => {
  it('renders the outlined "be the first" placeholder when the roster is empty', async () => {
    renderSupport();
    expect(await screen.findByTestId('sponsors-empty')).toBeInTheDocument();
    expect(screen.getByText(/Be the first to sponsor VoiceStudio/i)).toBeInTheDocument();
    expect(screen.getByText(/Your logo here/i)).toBeInTheDocument();
  });

  it('"Become a sponsor" opens the prefilled sponsor issue URL', async () => {
    renderSupport();
    await screen.findByTestId('sponsors-empty');
    fireEvent.click(screen.getByRole('button', { name: /become a sponsor/i }));
    expect(openExternal).toHaveBeenCalledWith(SPONSOR_CONTACT.githubIssue);
    // The contact target really is the labelled, prefilled sponsor issue.
    expect(SPONSOR_CONTACT.githubIssue).toContain('/issues/new');
    expect(SPONSOR_CONTACT.githubIssue).toContain('template=sponsor.yml');
  });

  it('renders each sponsor as an external logo link when the roster is populated', async () => {
    mockSponsors = [
      {
        name: 'Acme Corp',
        logoUrl: 'https://acme.example/logo.svg',
        url: 'https://acme.example',
        tier: 'gold',
      },
      {
        name: 'Globex',
        logoUrl: 'https://globex.example/logo.png',
        url: 'https://globex.example',
        tier: 'silver',
      },
    ];
    renderSupport();

    // No placeholder once sponsors exist.
    expect(screen.queryByTestId('sponsors-empty')).toBeNull();

    const acme = await screen.findByLabelText('Visit Acme Corp, a VoiceStudio sponsor');
    const globex = screen.getByLabelText('Visit Globex, a VoiceStudio sponsor');

    // Real hrefs (accessibility / right-click) + rel="noreferrer" + lazy logos.
    expect(acme).toHaveAttribute('href', 'https://acme.example');
    expect(acme).toHaveAttribute('rel', 'noreferrer');
    const img = screen.getByAltText('Acme Corp');
    expect(img).toHaveAttribute('src', 'https://acme.example/logo.svg');
    expect(img).toHaveAttribute('loading', 'lazy');

    // Click routes through the app's external-open, not the webview.
    fireEvent.click(globex);
    expect(openExternal).toHaveBeenCalledWith('https://globex.example');
  });
});
