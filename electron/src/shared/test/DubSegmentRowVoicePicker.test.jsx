import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '../i18n';

// The dub per-segment picker migrated from a native <select> to the shared,
// gallery-enabled VoiceSelector (#1220). VoiceSelector fetches /archetypes and
// materializes gallery picks — mock both so the row renders without a backend.
vi.mock('../api/hooks', () => ({ useArchetypes: vi.fn(() => ({ data: undefined })) }));
vi.mock('../api/archetypes', () => ({ useArchetypeAsProfile: vi.fn() }));

import DubSegmentRow from '../components/DubSegmentRow';
import { segmentGenInputs } from '../utils/segments';

const PROFILES = [{ id: 'p_clone', name: 'Aria' }];
const SPEAKER_CLONES = { 'Speaker 1': { duration: 3.2 } };

function makeProps(over = {}) {
  return {
    seg: { id: 's1', start: 0, end: 2, text: 'hola', profile_id: '' },
    idx: 0,
    disabled: false,
    isActive: false,
    isDone: false,
    isPlaying: false,
    previewLoading: false,
    selected: false,
    profiles: PROFILES,
    speakerClones: SPEAKER_CLONES,
    onEditField: vi.fn(),
    onDelete: vi.fn(),
    onRestore: vi.fn(),
    onPreview: vi.fn(),
    onSelect: vi.fn(),
    onSplit: vi.fn(),
    onMerge: vi.fn(),
    canMerge: false,
    onDirect: vi.fn(),
    onSeek: vi.fn(),
    timelineSelected: false,
    ...over,
  };
}

function renderRow(props) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DubSegmentRow {...props} />
    </QueryClientProvider>,
  );
}

function openPicker() {
  // The picker trigger shows the current voice label ("Default" for '').
  fireEvent.click(screen.getByRole('button', { name: /Default/ }));
}

describe('DubSegmentRow voice picker (#1220)', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  it('renders the shared VoiceSelector with clone + from-video options', () => {
    renderRow(makeProps());
    openPicker();
    expect(screen.getByText('Aria')).toBeInTheDocument(); // clone profile
    // from-video auto:<slug> option is preserved via speakerClones
    expect(screen.getByText('🎤 Speaker 1')).toBeInTheDocument();
  });

  it('writes seg.profile_id via onEditField on a pick', () => {
    const onEditField = vi.fn();
    renderRow(makeProps({ onEditField }));
    openPicker();
    fireEvent.mouseDown(screen.getByText('Aria'));
    expect(onEditField).toHaveBeenCalledWith('s1', 'profile_id', 'p_clone');
  });

  it('emits the auto:<slug> value for a from-video speaker (byte-identical to the old select)', () => {
    const onEditField = vi.fn();
    renderRow(makeProps({ onEditField }));
    openPicker();
    fireEvent.mouseDown(screen.getByText('🎤 Speaker 1'));
    expect(onEditField).toHaveBeenCalledWith('s1', 'profile_id', 'auto:speaker_1');
  });

  it('no longer offers the legacy design PRESETS group (superseded by the Gallery)', () => {
    renderRow(makeProps());
    openPicker();
    expect(screen.queryByText('Presets')).not.toBeInTheDocument();
    expect(screen.queryByText(/Authoritative/)).not.toBeInTheDocument();
  });

  // Backward-compat: dropping presets from the PICKER must not break already
  // stored `preset:` segment values — they still expand to instruct on generate
  // via the shared segmentGenInputs helper (utils/segments.js), unchanged.
  it('keeps expanding a stored preset: value on generate (backward compatible)', () => {
    const gi = segmentGenInputs({ text: 'hi', profile_id: 'preset:narrator' });
    expect(gi.profile_id).toBe(''); // preset is not a profile id
    expect(gi.instruct).toContain('male'); // design attrs expanded into instruct
  });
});
