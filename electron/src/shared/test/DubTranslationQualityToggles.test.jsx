import React, { createRef } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import i18n from '../i18n';

// Heavy children we don't exercise here — keep the render focused on the
// translation-settings bar.
vi.mock('../components/WaveformTimeline', () => ({ default: () => <div data-testid="wf" /> }));
vi.mock('../components/MultiLangPicker', () => ({ default: () => <div data-testid="mlp" /> }));
vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn(), loading: vi.fn() },
}));
vi.mock('../api/external', () => ({ openExternal: vi.fn() }));
vi.mock('../utils/copyText', () => ({ copyText: vi.fn().mockResolvedValue(true) }));

import DubLeftColumn from '../components/dub/DubLeftColumn';
import { useAppStore } from '../store';

const t = i18n.t.bind(i18n);

const LLM = {
  id: 'openai',
  display_name: 'LLM (OpenAI-compatible)',
  installed: true,
  pip_package: 'openai',
  install_command: 'uv pip install openai',
};
const GOOGLE = {
  id: 'google',
  display_name: 'Google Translate (Online, Free)',
  installed: true,
  pip_package: 'deep_translator',
  install_command: 'uv pip install deep_translator',
};

function makeProps(over = {}) {
  return {
    hasDubbedTrack: false,
    t,
    i18n,
    previewMode: 'original',
    setPreviewMode: vi.fn(),
    dubTracks: [],
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
    dubStep: 'editing',
    dubProgress: { current: 0, total: 0, text: '' },
    fmtDur: (s) => `${s}s`,
    genElapsed: 0,
    genRemaining: null,
    speakerClones: {},
    setDubSegments: vi.fn(),
    profiles: [],
    settingsOpen: true,
    setSettingsOpen: vi.fn(),
    dubLang: 'Spanish',
    dubLangCode: 'es',
    translateQuality: 'fast',
    activeEngineUnavailable: false,
    translateProvider: 'openai',
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
    activeEngineEntry: LLM,
    engines: [LLM, GOOGLE],
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

describe('Dub translation quality toggles (LLM engine)', () => {
  it('defaults ON in the store and persists via partialize', () => {
    const s = useAppStore.getState();
    expect(s.autoGlossary).toBe(true);
    expect(s.reflectPass).toBe(true);
  });

  it('renders both toggles for the LLM engine and writes the store', () => {
    render(<DubLeftColumn {...makeProps()} />);
    const auto = screen.getByLabelText(t('dub.auto_glossary_label'));
    const reflect = screen.getByLabelText(t('dub.reflect_label'));
    expect(auto.checked).toBe(true);
    expect(reflect.checked).toBe(true);
    // The reflect tooltip must warn about the LLM-call multiplier.
    expect(reflect.closest('label').title).toMatch(/3 LLM calls per segment/i);

    fireEvent.click(reflect);
    expect(useAppStore.getState().reflectPass).toBe(false);
    fireEvent.click(auto);
    expect(useAppStore.getState().autoGlossary).toBe(false);

    // restore defaults for other tests sharing the store
    useAppStore.getState().setReflectPass(true);
    useAppStore.getState().setAutoGlossary(true);
  });

  it('hides both toggles for MT engines — they cannot run either stage', () => {
    render(
      <DubLeftColumn {...makeProps({ translateProvider: 'google', activeEngineEntry: GOOGLE })} />,
    );
    expect(screen.queryByLabelText(t('dub.auto_glossary_label'))).toBeNull();
    expect(screen.queryByLabelText(t('dub.reflect_label'))).toBeNull();
  });

  it('keeps every translation quality selectable without an LLM', () => {
    const setTranslateQuality = vi.fn();
    render(
      <DubLeftColumn
        {...makeProps({
          translateProvider: 'google',
          activeEngineEntry: GOOGLE,
          llmEndpoint: { available: false },
          setTranslateQuality,
        })}
      />,
    );

    fireEvent.click(screen.getByRole('radio', { name: t('dub.autofit_quality') }));
    expect(setTranslateQuality).toHaveBeenCalledWith('autofit');
    fireEvent.click(screen.getByRole('radio', { name: t('dub.cinematic_quality') }));
    expect(setTranslateQuality).toHaveBeenCalledWith('cinematic');
  });
});
