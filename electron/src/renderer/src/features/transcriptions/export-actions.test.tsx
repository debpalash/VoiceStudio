import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { addTranscription } from '@shared/utils/transcriptionsStore';
import { TranscriptionsPage } from './transcriptions-page';

const mocks = vi.hoisted(() => ({
  save: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  idle: { stage: 'idle', text: '', paused: false },
  job: { running: false, error: null, file: null, savedId: null },
}));
vi.mock('@/components/bridge', () => ({ getBridge: () => ({ files: { saveData: mocks.save } }) }));
vi.mock('sonner', () => ({ toast: { success: mocks.success, error: mocks.error } }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}));
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }));
vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: { ready: true }, refetch: vi.fn() }),
}));
vi.mock('@/hooks/use-engines', () => ({ useEngines: () => ({}) }));
vi.mock('@/hooks/use-dictation-selection', () => ({ useDictationSelection: () => ({}) }));
vi.mock('@/hooks/use-native-dictation', () => ({ useNativeShortcut: () => ({}) }));
vi.mock('@/features/settings/model-catalogue-query', () => ({ useModelCatalogue: () => ({}) }));
vi.mock('@/hooks/use-recording', () => ({ useRecording: () => ({ selectedInputId: '' }) }));
vi.mock('./transcription-job', () => ({
  useTranscriptionJob: () => mocks.job,
  setTranscriptionsViewing: vi.fn(),
  clearTranscriptionFailure: vi.fn(),
  cancelTranscription: vi.fn(),
  startTranscription: vi.fn(),
}));
vi.mock('./live-dictation', () => ({
  LiveDictation: class {
    subscribe = () => () => {};
    getSnapshot = () => mocks.idle;
    cancel = vi.fn();
  },
}));
vi.mock('@/components/workspace-sidebar', () => ({
  SecondarySidebar: ({ children }: { children: ReactNode }) => <aside>{children}</aside>,
}));
vi.mock('@/components/app-shell/workspace-header', () => ({
  WorkspaceHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
}));
vi.mock('@/components/virtual-list', () => ({ VirtualList: () => null }));
vi.mock('@/components/recording-inputs', () => ({ RecordingInputs: () => null }));
vi.mock('./dictation-setup', () => ({ DictationSetup: () => null }));

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  mocks.save.mockResolvedValue({ canceled: false });
});
afterEach(cleanup);

it('saves the full displayed plain text and timed SRT through native Save As', async () => {
  addTranscription({
    text: 'Original words',
    refined_text: 'Cleaned full text.',
    language: 'en',
    segments: [{ start: 0.1, end: 1.5, text: 'Original words' }],
  });
  render(<TranscriptionsPage />);
  fireEvent.click(await screen.findByRole('button', { name: 'transcriptions.export_plain' }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledOnce());
  const plain = mocks.save.mock.calls[0][0];
  expect(plain.suggestedName).toBe('transcript.txt');
  expect(new TextDecoder().decode(plain.data)).toBe('Cleaned full text.\n');

  fireEvent.click(screen.getByRole('button', { name: 'transcriptions.export_srt' }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledTimes(2));
  const subtitle = mocks.save.mock.calls[1][0];
  expect(subtitle.suggestedName).toBe('transcript.srt');
  expect(new TextDecoder().decode(subtitle.data)).toBe(
    '1\r\n00:00:00,100 --> 00:00:01,500\r\nOriginal words\r\n\r\n',
  );
});

it('explains unavailable timings and still saves plain text when captions are impossible', async () => {
  addTranscription({ text: 'Without timings', segments: [] });
  render(<TranscriptionsPage />);
  const srt = await screen.findByRole('button', { name: 'transcriptions.export_srt' });
  expect(srt).toBeDisabled();
  expect(srt).toHaveAccessibleDescription('transcriptions.timestamps_unavailable');
  fireEvent.click(srt);
  expect(mocks.save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'transcriptions.export_plain' }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledOnce());
});

it('keeps cancellation quiet and reports actual save failures without losing the transcript', async () => {
  addTranscription({ text: 'Keep me' });
  mocks.save
    .mockResolvedValueOnce({ canceled: true })
    .mockRejectedValueOnce(new Error('disk full'));
  render(<TranscriptionsPage />);
  const save = await screen.findByRole('button', { name: 'transcriptions.export_plain' });
  fireEvent.click(save);
  await waitFor(() => expect(mocks.save).toHaveBeenCalledOnce());
  expect(mocks.success).not.toHaveBeenCalled();
  expect(mocks.error).not.toHaveBeenCalled();
  fireEvent.click(save);
  await waitFor(() => expect(mocks.error).toHaveBeenCalledOnce());
  expect(screen.getByText('Keep me')).toBeVisible();
});
