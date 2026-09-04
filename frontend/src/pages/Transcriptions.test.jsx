import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { requestDictationCapture, toast } = vi.hoisted(() => ({
  requestDictationCapture: vi.fn(),
  toast: { error: vi.fn(), success: vi.fn() },
}));

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
    localStorage.clear();
    requestDictationCapture.mockReset().mockResolvedValue(undefined);
    toast.error.mockReset();
  });

  it('shows the effective shortcut and starts the shared recorder from the empty state', async () => {
    render(<TranscriptionsPage />);
    expect(screen.getByText(/Super\+Shift\+V/)).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: 'Start dictation' }).at(-1));
    await waitFor(() => expect(requestDictationCapture).toHaveBeenCalledWith('start'));
  });

  it('reports a capture-controller failure', async () => {
    requestDictationCapture.mockRejectedValueOnce(new Error('event channel unavailable'));
    render(<TranscriptionsPage />);
    fireEvent.click(screen.getAllByRole('button', { name: 'Start dictation' }).at(-1));

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

    expect(screen.getAllByRole('button', { name: 'Start dictation' })).toHaveLength(2);
    expect(screen.getByText('No transcriptions yet')).toBeInTheDocument();
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
  });
});
