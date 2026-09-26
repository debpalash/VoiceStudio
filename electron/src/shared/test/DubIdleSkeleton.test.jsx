import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

import IdleSkeleton from '../components/dub/IdleSkeleton';
import {
  _cookieTransportAllowed,
  dubIngestUrl,
  DUB_COOKIE_SIZE_ERROR,
  MAX_COOKIE_EXPORT_BYTES,
} from '../api/dub';

// Regression guard for the Dub "transcribe-idle-desync" bug: on the
// URL-ingest (and restored-job) path there is no local `dubVideoFile`, so the
// waveform-overlay branch never runs. Before the fix, the no-file path handled
// only `uploading` and otherwise fell through to the idle dropzone — meaning
// while the pipeline was actively transcribing, the stepper showed
// Transcribe (active) but the main pane still showed the "Drop video here"
// dropzone + paste-URL input + "Pull YouTube captions" checkbox. The main
// view must instead reflect the pipeline stage on every ingest path.

const DROP_HINT = 'Drop video or audio here';
const URL_PLACEHOLDER = '…or paste YouTube / video URL';
const TRANSCRIBING = 'Transcribing audio…';

function baseProps(overrides = {}) {
  const noop = vi.fn();
  return {
    t: i18n.t,
    uiLocale: 'en',
    dubVideoFile: null, // URL-ingest / restored job: no local File
    activeProjectName: '',
    dubFilename: '',
    dubError: '',
    dubJobId: null,
    dubStep: 'idle',
    dubFailure: null,
    asrInstall: null,
    handleInstallMissingAsr: noop,
    handleDubRetryTranscribe: noop,
    handleDubImportSrt: noop,
    dubLocalBlobUrl: null,
    dubPrepStage: null,
    dubPrepProgress: { percent: null, speedBps: null, etaS: null, stageStartedAt: null },
    handleDubAbort: noop,
    transcribeElapsed: 7,
    dubDuration: 60,
    dubNumSpeakers: null,
    setDubNumSpeakers: noop,
    handleDubUpload: noop,
    demoDismissed: true, // skip DubbingDemo (network manifest) in the idle branch
    dismissDubDemo: noop,
    setDubVideoFile: noop,
    setDubInputType: noop,
    setDubStep: noop,
    fileToMediaUrl: noop,
    setDubLocalBlobUrl: noop,
    ingestUrl: '',
    setIngestUrl: noop,
    onIngestUrl: noop,
    fetchYtSubs: false,
    setFetchYtSubs: noop,
    youtubeCookieFile: null,
    setYoutubeCookieFile: noop,
    dubLangCode: 'en',
    dubSourceLangCode: 'auto',
    setDubSourceLangCode: noop,
    setDubLangCode: noop,
    setDubLang: noop,
    landingAdvOpen: false,
    setLandingAdvOpen: noop,
    dubInstruct: '',
    setDubInstruct: noop,
    ...overrides,
  };
}

function renderIdle(overrides) {
  return render(
    <I18nextProvider i18n={i18n}>
      <IdleSkeleton {...baseProps(overrides)} />
    </I18nextProvider>,
  );
}

describe('IdleSkeleton — pipeline-stage vs idle dropzone', () => {
  it('never sends cookie credentials over remote plaintext HTTP', () => {
    expect(_cookieTransportAllowed('http://127.0.0.1:3900')).toBe(true);
    expect(_cookieTransportAllowed('https://studio.example.test')).toBe(true);
    expect(_cookieTransportAllowed('http://studio.example.test')).toBe(false);
  });
  it('rejects an oversized cookie export before reading it', async () => {
    const cookieFile = {
      size: MAX_COOKIE_EXPORT_BYTES + 1,
      text: vi.fn(),
    };
    await expect(
      dubIngestUrl('https://youtube.com/watch?v=abc', 'job', { cookieFile }),
    ).rejects.toMatchObject({
      code: DUB_COOKIE_SIZE_ERROR,
    });
    expect(cookieFile.text).not.toHaveBeenCalled();
  });
  it('shows the idle dropzone only when the pipeline is truly idle (no job)', () => {
    const { container } = renderIdle({ dubStep: 'idle', dubJobId: null });
    expect(container.querySelector('.dub-start-screen')).not.toBeNull();
    expect(container.querySelector('.dub-start-card')).not.toBeNull();
    expect(container.querySelector('.dub-idle-drop.dub-start-drop')).not.toBeNull();
    expect(screen.getByText(DROP_HINT)).toBeInTheDocument();
    expect(screen.getByText(i18n.t('gallery.upload'))).toBeInTheDocument();
    expect(screen.getByPlaceholderText(URL_PLACEHOLDER)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: i18n.t('dub.source_language') })).toBeVisible();
    expect(screen.getByRole('button', { name: i18n.t('dub.target_language') })).toBeVisible();
    expect(screen.getByLabelText('Choose a cookies.txt export')).not.toBeVisible();
  });

  it('opens the native file picker from the keyboard-accessible import button', () => {
    const { container } = renderIdle();
    const input = container.querySelector('#video-upload');
    const clickInput = vi.spyOn(input, 'click');
    const upload = screen.getByRole('button', { name: i18n.t('gallery.upload') });

    expect(upload.tagName).toBe('BUTTON');
    upload.focus();
    expect(upload).toHaveFocus();
    fireEvent.click(upload);

    expect(clickInput).toHaveBeenCalledOnce();
  });

  it('accepts a local audio file from the central import control', async () => {
    const setDubVideoFile = vi.fn();
    const setDubInputType = vi.fn();
    const setDubStep = vi.fn();
    const setDubLocalBlobUrl = vi.fn((next) => {
      if (typeof next === 'function') next(null);
    });
    const urls = { audioUrl: 'blob:audio', videoUrl: null };
    const fileToMediaUrl = vi.fn().mockResolvedValue(urls);
    const { container } = renderIdle({
      setDubVideoFile,
      setDubInputType,
      setDubStep,
      setDubLocalBlobUrl,
      fileToMediaUrl,
    });
    const file = new File(['audio'], 'chapter.wav', { type: 'audio/wav' });

    fireEvent.change(container.querySelector('#video-upload'), { target: { files: [file] } });

    expect(setDubVideoFile).toHaveBeenCalledWith(file);
    expect(setDubInputType).toHaveBeenCalledWith('audio');
    expect(setDubStep).toHaveBeenCalledWith('idle');
    await waitFor(() => expect(fileToMediaUrl).toHaveBeenCalledWith(file, null));
    expect(setDubLocalBlobUrl).toHaveBeenLastCalledWith(urls);
  });

  it('submits a pasted URL from both the button and Enter key', () => {
    const onIngestUrl = vi.fn();
    renderIdle({ ingestUrl: 'https://example.test/video', onIngestUrl });
    const input = screen.getByRole('textbox', { name: URL_PLACEHOLDER });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('dub.ingest') }));
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(onIngestUrl).toHaveBeenCalledTimes(2);
  });

  it('keeps optional import and generation settings behind Advanced', () => {
    const setLandingAdvOpen = vi.fn();
    const { rerender } = renderIdle({ landingAdvOpen: false, setLandingAdvOpen });
    const advanced = screen.getByRole('button', { name: i18n.t('dub.advanced') });

    expect(advanced).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByLabelText('Choose a cookies.txt export')).not.toBeVisible();
    fireEvent.click(advanced);
    expect(setLandingAdvOpen).toHaveBeenCalledOnce();
    expect(setLandingAdvOpen.mock.calls[0][0](false)).toBe(true);

    rerender(
      <I18nextProvider i18n={i18n}>
        <IdleSkeleton {...baseProps({ landingAdvOpen: true, setLandingAdvOpen })} />
      </I18nextProvider>,
    );
    expect(screen.getByRole('button', { name: i18n.t('dub.advanced') })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(screen.getByLabelText('Choose a cookies.txt export')).toBeVisible();
    expect(screen.getByRole('checkbox', { name: i18n.t('dub.pull_captions') })).toBeVisible();
    expect(screen.getByRole('spinbutton')).toBeVisible();
    expect(screen.getByPlaceholderText(i18n.t('dub.style_placeholder'))).toBeVisible();
  });

  it('keeps source-language selection available after choosing a local file', () => {
    const setDubSourceLangCode = vi.fn();
    renderIdle({
      dubVideoFile: new File(['video'], 'thai.mp4', { type: 'video/mp4' }),
      setDubSourceLangCode,
    });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('dub.source_language') }));
    fireEvent.mouseDown(screen.getByRole('option', { name: /Thai.*th/ }));
    expect(setDubSourceLangCode).toHaveBeenCalledWith('th');
  });

  it('localizes source-language option names for the active UI locale', () => {
    renderIdle({
      dubVideoFile: new File(['video'], 'thai.mp4', { type: 'video/mp4' }),
      uiLocale: 'fr',
    });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('dub.source_language') }));
    const thai = new Intl.DisplayNames(['fr', 'en'], { type: 'language' }).of('th');
    expect(screen.getByRole('option', { name: `${thai} — th` })).toBeInTheDocument();
  });

  it('clears the native cookie picker when the selection is removed', () => {
    const selected = new File(['# Netscape HTTP Cookie File\n'], 'cookies.txt', {
      type: 'text/plain',
    });
    const { rerender } = renderIdle({ youtubeCookieFile: selected, landingAdvOpen: true });
    const input = screen.getByLabelText('Choose a cookies.txt export');
    fireEvent.change(input, { target: { files: [selected] } });
    expect(input.files).toHaveLength(1);

    rerender(
      <I18nextProvider i18n={i18n}>
        <IdleSkeleton {...baseProps({ youtubeCookieFile: null, landingAdvOpen: true })} />
      </I18nextProvider>,
    );
    expect(input.value).toBe('');
  });

  it('preserves the native cookie selection while Advanced is closed and reopened', () => {
    const selected = new File(['# Netscape HTTP Cookie File\n'], 'cookies.txt', {
      type: 'text/plain',
    });
    const { rerender } = renderIdle({ youtubeCookieFile: selected, landingAdvOpen: true });
    const input = screen.getByLabelText('Choose a cookies.txt export');
    fireEvent.change(input, { target: { files: [selected] } });

    rerender(
      <I18nextProvider i18n={i18n}>
        <IdleSkeleton {...baseProps({ youtubeCookieFile: selected, landingAdvOpen: false })} />
      </I18nextProvider>,
    );
    expect(screen.getByLabelText('Choose a cookies.txt export')).toBe(input);
    expect(input).not.toBeVisible();
    expect(input.files).toHaveLength(1);

    rerender(
      <I18nextProvider i18n={i18n}>
        <IdleSkeleton {...baseProps({ youtubeCookieFile: selected, landingAdvOpen: true })} />
      </I18nextProvider>,
    );
    expect(screen.getByLabelText('Choose a cookies.txt export')).toBe(input);
    expect(input).toBeVisible();
    expect(input.files).toHaveLength(1);
  });

  it('does NOT show the idle dropzone while transcribing a URL-ingested job', () => {
    const { container } = renderIdle({ dubStep: 'transcribing', dubJobId: 'job-url-1' });
    // The desync: dropzone + paste-URL input must be gone during transcribe.
    expect(container.querySelector('.dub-idle-drop')).toBeNull();
    expect(screen.queryByText(DROP_HINT)).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(URL_PLACEHOLDER)).not.toBeInTheDocument();
    // …and the transcribe progress view is shown instead.
    expect(screen.getByText(TRANSCRIBING)).toBeInTheDocument();
  });

  it('does NOT show the idle dropzone while preparing a URL-ingested job', () => {
    const { container } = renderIdle({
      dubStep: 'uploading',
      dubJobId: 'job-url-2',
      dubPrepStage: 'download',
    });
    expect(container.querySelector('.dub-idle-drop')).toBeNull();
    expect(screen.queryByPlaceholderText(URL_PLACEHOLDER)).not.toBeInTheDocument();
  });

  it('keeps both install and retry actions beside a missing-ASR error', () => {
    const install = vi.fn();
    const retry = vi.fn();
    renderIdle({
      dubStep: 'idle',
      dubJobId: 'job-needs-asr',
      dubError: 'No speech-to-text model is installed.',
      asrInstall: {
        phase: 'missing',
        repoId: 'Systran/faster-whisper-large-v3',
        label: 'Whisper large-v3',
        sizeGb: 2.9,
      },
      handleInstallMissingAsr: install,
      handleDubRetryTranscribe: retry,
    });
    fireEvent.click(screen.getByRole('button', { name: /Download Whisper large-v3/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Retry transcription' }));
    expect(install).toHaveBeenCalledOnce();
    expect(retry).toHaveBeenCalledOnce();
  });

  it('shows inline model progress instead of the upload form while installing ASR', () => {
    const { container } = renderIdle({
      dubStep: 'installing-asr',
      dubJobId: 'job-installing-asr',
      asrInstall: { phase: 'installing', label: 'Whisper large-v3', percent: 42 },
    });
    expect(container.querySelector('.dub-idle-drop')).toBeNull();
    expect(screen.getByRole('status')).toHaveTextContent('Installing Whisper large-v3…');
    expect(screen.getByText('42%')).toBeInTheDocument();
  });

  it('never falls back to the dropzone for a non-idle no-file step (e.g. stopping)', () => {
    const { container } = renderIdle({ dubStep: 'stopping', dubJobId: 'job-url-3' });
    expect(container.querySelector('.dub-idle-drop')).toBeNull();
    expect(screen.queryByPlaceholderText(URL_PLACEHOLDER)).not.toBeInTheDocument();
  });

  it('offers the batch-queue entry point from the idle landing without triggering the file picker', () => {
    // Regression: BatchQueue (and its watch folder) had NO entry point
    // anywhere in the UI — App.jsx switched on a mode nothing ever set.
    const onOpenQueue = vi.fn();
    const setDubVideoFile = vi.fn();
    renderIdle({ dubStep: 'idle', dubJobId: null, onOpenQueue, setDubVideoFile });

    fireEvent.click(screen.getByTestId('dub-open-batch-queue'));
    expect(onOpenQueue).toHaveBeenCalledOnce();
    expect(setDubVideoFile).not.toHaveBeenCalled();
  });
});
