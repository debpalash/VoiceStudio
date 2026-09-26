// Gallery polish — the borderless workspace redesign.
//
// Guards the design contract without snapshotting pixels: one shared fluid
// card grid (no fixed px floor that overflows narrow shells), theme-token
// card surfaces (no hardcoded white overlays), and the single section-header
// component every zone uses.
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import ArchetypeCard from './ArchetypeCard';
import GallerySectionHeader from './GallerySectionHeader';
import { GALLERY_GRID } from './constants';

const t = (_key, options = {}) => options.defaultValue || _key;

const archetype = {
  id: 'narrator',
  name: 'Narrator',
  language: 'English',
  use_case: 'narration',
  facets: { gender: 'female', age: 'adult', pitch: 'moderate pitch' },
  attrs: {},
};

describe('Gallery polish', () => {
  it('shares one fluid card grid with no fixed pixel floor', () => {
    expect(GALLERY_GRID).toContain('gallery-cards');
    expect(GALLERY_GRID).toContain('min(264px,100%)');
    expect(GALLERY_GRID).not.toContain('minmax(248px');
  });

  it('renders section headers with a title, count pill, and hairline hook', () => {
    render(<GallerySectionHeader title="Browse all" count={128} />);
    expect(screen.getByText('Browse all')).toBeTruthy();
    expect(screen.getByTestId('gallery-section-count')).toHaveTextContent('128');
    expect(document.querySelector('.gallery-section-header')).toBeTruthy();
  });

  it('keeps cards on theme-token surfaces with a rounded shell', () => {
    const { container } = render(
      <ArchetypeCard
        a={archetype}
        t={t}
        isFavorite={false}
        isPlaying={false}
        isLoadingPreview={false}
        onPreview={vi.fn()}
        onUse={vi.fn()}
        onDesign={vi.fn()}
        onToggleFavorite={vi.fn()}
      />,
    );
    const card = container.querySelector('[data-testid="gallery-persona-card"]');
    expect(card.className).toContain('rounded-[12px]');
    expect(card.className).toContain('color-mix(in_srgb,var(--chrome-fg)');
    expect(card.className).not.toContain('rgba(255,255,255');
  });

  it('tints (not just rings) the playing card', () => {
    const { container } = render(
      <ArchetypeCard
        a={archetype}
        t={t}
        isFavorite={false}
        isPlaying
        isLoadingPreview={false}
        onPreview={vi.fn()}
        onUse={vi.fn()}
        onDesign={vi.fn()}
        onToggleFavorite={vi.fn()}
      />,
    );
    const card = container.querySelector('[data-testid="gallery-persona-card"]');
    expect(card.className).toContain('var(--card-accent)');
  });

  it('resets native button faces and never wraps the Use-voice label', () => {
    const { container } = render(
      <ArchetypeCard
        a={archetype}
        t={t}
        isFavorite={false}
        isPlaying={false}
        isLoadingPreview={false}
        onPreview={vi.fn()}
        onUse={vi.fn()}
        onDesign={vi.fn()}
        onUseInStories={vi.fn()}
        onUseAsAudiobookDefault={vi.fn()}
        onToggleFavorite={vi.fn()}
      />,
    );
    const card = container.querySelector('[data-testid="gallery-persona-card"]');
    // Every native <button> on the card carries the reset (no preflight).
    for (const btn of card.querySelectorAll('button')) {
      expect(btn.className).toContain('border-transparent');
    }
    const useBtn = screen.getByRole('button', { name: 'Use voice' });
    expect(useBtn.className).toContain('whitespace-nowrap');
    // Designer + overflow live in the header cluster, not the action row.
    const header = card.querySelector('.truncate').closest('.flex');
    expect(header.querySelector('[aria-label="Open in Designer"]')).not.toBeNull();
    expect(header.querySelector('[aria-label="More actions"]')).not.toBeNull();
  });
});
