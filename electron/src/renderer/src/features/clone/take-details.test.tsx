import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({
  saveAudio: vi.fn(() => Promise.resolve({ canceled: true })),
  revealPath: vi.fn(),
  composerLanguage: 'English',
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ files: { saveAudio: mock.saveAudio, revealPath: mock.revealPath } }),
}));
vi.mock('@/components/waveform-player', () => ({ WaveformPlayer: () => <div>player</div> }));
vi.mock('@/hooks/use-profiles', () => ({ useProfiles: () => ({ data: [] }) }));
vi.mock('@/hooks/use-generate', () => ({ useGenerateClone: () => ({ isGenerating: false }) }));
vi.mock('@/lib/api/client', () => ({
  audioUrl: (path: string) => `/audio/${path}`,
  audioMp3Url: (path: string) =>
    path.endsWith('.wav') ? `/audio/${path.slice(0, -4)}.mp3` : undefined,
}));
vi.mock('@/lib/store/takes', () => ({
  reuseTake: vi.fn(),
  takeSettings: () => ({}),
}));
vi.mock('@/lib/store/clone-settings', () => ({
  setCloneSetting: vi.fn(),
  useCloneSetting: () => mock.composerLanguage,
}));
vi.mock('./save-take-profile', () => ({ SaveTakeProfile: () => <div>save profile</div> }));
vi.mock('./lock-voice', () => ({ LockVoice: () => <div>lock</div> }));

import { TakeDetails, sameLanguage } from './take-details';

it('exports a historical take through the native save dialog', async () => {
  render(
    <TakeDetails
      item={
        {
          id: 'take-42',
          text: 'Historical line',
          audio_path: 'history/take-42.wav',
          language: 'English',
        } as never
      }
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: 'clone.download' }));
  await waitFor(() =>
    expect(mock.saveAudio).toHaveBeenCalledWith({
      url: '/audio/history/take-42.wav',
      suggestedName: 'voicestudio-take-42.wav',
    }),
  );
});

it('exports a historical take as MP3 from the format menu', async () => {
  render(
    <TakeDetails
      item={
        {
          id: 'take-42',
          text: 'Historical line',
          audio_path: 'ab12cd34.wav',
          language: 'English',
        } as never
      }
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: 'clone.download_format' }));
  fireEvent.click(await screen.findByRole('button', { name: 'clone.download_mp3' }));
  await waitFor(() =>
    expect(mock.saveAudio).toHaveBeenCalledWith({
      url: '/audio/ab12cd34.mp3',
      suggestedName: 'voicestudio-take-42.mp3',
    }),
  );
});

const take = (patch: Record<string, unknown> = {}) =>
  ({
    id: 'take-7',
    text: 'Line',
    mode: 'design',
    audio_path: 'take-7.wav',
    language: 'German',
    instruct: 'middle-aged, low pitch, british accent, female, whisper',
    ...patch,
  }) as never;

it('keeps a long style on one truncated line with the full value as its tooltip', () => {
  render(<TakeDetails item={take()} />);
  const style = screen.getByTitle('middle-aged, low pitch, british accent, female, whisper');
  expect(style.tagName).toBe('DD');
  expect(style).toHaveClass('truncate', 'min-w-0');
  expect(style).not.toHaveClass('whitespace-pre-wrap');
});

it('shows the language the take used and which one new takes will use', () => {
  mock.composerLanguage = 'English';
  const { unmount } = render(<TakeDetails item={take()} />);
  const language = screen.getByTitle('clone.take_language_used');
  expect(language).toHaveTextContent('German');
  expect(language).toHaveTextContent('clone.take_language_next');
  unmount();
  mock.composerLanguage = 'German';
  render(<TakeDetails item={take()} />);
  expect(screen.getByTitle('clone.take_language_used')).not.toHaveTextContent(
    'clone.take_language_next',
  );
});

it('treats an unset take language as Auto', () => {
  expect(sameLanguage(null, 'Auto')).toBe(true);
  expect(sameLanguage('auto', 'Auto')).toBe(true);
  expect(sameLanguage('German', 'English')).toBe(false);
});
