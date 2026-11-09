import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { NowPlayingDock, PlayInDockButton } from './now-playing-dock';
import { claimPlayback, stopActivePlayback } from '@/lib/audio/playback';
import { closeDock, nowPlayingStore, playInDock } from '@/lib/store/now-playing';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/components/waveform-player', () => ({
  WaveformPlayer: ({
    src,
    source,
    durationHint,
  }: {
    src: string;
    source: string;
    durationHint?: number;
  }) => (
    <div
      data-testid="waveform"
      data-src={src}
      data-source={source}
      data-duration={durationHint ?? ''}
    />
  ),
}));

afterEach(() => {
  cleanup();
  stopActivePlayback();
  closeDock();
});

it('plays a row clip in the bottom player and closes it', () => {
  render(
    <>
      <PlayInDockButton src="/audio/a.wav" source="take-1" title="Welcome aboard" kind="clone" />
      <NowPlayingDock />
    </>,
  );
  expect(screen.queryByRole('region', { name: 'player.now_playing' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'player.play' }));
  const dock = screen.getByRole('region', { name: 'player.now_playing' });
  expect(dock).toHaveTextContent('Welcome aboard');
  expect(screen.getByTestId('waveform')).toHaveAttribute('data-source', 'dock:take-1');
  fireEvent.click(screen.getByRole('button', { name: 'common.close' }));
  expect(nowPlayingStore.state).toBeNull();
  expect(screen.queryByRole('region', { name: 'player.now_playing' })).toBeNull();
});

it.each([
  ['design', 'nav.design'],
  ['clone', 'nav.clone'],
  ['convert', 'player.kind_convert'],
  ['audiobook', 'projects.audiobook'],
  ['story', 'projects.story'],
] as const)('labels a %s clip by its own workspace', (kind, labelKey) => {
  render(<NowPlayingDock />);
  act(() => playInDock({ src: '/audio/a.wav', source: 'take-1', title: 'A', kind }));
  const dock = screen.getByRole('region', { name: 'player.now_playing' });
  expect(dock).toHaveTextContent(labelKey);
  if (kind !== 'clone') expect(dock).not.toHaveTextContent('nav.clone');
});

it('hands the stored take length to the player before metadata loads', () => {
  render(<NowPlayingDock />);
  act(() =>
    playInDock({
      src: '/audio/a.wav',
      source: 'take-1',
      title: 'A',
      kind: 'design',
      duration: 14.64,
    }),
  );
  expect(screen.getByTestId('waveform')).toHaveAttribute('data-duration', '14.64');
});

it('truncates a long title on a word boundary and keeps the full text in the tooltip', () => {
  const title =
    "Welcome aboard. I was just a three-second clip a moment ago — now I can say anything you'd like.";
  render(<NowPlayingDock />);
  act(() => playInDock({ src: '/audio/a.wav', source: 'take-1', title, kind: 'design' }));
  const heading = screen.getByText(/^Welcome aboard\. I was just a…$/);
  expect(heading.parentElement).toHaveAttribute('title', title + ' · nav.design');
});

it('shows a take playing inline (Latest take card) on its row button, one player at a time', () => {
  render(<PlayInDockButton src="/audio/a.wav" source="take-1" title="A" kind="design" />);
  const dockStop = vi.fn();
  act(() => {
    claimPlayback(dockStop, 'dock:take-1');
  });
  expect(screen.getByRole('button', { name: 'player.pause' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  // The inline card starting the same take stops the dock's copy.
  act(() => {
    claimPlayback(() => {}, 'take-1');
  });
  expect(dockStop).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'player.pause' })).toBeInTheDocument();
});

it('restarts the same clip when played again', () => {
  render(<PlayInDockButton src="/audio/a.wav" source="take-1" title="A" kind="clone" />);
  fireEvent.click(screen.getByRole('button', { name: 'player.play' }));
  const first = nowPlayingStore.state!.nonce;
  fireEvent.click(screen.getByRole('button', { name: 'player.play' }));
  expect(nowPlayingStore.state!.nonce).toBe(first + 1);
});
