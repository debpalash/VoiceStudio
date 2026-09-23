import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { Profile } from '@/lib/api/types';

const mock = vi.hoisted(() => ({ json: vi.fn(), replace: vi.fn(), players: [] as string[] }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}));
vi.mock('@/lib/api/client', () => ({
  apiJson: mock.json,
  describeError: (error: Error) => error.message,
  profileAudioUrl: (id: string, audioUrl?: string | null) => audioUrl ?? `/profiles/${id}/audio`,
}));
vi.mock('@/hooks/use-profiles', () => ({
  useDeleteProfile: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useReplaceProfileAudio: () => ({ mutateAsync: mock.replace }),
}));
vi.mock('@/hooks/use-tts-readiness', () => ({ useTtsReadiness: () => null }));
vi.mock('@/lib/audio/object-url', () => ({
  createObjectUrl: () => 'blob:new-clip',
  revokeObjectUrl: vi.fn(),
}));
vi.mock('@/components/waveform-player', () => ({
  WaveformPlayer: ({ src }: { src: string }) => {
    mock.players.push(src);
    return <div data-testid="player">{src}</div>;
  },
}));
vi.mock('./reference-input', () => ({
  ReferenceSourcePicker: ({
    onAccept,
  }: {
    onAccept: (file: File, seconds: number | null) => void;
  }) => (
    <button
      type="button"
      onClick={() => onAccept(new File(['audio'], 'better.wav', { type: 'audio/wav' }), 4.2)}
    >
      pick-clip
    </button>
  ),
  ReferenceUsageNote: ({ durationSeconds }: { durationSeconds: number | null }) => (
    <p data-testid="usage-note">{durationSeconds}</p>
  ),
}));
vi.mock('./profile-consent', () => ({ ProfileConsent: () => null }));
vi.mock('./profile-preview', () => ({ ProfilePreview: () => null }));
vi.mock('./profile-usage', () => ({ ProfileUsagePanel: () => null }));
vi.mock('./language-picker', () => ({ LanguagePicker: () => null }));
vi.mock('./persona-export', () => ({ PersonaExport: () => null }));
vi.mock('./profile-image-editor', () => ({ ProfileImageEditor: () => null }));
import { EditProfile } from './edit-profile';

const base = {
  id: 'v1',
  name: 'Scarlet',
  kind: 'clone',
  ref_audio_path: 'v1.wav',
  audio_url: '/profiles/v1/audio?v=1',
  ref_text: 'old words',
  instruct: '',
  language: 'Auto',
  created_at: 1,
  is_locked: 1,
} as unknown as Profile;

function renderEditor(profile: Profile = base, onDone = vi.fn()) {
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <EditProfile profile={profile} onDone={onDone} />
    </QueryClientProvider>,
  );
  return onDone;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mock.players.length = 0;
});

it('plays the stored reference through its versioned URL', () => {
  renderEditor();
  expect(screen.getByTestId('player')).toHaveTextContent('/profiles/v1/audio?v=1');
});

it('saves a replacement clip back to the same profile with a fresh transcript', async () => {
  mock.json.mockResolvedValue({ ...base });
  mock.replace.mockResolvedValue({ ...base });
  const onDone = renderEditor();

  fireEvent.click(screen.getByRole('button', { name: /clone.replace_reference/ }));
  fireEvent.click(screen.getByText('pick-clip'));

  expect(screen.getByTestId('player')).toHaveTextContent('blob:new-clip');
  expect(screen.getByText('clone.replace_reference_hint')).toBeInTheDocument();
  // The engine-aware length note describes the new clip (#2281).
  expect(screen.getByTestId('usage-note')).toHaveTextContent('4.2');
  // The old clip's transcript no longer applies to the new one.
  expect(screen.getByLabelText('clone.transcript')).toHaveValue('');

  fireEvent.submit(screen.getByRole('button', { name: 'clone.save' }).closest('form')!);

  await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
  // One request carries the clip and the profile edits, so it is atomic.
  expect(mock.json).not.toHaveBeenCalled();
  expect(mock.replace).toHaveBeenCalledWith(
    expect.objectContaining({
      id: 'v1',
      refAudioName: 'better.wav',
      refText: '',
      fields: { name: 'Scarlet', language: 'Auto', instruct: '' },
    }),
  );
});

it('restores transcript edits made before a replacement is discarded', () => {
  renderEditor();
  const transcript = screen.getByLabelText('clone.transcript');
  fireEvent.change(transcript, { target: { value: 'corrected words' } });

  fireEvent.click(screen.getByRole('button', { name: /clone.replace_reference/ }));
  fireEvent.click(screen.getByText('pick-clip'));
  fireEvent.click(screen.getByRole('button', { name: /clone.keep_reference/ }));

  expect(screen.getByLabelText('clone.transcript')).toHaveValue('corrected words');
});

it('keeps the current reference and its transcript when the replacement is discarded', async () => {
  mock.json.mockResolvedValue({ ...base });
  const onDone = renderEditor();

  fireEvent.click(screen.getByRole('button', { name: /clone.replace_reference/ }));
  fireEvent.click(screen.getByText('pick-clip'));
  fireEvent.click(screen.getByRole('button', { name: /clone.keep_reference/ }));

  expect(screen.getByLabelText('clone.transcript')).toHaveValue('old words');
  fireEvent.submit(screen.getByRole('button', { name: 'clone.save' }).closest('form')!);
  await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
  expect(JSON.parse(mock.json.mock.calls[0][1].body).ref_text).toBe('old words');
  expect(mock.replace).not.toHaveBeenCalled();
});

it('keeps the chosen clip and saves no edits when the upload fails', async () => {
  mock.json.mockResolvedValue({ ...base });
  mock.replace.mockRejectedValue(new Error('That file could not be read as audio.'));
  const onDone = renderEditor();

  fireEvent.change(screen.getByLabelText('clone.profile_name'), { target: { value: 'Crimson' } });
  fireEvent.click(screen.getByRole('button', { name: /clone.replace_reference/ }));
  fireEvent.click(screen.getByText('pick-clip'));
  fireEvent.submit(screen.getByRole('button', { name: 'clone.save' }).closest('form')!);

  expect(await screen.findByRole('alert')).toBeInTheDocument();
  expect(onDone).not.toHaveBeenCalled();
  // The name edit was not committed separately ahead of the failed upload.
  expect(mock.json).not.toHaveBeenCalled();
  expect(screen.getByTestId('player')).toHaveTextContent('blob:new-clip');
});

it('does not offer replacement for designed voices', () => {
  renderEditor({ ...base, kind: 'design' } as Profile);
  expect(screen.queryByRole('button', { name: /clone.replace_reference/ })).toBeNull();
});
