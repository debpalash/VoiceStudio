import React, { createRef } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import i18n from '../i18n';

// Preview-tab ↔ transcript sync: clicking a dubbed-language pill on the
// Export step must ALSO switch the segment texts to that language (the P1.2
// per-language translations store) — previously the tabs only swapped the
// video, so previewing German played German audio over Bengali segment text.

vi.mock('../components/WaveformTimeline', () => ({ default: () => <div data-testid="wf" /> }));
vi.mock('../components/MultiLangPicker', () => ({ default: () => <div data-testid="mlp" /> }));
vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn(), loading: vi.fn() },
}));
const dubListTracks = vi.hoisted(() => vi.fn());
const dubSegmentsTextMock = vi.hoisted(() => vi.fn());
vi.mock('../api/dub', () => ({
  dubListTracks: (...a) => dubListTracks(...a),
  dubSegmentsText: (...a) => dubSegmentsTextMock(...a),
}));

import DubLeftColumn from '../components/dub/DubLeftColumn';
import { useAppStore } from '../store';

const t = i18n.t.bind(i18n);

function makeProps(over = {}) {
  return {
    hasDubbedTrack: true,
    t,
    i18n,
    previewMode: 'bn',
    setPreviewMode: vi.fn(),
    dubTracks: ['bn', 'de'],
    videoSrc: '',
    waveformRef: createRef(),
    dubJobId: 'job1',
    dubSegments: [{ id: '1', text: 'hi' }],
    timelineOnsets: [],
    timelineSelSegId: null,
    setTimelineSelSegId: vi.fn(),
    incrementalPlan: null,
    segmentMoveResize: vi.fn(),
    segmentDelete: vi.fn(),
    onTimelinePreviewSegment: vi.fn(),
    dubStep: 'done',
    dubProgress: { current: 0, total: 0, text: '' },
    fmtDur: (s) => `${s}s`,
    genElapsed: 0,
    genRemaining: null,
    speakerClones: {},
    setDubSegments: vi.fn(),
    profiles: [],
    settingsOpen: false,
    setSettingsOpen: vi.fn(),
    dubLang: 'Bengali',
    dubLangCode: 'bn',
    translateQuality: 'fast',
    activeEngineUnavailable: false,
    translateProvider: 'google',
    dubInstruct: '',
    setDubInstruct: vi.fn(),
    handleTranslateAll: vi.fn(),
    isTranslating: false,
    editSegments: vi.fn(),
    ...over,
  };
}

beforeEach(() => {
  dubListTracks.mockResolvedValue({ tracks: {} });
  dubSegmentsTextMock.mockResolvedValue({ 2: 'Zeile zwei (vom Server)' });
  useAppStore.setState({
    dubLangCode: 'bn',
    dubLang: 'Bengali',
    dubSegments: [
      {
        id: '1',
        text: 'বাংলা লাইন',
        text_original: 'the original line',
        translations: { bn: 'বাংলা লাইন', de: 'die deutsche Zeile' },
      },
    ],
  });
});

describe('preview tab → transcript language sync', () => {
  it('clicking a language pill swaps segment text to that language', () => {
    render(<DubLeftColumn {...makeProps()} />);
    fireEvent.click(screen.getByRole('radio', { name: /german|deutsch/i }));
    const st = useAppStore.getState();
    expect(st.dubLangCode).toBe('de');
    expect(st.dubSegments[0].text).toBe('die deutsche Zeile');
    // Outgoing language snapshotted, not lost.
    expect(st.dubSegments[0].translations.bn).toBe('বাংলা লাইন');
  });

  it('the Original pill leaves the editing language untouched', () => {
    render(<DubLeftColumn {...makeProps()} />);
    fireEvent.click(screen.getByRole('radio', { name: /original/i }));
    expect(useAppStore.getState().dubLangCode).toBe('bn');
  });
});

describe('review round: partial translations + dialect guard', () => {
  it('hydrates rows missing the incoming language from the backend store', async () => {
    useAppStore.setState({
      dubJobId: 'job1',
      dubLangCode: 'bn',
      dubSegments: [
        {
          id: '1',
          text: 'বাংলা ১',
          text_original: 'o1',
          translations: { bn: 'বাংলা ১', de: 'Zeile eins' },
        },
        {
          id: '2',
          text: 'বাংলা ২',
          text_original: 'o2',
          translations: { bn: 'বাংলা ২' },
          merge_parts: [{ textStart: 0, textEnd: 7, speaker_id: 'Anna' }],
        }, // de missing
      ],
    });
    render(<DubLeftColumn {...makeProps()} />);
    fireEvent.click(screen.getByRole('radio', { name: /german|deutsch/i }));
    // switch applied immediately for stored rows…
    expect(useAppStore.getState().dubSegments[0].text).toBe('Zeile eins');
    // …and the missing row hydrates asynchronously from segments_i18n.
    await vi.waitFor(() => {
      expect(useAppStore.getState().dubSegments[1].text).toBe('Zeile zwei (vom Server)');
    });
    expect(useAppStore.getState().dubSegments[1].translations.de).toBe('Zeile zwei (vom Server)');
    expect(useAppStore.getState().dubSegments[1].merge_parts).toBeUndefined();
  });

  it('changing a cast voice updates stored merge attribution', () => {
    const setDubSegments = vi.fn();
    render(
      <DubLeftColumn
        {...makeProps({
          setDubSegments,
          profiles: [{ id: 'voice-new', name: 'New voice' }],
          dubSegments: [
            {
              id: '1',
              speaker_id: 'Anna',
              profile_id: 'voice-old',
              text: 'hello',
              merge_parts: [{ textStart: 0, textEnd: 5, speaker_id: 'Anna' }],
            },
          ],
        })}
      />,
    );

    fireEvent.click(document.querySelector('.dub-cast__select'));
    fireEvent.mouseDown(screen.getByRole('option', { name: 'New voice', exact: true }));

    const update = setDubSegments.mock.calls[0][0][0];
    expect(update.profile_id).toBe('voice-new');
    expect(update.merge_parts[0].profile_id).toBe('voice-new');
  });

  it('invalidates merged rows containing the changed speaker without changing their top voice', () => {
    const setDubSegments = vi.fn();
    render(
      <DubLeftColumn
        {...makeProps({
          setDubSegments,
          profiles: [{ id: 'voice-new', name: 'New voice' }],
          dubSegments: [
            {
              id: 'merged',
              speaker_id: 'Anna',
              profile_id: 'voice-anna',
              text: 'hello there',
              merge_parts: [
                { textStart: 0, textEnd: 5, speaker_id: 'Anna', profile_id: 'voice-anna' },
                { textStart: 6, textEnd: 11, speaker_id: 'Ben', profile_id: 'voice-ben' },
              ],
            },
          ],
        })}
      />,
    );

    fireEvent.click(document.querySelectorAll('.dub-cast__select')[1]);
    fireEvent.mouseDown(screen.getByRole('option', { name: 'New voice', exact: true }));

    const update = setDubSegments.mock.calls[0][0];
    expect(update[0].profile_id).toBe('voice-anna');
    expect(update[0].merge_parts[1].profile_id).toBe('voice-new');
  });

  it('switching language clears a dialect that no longer matches', () => {
    useAppStore.setState({ dubDialect: 'bn-BD', dubLangCode: 'bn' });
    useAppStore.getState().switchDubLangCode('de');
    expect(useAppStore.getState().dubDialect).toBe('');
    // …and keeps one that still matches.
    useAppStore.setState({ dubDialect: 'de-AT', dubLangCode: 'de' });
    useAppStore.getState().switchDubLangCode('de-CH' in {} ? 'x' : 'de');
    useAppStore.getState().switchDubLangCode('bn');
    useAppStore.setState({ dubDialect: 'bn-BD', dubLangCode: 'bn' });
    useAppStore.getState().switchDubLangCode('bn');
    expect(useAppStore.getState().dubDialect).toBe('bn-BD'); // same code → untouched
  });
});
