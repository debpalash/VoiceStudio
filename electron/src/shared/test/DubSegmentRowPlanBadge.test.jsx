import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import '../i18n';

// Pre-synthesis duration-plan badge — the /dub/translate response stamps a
// per-segment `plan` (fits/tight/impossible + optional condensed rewrite);
// the row must warn on tight/impossible BEFORE any GPU time is spent, stay
// silent on fits, and let the user apply a suggested rewrite in one click.

// The row now embeds the shared VoiceSelector, which reads /archetypes and
// materializes gallery picks. Mock both so this badge-focused test needs no
// react-query provider or backend (the dropdown stays closed → no fetch anyway).
vi.mock('../api/hooks', () => ({ useArchetypes: vi.fn(() => ({ data: undefined })) }));
vi.mock('../api/archetypes', () => ({ useArchetypeAsProfile: vi.fn() }));

import DubSegmentRow from '../components/DubSegmentRow';

function makeProps(plan, over = {}) {
  return {
    seg: {
      id: 's1',
      start: 0,
      end: 2,
      text: 'hola mundo',
      plan,
    },
    idx: 0,
    disabled: false,
    isActive: false,
    isDone: false,
    isPlaying: false,
    previewLoading: false,
    selected: false,
    profiles: [],
    speakerClones: {},
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

describe('DubSegmentRow duration-plan badge', () => {
  it('warns on an impossible segment with the estimated overrun', () => {
    render(
      <DubSegmentRow
        {...makeProps({
          status: 'impossible',
          est_dur_s: 6.9,
          available_s: 2.0,
          est_overrun_s: 4.9,
          calibrated: false,
        })}
      />,
    );
    expect(screen.getByText(/Won't fit \+4\.9s/)).toBeTruthy();
  });

  it('flags a tight segment', () => {
    render(
      <DubSegmentRow
        {...makeProps({
          status: 'tight',
          est_dur_s: 3.0,
          available_s: 2.0,
          est_overrun_s: 1.0,
          calibrated: true,
        })}
      />,
    );
    expect(screen.getByText(/Tight fit/)).toBeTruthy();
  });

  it('stays silent when the plan says fits', () => {
    render(
      <DubSegmentRow
        {...makeProps({
          status: 'fits',
          est_dur_s: 1.0,
          available_s: 2.0,
          est_overrun_s: 0,
          calibrated: false,
        })}
      />,
    );
    expect(screen.queryByText(/Tight fit/)).toBeNull();
    expect(screen.queryByText(/Won't fit/)).toBeNull();
  });

  it('applies the condensed rewrite via onEditField — never automatically', () => {
    const props = makeProps({
      status: 'impossible',
      est_dur_s: 6.9,
      available_s: 2.0,
      est_overrun_s: 4.9,
      calibrated: false,
      suggested_text: 'hola',
    });
    render(<DubSegmentRow {...props} />);
    // The row still shows the original text — suggestions are opt-in per click.
    expect(screen.getByDisplayValue('hola mundo')).toBeTruthy();
    fireEvent.click(screen.getByText(/Use shorter rewrite/));
    expect(props.onEditField).toHaveBeenCalledWith('s1', 'text', 'hola');
  });
});
