import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ save: vi.fn(), start: vi.fn() }));
vi.mock('@/hooks/use-reference-asr-install', () => ({
  useReferenceAsrInstall: () => ({
    start: mock.start,
    installing: false,
    progress: null,
    error: null,
  }),
}));
vi.mock('@/hooks/use-profiles', () => ({
  useCreateCloneProfile: () => ({ mutateAsync: mock.save, isPending: false }),
  useProfiles: () => ({ data: [] }),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="#models">{children}</a>,
}));
vi.mock('./portrait-search', () => ({ PortraitSearch: () => null }));
vi.mock('./profile-image-editor', () => ({ ProfileImageEditor: () => null }));
vi.mock('@/components/waveform-player', () => ({ WaveformPlayer: () => null }));
import { OptionalDetails, SaveProfileForm } from './reference-panel';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('blocks direct submission while transcribing and permits saving afterward', async () => {
  const file = new File(['audio'], 'voice.wav', { type: 'audio/wav' });
  const done = vi.fn();
  mock.save.mockResolvedValue({ id: 'saved', name: 'Voice' });
  const { container, rerender } = render(
    <SaveProfileForm file={file} onDone={done} transcribing selectOnSave={false} />,
  );
  fireEvent.change(screen.getByLabelText('clone.profile_name'), { target: { value: 'Voice' } });
  await act(async () => {
    fireEvent.submit(container.querySelector('form')!);
  });
  expect(mock.save).not.toHaveBeenCalled();
  expect(done).not.toHaveBeenCalled();
  rerender(<SaveProfileForm file={file} onDone={done} transcribing={false} selectOnSave={false} />);
  fireEvent.submit(container.querySelector('form')!);
  await waitFor(() => expect(done).toHaveBeenCalledOnce());
  expect(mock.save).toHaveBeenCalledWith(
    expect.objectContaining({ name: 'Voice', refAudio: file }),
  );
});

it('opens optional details and explains a failed automatic transcription', async () => {
  const retry = vi.fn();
  render(<OptionalDetails transcription={{ state: 'failed', recommended: null, retry }} />);

  expect(await screen.findByText('transcriptions.failed')).toHaveClass('text-destructive');
  fireEvent.click(screen.getByRole('button', { name: 'referenceAsr.retry' }));
  expect(retry).toHaveBeenCalledOnce();
});

it('hands missing reference ASR to the app-operation repair flow without losing retry', () => {
  const retry = vi.fn();
  const listener = vi.fn();
  window.addEventListener('voicestudio:repair-agent-open', listener);
  render(<OptionalDetails transcription={{ state: 'unavailable', recommended: null, retry }} />);

  fireEvent.click(screen.getByRole('button', { name: 'repairAgent.fix' }));

  expect(listener).toHaveBeenCalledOnce();
  const event = listener.mock.calls[0]?.[0] as CustomEvent<{ report: string; autoFix: boolean }>;
  expect(event.detail.autoFix).toBe(true);
  expect(event.detail.report).toContain('ACTION_REQUEST: Restore local reference-audio');
  fireEvent.click(screen.getByRole('button', { name: 'referenceAsr.retry' }));
  expect(retry).toHaveBeenCalledOnce();
  window.removeEventListener('voicestudio:repair-agent-open', listener);
});

it('offers a one-click install of the recommended speech-to-text model', () => {
  const retry = vi.fn();
  const recommended = { repo_id: 'Systran/faster-whisper-large-v3', label: 'Whisper', size_gb: 3 };
  render(<OptionalDetails transcription={{ state: 'unavailable', retry, recommended }} />);

  expect(mock.start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'referenceAsr.install' }));
  expect(mock.start).toHaveBeenCalledOnce();
});
