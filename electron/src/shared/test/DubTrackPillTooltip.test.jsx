import React, { createRef } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import i18n from '../i18n';

// P0.2 — track-bar polish: the Original/<lang> pills enrich with per-track
// metadata (duration + timing strategy) hydrated lazily from the existing
// GET /dub/tracks/{job_id}. The fetch must be failure-silent: the pills are
// the P0 visibility fix and can never depend on the enrichment call.

vi.mock('../components/WaveformTimeline', () => ({ default: () => <div data-testid="wf" /> }));
vi.mock('../components/MultiLangPicker', () => ({ default: () => <div data-testid="mlp" /> }));
vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn(), loading: vi.fn() },
}));
const dubListTracks = vi.hoisted(() => vi.fn());
vi.mock('../api/dub', () => ({ dubListTracks: (...a) => dubListTracks(...a) }));

import DubLeftColumn from '../components/dub/DubLeftColumn';

const t = i18n.t.bind(i18n);

function makeProps(over = {}) {
  return {
    hasDubbedTrack: true,
    t,
    i18n,
    previewMode: 'bn',
    setPreviewMode: vi.fn(),
    dubTracks: ['bn'],
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
    hasAnyTranslation: false,
    handleCleanupSegments: vi.fn(),
    setDubLang: vi.fn(),
    setDubLangCode: vi.fn(),
    dubDialect: '',
    setDubDialect: vi.fn(),
    enginesSandboxed: false,
    handleInstallEngine: vi.fn(),
    engineInstalling: null,
    activeEngineEntry: undefined,
    engines: [],
    setTranslateProvider: vi.fn(),
    setTranslateQuality: vi.fn(),
    llmEndpoint: { available: true },
    multiLangMode: false,
    setMultiLangMode: vi.fn(),
    multiLangs: [],
    setMultiLangs: vi.fn(),
    editSegments: vi.fn(),
    ...over,
  };
}

describe('DubLeftColumn — track pill tooltips (P0.2)', () => {
  beforeEach(() => {
    dubListTracks.mockReset();
  });

  it('hydrates duration + timing strategy from /dub/tracks and reflects the previewed track', async () => {
    dubListTracks.mockResolvedValue({
      bn: { duration: 72.4, timing_strategy: 'smart_fit', language: 'Bengali' },
    });
    render(<DubLeftColumn {...makeProps()} />);

    const pill = screen.getByRole('radio', { name: 'Bengali' });
    // Selection indicator must track previewMode (accurate post-restore).
    expect(pill).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: t('dub.original_audio') })).toHaveAttribute(
      'aria-checked',
      'false',
    );

    expect(dubListTracks).toHaveBeenCalledWith('job1');
    await waitFor(() => expect(pill).toHaveAttribute('title', 'Duration 72s · Timing Smart Fit'));
  });

  it('is failure-silent: a failed metadata fetch leaves the pills fully usable', async () => {
    dubListTracks.mockRejectedValue(new Error('boom'));
    render(<DubLeftColumn {...makeProps()} />);

    expect(dubListTracks).toHaveBeenCalledWith('job1');
    const pill = await screen.findByRole('radio', { name: 'Bengali' });
    await waitFor(() => expect(dubListTracks).toHaveBeenCalled());
    expect(pill).not.toHaveAttribute('title');
  });

  it('does not call the endpoint when there are no dubbed tracks', () => {
    render(
      <DubLeftColumn
        {...makeProps({ hasDubbedTrack: false, dubTracks: [], dubStep: 'editing' })}
      />,
    );
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
    expect(dubListTracks).not.toHaveBeenCalled();
  });
});
