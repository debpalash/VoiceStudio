import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { requestDictationCapture, toast } = vi.hoisted(() => ({
  requestDictationCapture: vi.fn(),
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock('../utils/dictationCapture', () => ({ requestDictationCapture }));
vi.mock('../utils/copyText', () => ({ copyText: vi.fn().mockResolvedValue(true) }));
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

import TranscriptionsPage, { addTranscription } from './Transcriptions';

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
