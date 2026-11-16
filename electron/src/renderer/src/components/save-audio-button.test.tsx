import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  saveExport: vi.fn(async () => ({ canceled: true })),
  bridge: { files: { revealPath: vi.fn() } } as object | null,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('./bridge', () => ({ getBridge: () => mocks.bridge }));
vi.mock('@/lib/export-history', () => ({ saveExport: mocks.saveExport }));

import { audioMp3Url } from '@/lib/api/client';
import { SaveAudioButton } from './save-audio-button';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mocks.bridge = { files: { revealPath: vi.fn() } };
});

it('stays enabled in the browser build, where there is no desktop bridge (#4222)', async () => {
  mocks.bridge = null;
  render(
    <SaveAudioButton
      url="/audio/ab12cd34.wav"
      mp3Url="/audio/ab12cd34.mp3"
      suggestedName="take.wav"
    />,
  );
  const button = screen.getByRole('button', { name: /clone\.download$/ });
  expect(button).toBeEnabled();
  expect(screen.getByRole('button', { name: 'clone.download_format' })).toBeEnabled();
  fireEvent.click(button);
  await waitFor(() =>
    expect(mocks.saveExport).toHaveBeenCalledWith('/audio/ab12cd34.wav', 'take.wav'),
  );
});

it('saves the WAV with one click and offers no format menu without an MP3 URL', async () => {
  render(<SaveAudioButton url="/audio/ab12cd34.wav" suggestedName="take.wav" />);
  expect(screen.queryByRole('button', { name: 'clone.download_format' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /clone\.download/ }));
  await waitFor(() =>
    expect(mocks.saveExport).toHaveBeenCalledWith('/audio/ab12cd34.wav', 'take.wav'),
  );
});

it('exports the MP3 from the format menu under a matching file name', async () => {
  render(
    <SaveAudioButton
      url="/audio/ab12cd34.wav"
      mp3Url="/audio/ab12cd34.mp3"
      suggestedName="voicestudio-take.wav"
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'clone.download_format' }));
  fireEvent.click(await screen.findByRole('button', { name: 'clone.download_mp3' }));
  await waitFor(() =>
    expect(mocks.saveExport).toHaveBeenCalledWith('/audio/ab12cd34.mp3', 'voicestudio-take.mp3'),
  );
});

it('only derives an MP3 URL for backend renders', () => {
  expect(audioMp3Url('ab12cd34.wav')).toMatch(/\/audio\/ab12cd34\.mp3$/);
  expect(audioMp3Url('../secret.wav')).toBeUndefined();
  expect(audioMp3Url('ab12cd34.flac')).toBeUndefined();
});
