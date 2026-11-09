import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { NowPlayingDock } from './now-playing-dock';
import { closeDock, playInDock } from '@/lib/store/now-playing';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('@/components/waveform-player', () => {
  throw new Error('Failed to fetch dynamically imported module');
});

afterEach(() => {
  cleanup();
  closeDock();
});

it('keeps the dock and shell mounted when the player chunk fails to load', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  render(
    <>
      <p>shell</p>
      <NowPlayingDock />
    </>,
  );
  act(() => playInDock({ src: '/a.wav', source: 's', title: 'Clip', kind: 'clone' }));
  expect(await screen.findByRole('region', { name: 'player.now_playing' })).toBeTruthy();
  expect(screen.getByText('shell')).toBeTruthy();
  expect(screen.queryByTestId('waveform')).toBeNull();
  expect(await screen.findByText('player.unavailable')).toBeTruthy();
});
