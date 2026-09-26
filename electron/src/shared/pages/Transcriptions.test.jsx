import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { requestDictationCapture, copyToClipboard, toast, readiness, apiJson } = vi.hoisted(() => ({
  readiness: {
    phase: 'ready',
    check: vi.fn().mockResolvedValue(true),
    install: vi.fn(),
    select: vi.fn(),
  },
  requestDictationCapture: vi.fn(),
  copyToClipboard: vi.fn(),
  toast: { error: vi.fn(), success: vi.fn() },
  apiJson: vi.fn(),
}));

vi.mock('../hooks/useDictationReadiness', () => ({
  useDictationReadiness: () => readiness,
}));
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal()),
  apiJson: (...args) => apiJson(...args),
}));

vi.mock('../utils/copyText', () => ({ copyText: copyToClipboard }));
vi.mock('../utils/dictationCapture', () => ({ requestDictationCapture }));
vi.mock('../components/EngineQuickSwitch', () => ({ default: () => null }));
vi.mock('../hooks/useEffectiveDictationShortcut', () => ({
  useEffectiveDictationShortcut: () => ({
    info: {
      accelerator: 'Super+Shift+V',
      display: 'Super+Shift+V',
      backend: 'portal',
    },
  }),
}));
vi.mock('react-hot-toast', () => ({ toast }));

import TranscriptionsPage, { addTranscription, segTimeRange } from './Transcriptions';

describe('Transcriptions capture entry point', () => {
  beforeEach(() => {
    readiness.phase = 'ready';
    readiness.missing = null;
    localStorage.clear();
    requestDictationCapture.mockReset().mockResolvedValue(undefined);
    toast.error.mockReset();
  });

  it('shows the effective shortcut and starts the shared recorder from the empty state', async () => {
    render(<TranscriptionsPage />);
    expect(screen.getByText('Super+Shift+V', { selector: 'kbd' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Start dictation' }));
    await waitFor(() => expect(requestDictationCapture).toHaveBeenCalledWith('start'));
  });

  it('reports a capture-controller failure', async () => {
    requestDictationCapture.mockRejectedValueOnce(new Error('event channel unavailable'));
    render(<TranscriptionsPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Start dictation' }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'Could not start dictation. Check microphone access, then try again.',
      ),
    );
  });

  it('keeps the capture action available for whitespace-only searches', () => {
    render(<TranscriptionsPage />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Search transcriptions…' }), {
      target: { value: '   ' },
    });

    const button = screen.getByRole('button', { name: 'Start dictation' });
    expect(button.querySelector(':scope > svg')).toBeInTheDocument();
    expect(button.querySelector(':scope > span')).toHaveTextContent('Start dictation');
    expect(screen.getByText('No transcriptions yet')).toBeInTheDocument();
  });

  it('moves the single capture action to the header once history exists', () => {
    addTranscription({ text: 'Existing transcript.', language: 'en' });
    render(<TranscriptionsPage />);

    const button = screen.getByRole('button', { name: 'Start dictation' });
    expect(button.closest('.txn-header__right')).toBeInTheDocument();
    expect(button.querySelector(':scope > svg')).toBeInTheDocument();
    expect(button.querySelector(':scope > span')).toHaveTextContent('Start dictation');
    expect(screen.queryByText('No transcriptions yet')).not.toBeInTheDocument();
  });

  it('shows a successful transcript emitted by the shared recorder', async () => {
    render(<TranscriptionsPage />);
    act(() => {
      addTranscription({ text: 'The shared capture path works.', language: 'en' });
    });

    expect(await screen.findByText('The shared capture path works.')).toBeInTheDocument();
  });
});

// #1798: an OpenAI-compatible ASR answering in json/text format returns no
// timings, and services/asr_backend.py records that honestly as `end: null`
// rather than inventing a number. The segment list called `.toFixed()` on it
// unconditionally, which threw during render and took the whole
// Transcriptions view down — a transcript that merely lacked timings became
// one the user could not read at all.
describe('segments without timings (#1798)', () => {
  beforeEach(() => {
    readiness.phase = 'ready';
    readiness.missing = null;
    localStorage.clear();
  });

  it('renders a segment whose end is null instead of crashing the view', async () => {
    addTranscription({
      text: 'hello from an untimed backend',
      language: 'en',
      segments: [{ text: 'hello from an untimed backend', start: 0, end: null }],
    });

    render(<TranscriptionsPage />);
    fireEvent.click(await screen.findByText('hello from an untimed backend'));

    // The transcript itself must still be readable — this is the regression:
    // before the guard, the null `end` threw and nothing rendered at all.
    expect(screen.getAllByText('hello from an untimed backend').length).toBeGreaterThan(0);
  });

  it('formats what is known and never prints NaN', () => {
    expect(segTimeRange({ start: 12, end: 15.55 })).toBe('12.0s – 15.6s');
    expect(segTimeRange({ start: 0, end: null })).toBe('0.0s');
    expect(segTimeRange({ start: null, end: 4 })).toBe('4.0s');
    expect(segTimeRange({ text: 'no timings' })).toBe('');
    expect(segTimeRange(undefined)).toBe('');
    expect(segTimeRange({ start: NaN, end: Infinity })).toBe('');
    expect(segTimeRange({ start: '0', end: {} })).toBe('');
  });
});

describe('transcription clipboard', () => {
  beforeEach(() => {
    readiness.phase = 'ready';
    readiness.missing = null;
    localStorage.clear();
    toast.success.mockReset();
    toast.error.mockReset();
    copyToClipboard.mockReset();
    addTranscription({ text: 'Copy this transcript.', language: 'en' });
  });

  it.each([true, false])('reports clipboard result %s accurately', async (copied) => {
    copyToClipboard.mockResolvedValueOnce(copied);
    render(<TranscriptionsPage />);
    fireEvent.click(screen.getByText('Copy this transcript.'));
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    await waitFor(() => expect(copyToClipboard).toHaveBeenCalledWith('Copy this transcript.'));
    await waitFor(() => expect(copied ? toast.success : toast.error).toHaveBeenCalled());
    expect(copied ? toast.error : toast.success).not.toHaveBeenCalled();
  });

  it('reports an unexpected clipboard rejection', async () => {
    copyToClipboard.mockRejectedValueOnce(new Error('Clipboard unavailable'));
    render(<TranscriptionsPage />);
    fireEvent.click(screen.getByText('Copy this transcript.'));
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.success).not.toHaveBeenCalled();
  });
});

const CATALOGUE = {
  models: [
    {
      id: 'sherpa-whisper-tiny',
      repo_id: 'test/model',
      label: 'Tiny',
      tag: 'offline',
      recommended: true,
      size_gb: 0.1,
      languages: '90+ languages (auto-detect)',
      installed: false,
    },
    {
      id: 'sherpa-parakeet-tdt-v2',
      repo_id: 'test/parakeet',
      label: 'Parakeet TDT v2',
      tag: 'offline',
      size_gb: 0.66,
      languages: 'English',
      installed: false,
    },
    {
      id: 'sherpa-zipformer-en-20m',
      repo_id: 'test/zipformer',
      label: 'Zipformer Streaming EN',
      tag: 'streaming',
      size_gb: 0.044,
      languages: 'English',
      installed: true,
    },
  ],
};

it('blocks recording and lets the user pick which model to install when one is missing', async () => {
  localStorage.clear();
  readiness.phase = 'missing';
  readiness.missing = { recommended: { repo_id: 'test/model', label: 'Tiny', size_gb: 0.1 } };
  readiness.install.mockReset();
  readiness.select.mockReset();
  apiJson.mockResolvedValue(CATALOGUE);
  render(<TranscriptionsPage />);
  expect(screen.getByRole('button', { name: 'Start dictation' })).toBeDisabled();

  // Every catalogue model is offered, grouped by the accuracy/latency trade-off,
  // with languages and size — not just the recommended download.
  expect(
    await screen.findByText('Best accuracy — transcribes after you stop speaking'),
  ).toBeInTheDocument();
  expect(screen.getByText('Lowest latency — live text while you speak')).toBeInTheDocument();
  expect(screen.getAllByText('English', { selector: 'span' })).toHaveLength(2);

  fireEvent.click(screen.getByRole('button', { name: 'Download Parakeet TDT v2 (0.66 GB)' }));
  expect(readiness.install).toHaveBeenCalledWith(
    expect.objectContaining({ id: 'sherpa-parakeet-tdt-v2', repo_id: 'test/parakeet' }),
  );

  // A model already on disk is a switch, not a download.
  fireEvent.click(screen.getByRole('button', { name: 'Use Zipformer Streaming EN' }));
  expect(readiness.select).toHaveBeenCalledWith(
    expect.objectContaining({ id: 'sherpa-zipformer-en-20m' }),
  );
  expect(readiness.install).toHaveBeenCalledTimes(1);
  expect(screen.getByText('Super+Shift+V', { selector: 'kbd' })).toBeInTheDocument();
});

it('falls back to the recommended download when the catalogue cannot be read', async () => {
  localStorage.clear();
  readiness.phase = 'missing';
  readiness.missing = { recommended: { repo_id: 'test/model', label: 'Tiny', size_gb: 0.1 } };
  readiness.install.mockReset();
  apiJson.mockRejectedValue(new Error('backend restarting'));
  render(<TranscriptionsPage />);
  fireEvent.click(await screen.findByRole('button', { name: 'Download Tiny (0.1 GB)' }));
  expect(readiness.install).toHaveBeenCalledWith(
    expect.objectContaining({ repo_id: 'test/model', label: 'Tiny' }),
  );
});

it('names the model being installed in the progress bar', () => {
  localStorage.clear();
  readiness.phase = 'installing';
  readiness.percent = 40;
  readiness.missing = { recommended: { repo_id: 'test/model', label: 'Tiny', size_gb: 0.1 } };
  readiness.target = { repo_id: 'test/parakeet', label: 'Parakeet TDT v2', size_gb: 0.66 };
  render(<TranscriptionsPage />);
  expect(screen.getByRole('progressbar')).toHaveAccessibleName(/Parakeet TDT v2/);
  readiness.target = null;
  readiness.percent = undefined;
});
