import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import ArchetypesZone from './ArchetypesZone';
import ArchetypeCard from './ArchetypeCard';
import { useArchetypes } from '../../api/hooks';

vi.mock('../../api/hooks', () => ({
  useArchetypeCategories: () => ({
    data: [
      { id: 'narration', name: 'Narration & Story', icon: 'BookOpen' },
      { id: 'social', name: 'Social Media', icon: 'Radio' },
    ],
  }),
  useArchetypes: vi.fn((filters) => ({
    data: filters.featured ? { items: [] } : { items: [], total: 0 },
    isLoading: false,
    isFetching: false,
  })),
}));

const t = (_key, options = {}) => options.defaultValue || _key;

const baseProps = {
  t,
  filters: {
    use_case: null,
    gender: null,
    age: null,
    pitch: null,
    accent: null,
    whisper: null,
    lang: null,
  },
  setFilter: vi.fn(),
  resetFilters: vi.fn(),
  favorites: [],
  toggleFavorite: vi.fn(),
  viewMode: 'grid',
  setViewMode: vi.fn(),
  playingId: null,
  loadingPreviewId: null,
  onPreview: vi.fn(),
  onUse: vi.fn(),
  onDesign: vi.fn(),
};

describe('ArchetypesZone filter toolbar', () => {
  it('keeps categories in one menu and reveals advanced filters on demand', () => {
    const setFilter = vi.fn();
    render(<ArchetypesZone {...baseProps} setFilter={setFilter} />);

    const categoryMenu = screen.getByRole('combobox', { name: 'Archetypes' });
    expect(screen.getAllByRole('combobox')).toHaveLength(1);

    fireEvent.change(categoryMenu, { target: { value: 'social' } });
    expect(setFilter).toHaveBeenCalledWith('use_case', 'social');

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
    expect(screen.getAllByRole('combobox')).toHaveLength(6);
    expect(screen.getByRole('checkbox', { name: 'Whisper' })).toBeInTheDocument();
  });
});

describe('ArchetypeCard accessibility', () => {
  it('names icon actions and exposes favorite state', () => {
    render(
      <ArchetypeCard
        a={{
          id: 'narrator',
          name: 'Narrator',
          language: 'English',
          use_case: 'narration',
          facets: { gender: 'female', age: 'adult', pitch: 'moderate pitch' },
          attrs: {},
        }}
        t={t}
        isFavorite
        isPlaying={false}
        isLoadingPreview={false}
        onPreview={vi.fn()}
        onUse={vi.fn()}
        onDesign={vi.fn()}
        onToggleFavorite={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Favorite' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Open in Designer' })).toBeInTheDocument();
  });

  it('exposes the Stories and Audiobook handoffs from More actions', async () => {
    const onUseInStories = vi.fn();
    const onUseAsAudiobookDefault = vi.fn();
    const archetype = {
      id: 'narrator',
      name: 'Narrator',
      language: 'English',
      use_case: 'narration',
      facets: { gender: 'female', age: 'adult', pitch: 'moderate pitch' },
      attrs: {},
    };
    render(
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
        onUseInStories={onUseInStories}
        onUseAsAudiobookDefault={onUseAsAudiobookDefault}
      />,
    );

    fireEvent.pointerDown(screen.getByRole('button', { name: 'More actions' }), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Use in Stories' }));
    expect(onUseInStories).toHaveBeenCalledWith(archetype);

    fireEvent.pointerDown(screen.getByRole('button', { name: 'More actions' }), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Set as Audiobook default' }));
    expect(onUseAsAudiobookDefault).toHaveBeenCalledWith(archetype);
  });
});

describe('ArchetypesZone enhanced filters', () => {
  it('searches the whole catalog by name after a short debounce', async () => {
    render(<ArchetypesZone {...baseProps} />);
    const search = screen.getByRole('textbox', { name: 'Search…' });
    fireEvent.change(search, { target: { value: 'Librarian' } });
    await waitFor(
      () => {
        const calls = vi.mocked(useArchetypes).mock.calls;
        expect(calls.some(([f]) => f?.q === 'Librarian')).toBe(true);
      },
      { timeout: 2000 },
    );
  });

  it('clears the search from its inline button', async () => {
    render(<ArchetypesZone {...baseProps} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Search…' }), {
      target: { value: 'Librarian' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(screen.getByRole('textbox', { name: 'Search…' })).toHaveValue('');
  });

  it('shows each active filter as an iconified removable pill', () => {
    const setFilter = vi.fn();
    render(
      <ArchetypesZone
        {...baseProps}
        setFilter={setFilter}
        filters={{ ...baseProps.filters, gender: 'female' }}
      />,
    );
    // Pill carries the facet icon + readable label.
    const pill = screen.getByText('Female').closest('span[class*="rounded-"]');
    expect(pill.querySelector('svg')).not.toBeNull();
    // The test t() mock leaves {{term}} uninterpolated — match that literally.
    fireEvent.click(screen.getByRole('button', { name: 'Remove {{term}}' }));
    expect(setFilter).toHaveBeenCalledWith('gender', null);
  });

  it('clears everything from the pills row at once', () => {
    const setFilter = vi.fn();
    const resetFilters = vi.fn();
    render(
      <ArchetypesZone
        {...baseProps}
        setFilter={setFilter}
        resetFilters={resetFilters}
        filters={{ ...baseProps.filters, gender: 'female' }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(resetFilters).toHaveBeenCalledOnce();
  });

  it('iconifies the facet selects in the Filters panel', () => {
    const { container } = render(<ArchetypesZone {...baseProps} />);
    fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
    // Category + 5 facet selects each sit beside their dimension icon.
    const icons = container.querySelectorAll('.border-b span[class*="inline-flex"] > svg');
    expect(icons.length).toBeGreaterThanOrEqual(6);
  });
});
