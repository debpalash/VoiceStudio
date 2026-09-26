import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import VoiceSelector from '../components/VoiceSelector';
import { useArchetypes } from '../api/hooks';
import { useArchetypeAsProfile } from '../api/archetypes';
import { useAppStore } from '../store';

// VoiceSelector's gallery group (#1219) fetches /archetypes (via useArchetypes)
// and materializes a pick (useArchetypeAsProfile). Mock both so the picker's
// gallery + materialize-on-select can be tested without a backend.
vi.mock('../api/hooks', () => ({ useArchetypes: vi.fn(() => ({ data: undefined })) }));
vi.mock('../api/archetypes', () => ({ useArchetypeAsProfile: vi.fn() }));

const PROFILES = [
  { id: 'p_clone', name: 'Aria' }, // falsy instruct → clone
  { id: 'p_design', name: 'Narrator', instruct: 'warm, deep' }, // designed
];

const GALLERY_ITEMS = [
  { id: 'a_lib', name: 'The Librarian', icon: 'Library' },
  { id: 'a_mate', name: 'The Mate', icon: 'Coffee' },
];

function renderVS(props) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <VoiceSelector {...props} />
    </QueryClientProvider>,
  );
}

function open() {
  // The trigger is the only button until the popup opens.
  fireEvent.click(screen.getAllByRole('button')[0]);
}

describe('VoiceSelector', () => {
  beforeEach(() => {
    window.localStorage.clear();
    // jsdom doesn't implement scrollIntoView; SearchableSelect calls it on open.
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    vi.mocked(useArchetypes).mockReset().mockReturnValue({ data: undefined });
    vi.mocked(useArchetypeAsProfile).mockReset();
    useAppStore.setState({ favoriteArchetypeIds: [] });
  });

  it('renders engine-default first, then grouped clone/designed options', () => {
    renderVS({ value: '', onChange: vi.fn(), profiles: PROFILES });
    // trigger shows the engine-default label
    expect(screen.getByRole('button', { name: /Engine default/ })).toBeInTheDocument();
    open();
    expect(screen.getByText('Aria')).toBeInTheDocument();
    expect(screen.getByText('Narrator')).toBeInTheDocument();
    // group headers present (designed split from clone)
    expect(screen.getByText('Cloned voices')).toBeInTheDocument();
    expect(screen.getByText('Designed voices')).toBeInTheDocument();
  });

  it('commits the profile id (value contract) on click', () => {
    const onChange = vi.fn();
    renderVS({ value: '', onChange, profiles: PROFILES });
    open();
    fireEvent.mouseDown(screen.getByText('Aria'));
    expect(onChange).toHaveBeenCalledWith('p_clone');
  });

  it('emits preset:<id> values when presets enabled', () => {
    const onChange = vi.fn();
    renderVS({ value: '', onChange, profiles: [], presets: true });
    open();
    expect(screen.getByText('Presets')).toBeInTheDocument();
    // the first preset row commits a preset: value
    const presetRow = screen
      .getAllByRole('option')
      .find((el) => el.textContent && /Authoritative|Preset|🎙/.test(el.textContent));
    // fall back to any non-default option if preset names change
    fireEvent.mouseDown(presetRow || screen.getAllByRole('option')[1]);
    expect(onChange.mock.calls[0][0]).toMatch(/^preset:/);
  });

  it('slugs from-video speakers to auto:<slug> (byte-identical to dub)', () => {
    const onChange = vi.fn();
    renderVS({ value: '', onChange, profiles: [], speakerClones: { 'Speaker 1': {} } });
    open();
    expect(screen.getByText('From video')).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByText('🎤 Speaker 1'));
    expect(onChange).toHaveBeenCalledWith('auto:speaker_1');
  });

  it('uses the canonical backend slug for raw diarizer speaker ids', () => {
    const onChange = vi.fn();
    renderVS({ value: '', onChange, profiles: [], speakerClones: { SPEAKER_00: {} } });
    open();
    fireEvent.mouseDown(screen.getByText('🎤 SPEAKER_00'));
    expect(onChange).toHaveBeenCalledWith('auto:speaker00');
  });

  it('renders a ghost row (does NOT auto-clear) for a deleted-but-referenced voice', () => {
    const onChange = vi.fn();
    renderVS({ value: 'p_gone', onChange, profiles: PROFILES });
    // trigger shows a human label, not the raw id
    expect(screen.getByRole('button', { name: /Voice not found/ })).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled(); // value preserved
  });

  it('does NOT record sentinel values (engine-default) as recents', () => {
    const onChange = vi.fn();
    renderVS({ value: 'p_clone', onChange, profiles: PROFILES, recentsKey: 'vs_test' });
    open();
    // pick engine default ('')
    fireEvent.mouseDown(screen.getByText('Engine default'));
    expect(onChange).toHaveBeenCalledWith('');
    const recents = JSON.parse(window.localStorage.getItem('vs_test') || '[]');
    expect(recents).not.toContain('');
  });

  it('DOES record a real profile id as a recent', () => {
    renderVS({ value: '', onChange: vi.fn(), profiles: PROFILES, recentsKey: 'vs_test2' });
    open();
    fireEvent.mouseDown(screen.getByText('Aria'));
    const recents = JSON.parse(window.localStorage.getItem('vs_test2') || '[]');
    expect(recents).toContain('p_clone');
  });

  it('renders a preview button only when onPreview is provided, passing the current value', () => {
    const onPreview = vi.fn();
    const { rerender } = renderVS({
      value: 'p_clone',
      onChange: vi.fn(),
      profiles: PROFILES,
      onPreview,
    });
    const previewBtn = screen.getByRole('button', { name: /Preview voice/ });
    fireEvent.click(previewBtn);
    expect(onPreview).toHaveBeenCalledWith('p_clone');

    // absent without the prop
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <VoiceSelector value="p_clone" onChange={vi.fn()} profiles={PROFILES} />
      </QueryClientProvider>,
    );
    expect(screen.queryByRole('button', { name: /Preview voice/ })).not.toBeInTheDocument();
  });

  it('disables the preview button while previewLoading', () => {
    renderVS({
      value: 'p_clone',
      onChange: vi.fn(),
      profiles: PROFILES,
      onPreview: vi.fn(),
      previewLoading: true,
    });
    expect(screen.getByRole('button', { name: /Preview voice/ })).toBeDisabled();
  });

  // ── Gallery (archetype) group + materialize-on-select (#1219) ──────────────
  describe('gallery group', () => {
    it('renders a searchable Gallery group with favorites listed first', () => {
      useAppStore.setState({ favoriteArchetypeIds: ['a_mate'] });
      vi.mocked(useArchetypes).mockReturnValue({ data: { items: GALLERY_ITEMS } });
      renderVS({ value: '', onChange: vi.fn(), profiles: PROFILES });
      open();
      expect(screen.getByText('Gallery')).toBeInTheDocument(); // group header
      expect(screen.getByText('The Librarian')).toBeInTheDocument();
      expect(screen.getByText('The Mate')).toBeInTheDocument();
      // Favorite (The Mate) is ordered before the non-favorite in the gallery.
      const options = screen.getAllByRole('option').map((o) => o.textContent);
      const mate = options.findIndex((tx) => tx.includes('The Mate'));
      const lib = options.findIndex((tx) => tx.includes('The Librarian'));
      expect(mate).toBeGreaterThanOrEqual(0);
      expect(mate).toBeLessThan(lib);
    });

    it('does NOT fetch archetypes until the dropdown opens', () => {
      renderVS({ value: '', onChange: vi.fn(), profiles: PROFILES });
      // enabled === false while closed
      expect(vi.mocked(useArchetypes).mock.calls.at(-1)[1]).toBe(false);
      open();
      expect(vi.mocked(useArchetypes).mock.calls.at(-1)[1]).toBe(true);
    });

    it('materializes a gallery pick and emits the profile id (never archetype:<id>)', async () => {
      vi.mocked(useArchetypes).mockReturnValue({ data: { items: GALLERY_ITEMS } });
      vi.mocked(useArchetypeAsProfile).mockResolvedValue({
        profile_id: 'p_new',
        name: 'The Librarian',
      });
      const onChange = vi.fn();
      renderVS({ value: '', onChange, profiles: PROFILES });
      open();
      fireEvent.mouseDown(screen.getByText('The Librarian'));
      await waitFor(() =>
        expect(vi.mocked(useArchetypeAsProfile)).toHaveBeenCalledWith('a_lib', 'The Librarian'),
      );
      await waitFor(() => expect(onChange).toHaveBeenCalledWith('p_new'));
      // The archetype sentinel must NEVER reach the parent / backend.
      expect(onChange).not.toHaveBeenCalledWith('archetype:a_lib');
    });

    it('keeps the previous value when materializing fails', async () => {
      vi.mocked(useArchetypes).mockReturnValue({ data: { items: GALLERY_ITEMS } });
      vi.mocked(useArchetypeAsProfile).mockRejectedValue(new Error('boom'));
      const onChange = vi.fn();
      renderVS({ value: 'p_clone', onChange, profiles: PROFILES });
      open();
      fireEvent.mouseDown(screen.getByText('The Mate'));
      await waitFor(() =>
        expect(vi.mocked(useArchetypeAsProfile)).toHaveBeenCalledWith('a_mate', 'The Mate'),
      );
      // onChange never fires on the error path → value preserved.
      expect(onChange).not.toHaveBeenCalled();
    });
  });
});
