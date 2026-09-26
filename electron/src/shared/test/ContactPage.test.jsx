// Contact channels — render-level coverage for every guidance section
// (bug / feature / community / support / security), each channel pointing at
// the right URL, and the in-app bug-report affordance.
//
// Contact is a SECTION of SupportPage now, not a page of its own: sponsoring,
// commercial licensing and getting in touch answered one question between them
// and each used to be somewhere else. What is pinned here is the content and
// its links — the page shell (header, back button) belongs to the host and is
// covered by SupportPage's own suite. openExternal is mocked so no real
// browser navigation happens; the store is the real one.
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const { openExternal } = vi.hoisted(() => ({ openExternal: vi.fn() }));
vi.mock('../api/external', () => ({ openExternal }));

import { ContactSections } from '../pages/ContactPage';
import { useAppStore } from '../store';

const REPO = 'https://github.com/debpalash/VoiceStudio';

beforeEach(() => {
  openExternal.mockClear();
  useAppStore.getState().setMode?.('launchpad');
});

describe('contact sections', () => {
  it('renders every guidance section', () => {
    render(<ContactSections />);
    for (const name of [
      'Report a bug',
      'Request a feature or ask',
      'Get help & community',
      'Support the project',
      'Report a security issue',
    ]) {
      expect(screen.getByRole('heading', { name })).toBeInTheDocument();
    }
  });

  it('exposes the in-app bug-report affordance', () => {
    render(<ContactSections />);
    expect(screen.getByRole('button', { name: /open bug reporter/i })).toBeInTheDocument();
  });

  it('points each external channel at the right URL', () => {
    render(<ContactSections />);
    const href = (name) => screen.getByRole('link', { name }).getAttribute('href');
    expect(href('Open GitHub Issues')).toBe(`${REPO}/issues`);
    expect(href('Join the Discord')).toBe('https://discord.gg/bzQavDfVV9');
    expect(href('Follow on X')).toBe('https://x.com/idebpalash');
    expect(href('Report privately')).toBe(`${REPO}/security/advisories/new`);
    expect(href(/licensing/i)).toBe(`mailto:VoiceStudio@palash.dev`);
    expect(href(/more about the project/i)).toBe('https://palash.dev');
  });

  it('opens external links via the shared opener and marks them noreferrer', () => {
    render(<ContactSections />);
    const link = screen.getByRole('link', { name: 'Join the Discord' });
    expect(link).toHaveAttribute('rel', 'noreferrer');
    expect(link).toHaveAttribute('target', '_blank');
    fireEvent.click(link);
    expect(openExternal).toHaveBeenCalledWith('https://discord.gg/bzQavDfVV9');
  });

  it('sends "Support the project" to the support section, not out to Ko-fi', () => {
    // The support surface is on this same page now, so the CTA scrolls rather
    // than navigating — and must still never duplicate the Ko-fi/PayPal links.
    const target = document.createElement('div');
    target.id = 'support-give';
    target.scrollIntoView = vi.fn();
    document.body.appendChild(target);

    render(<ContactSections />);
    fireEvent.click(screen.getByRole('button', { name: 'See ways to support' }));

    expect(target.scrollIntoView).toHaveBeenCalled();
    expect(openExternal).not.toHaveBeenCalled();
    target.remove();
  });
});
