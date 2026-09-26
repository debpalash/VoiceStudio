import React, { createRef } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import i18n from '../i18n';

// Heavy children we don't exercise here — keep the render focused on the
// Engine selector's install affordance.
vi.mock('../components/WaveformTimeline', () => ({ default: () => <div data-testid="wf" /> }));
vi.mock('../components/MultiLangPicker', () => ({
  default: ({ single, onChange }) =>
    single ? (
      <button onClick={() => onChange([{ lang: 'Auto', code: 'Auto' }])}>Select Auto</button>
    ) : (
      <div data-testid="mlp" />
    ),
}));
vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn(), loading: vi.fn() },
}));
const openExternal = vi.fn();
vi.mock('../api/external', () => ({ openExternal: (...a) => openExternal(...a) }));
const copyText = vi.fn().mockResolvedValue(true);
vi.mock('../utils/copyText', () => ({ copyText: (...a) => copyText(...a) }));

import DubLeftColumn from '../components/dub/DubLeftColumn';

const t = i18n.t.bind(i18n);

const GOOGLE = {
  id: 'google',
  display_name: 'Google Translate (Online, Free)',
  installed: false,
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
    activeEngineUnavailable: true,
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
    activeEngineEntry: GOOGLE,
    engines: [GOOGLE],
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

describe('DubLeftColumn — translation-engine install affordance', () => {
  beforeEach(() => {
    openExternal.mockClear();
    copyText.mockClear();
  });

  it('FROM-SOURCE lane: renders a highlighted Install button wired to handleInstallEngine', () => {
    const handleInstallEngine = vi.fn();
    render(<DubLeftColumn {...makeProps({ enginesSandboxed: false, handleInstallEngine })} />);

    // Highlighted accent button (not the muted chip): brand-accent bg class.
    const btn = screen.getByRole('button', { name: /install deep_translator/i });
    expect(btn.className).toMatch(/bg-\[var\(--color-brand\)\]/);

    fireEvent.click(btn);
    expect(handleInstallEngine).toHaveBeenCalledWith('google');
  });

  it('FROZEN lane: opens a popover with the copy-command + Switch-to-Argos + Docs, and NEVER installs', () => {
    const handleInstallEngine = vi.fn();
    const setTranslateProvider = vi.fn();
    render(
      <DubLeftColumn
        {...makeProps({ enginesSandboxed: true, handleInstallEngine, setTranslateProvider })}
      />,
    );

    // No from-source install button in the frozen lane.
    expect(
      screen.queryByRole('button', { name: /install deep_translator/i }),
    ).not.toBeInTheDocument();

    // The highlighted trigger opens the escape-hatch popover.
    const trigger = screen.getByRole('button', { name: /needs install/i });
    expect(trigger.className).toMatch(/bg-\[var\(--color-brand\)\]/);
    fireEvent.click(trigger);

    const dialog = screen.getByRole('dialog');
    // Exact install command, single-sourced from install_command.
    expect(within(dialog).getByText('uv pip install deep_translator')).toBeInTheDocument();

    // Copy-to-clipboard works.
    fireEvent.click(within(dialog).getByRole('button', { name: t('dub.copy_command') }));
    expect(copyText).toHaveBeenCalledWith('uv pip install deep_translator');

    // Docs deeplink opens via the Tauri shell.open path.
    fireEvent.click(within(dialog).getByRole('button', { name: t('dub.open_docs') }));
    expect(openExternal).toHaveBeenCalledWith(expect.stringContaining('translation-engines.md'));

    // Guaranteed offline escape hatch: switch to Argos.
    fireEvent.click(within(dialog).getByRole('button', { name: /switch to argos/i }));
    expect(setTranslateProvider).toHaveBeenCalledWith('argos');

    // Critical: the frozen lane must never trigger an install.
    expect(handleInstallEngine).not.toHaveBeenCalled();
  });

  it('changing the engine <select> fires setTranslateProvider (the error-clearing corrective action)', () => {
    const setTranslateProvider = vi.fn();
    const engines = [
      GOOGLE,
      { id: 'argos', display_name: 'Argos', installed: true, install_command: null },
    ];
    render(<DubLeftColumn {...makeProps({ engines, setTranslateProvider })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Engine', exact: true }));
    fireEvent.mouseDown(screen.getByRole('option', { name: 'Argos', exact: true }));
    expect(setTranslateProvider).toHaveBeenCalledWith('argos');
  });
});

describe('translation language consistency', () => {
  it('clears the previous ISO code and dialect when Auto is selected', () => {
    const props = makeProps({ dubLang: 'Chinese', dubLangCode: 'zh', dubDialect: 'zh-CN' });
    render(<DubLeftColumn {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Select Auto' }));
    expect(props.setDubLang).toHaveBeenCalledWith('Auto');
    expect(props.setDubLangCode).toHaveBeenCalledWith('Auto');
    expect(props.setDubDialect).toHaveBeenCalledWith('');
  });
  it('updates the language name when choosing an ISO code', () => {
    const props = makeProps({ dubDialect: 'zh-CN' });
    render(<DubLeftColumn {...props} />);
    fireEvent.click(screen.getByRole('button', { name: t('dub.iso_code') }));
    fireEvent.mouseDown(screen.getByRole('option', { name: 'en — English' }));
    expect(props.setDubLang).toHaveBeenCalledWith('English');
    expect(props.setDubLangCode).toHaveBeenCalledWith('en');
    expect(props.setDubDialect).toHaveBeenCalledWith('');
  });
});
