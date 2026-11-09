import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
type Handlers = Record<string, (() => void) | undefined> & { src: { src: string } };
const players = vi.hoisted(() => ({ mounted: [] as Handlers[], live: null as Handlers | null }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('./media-player', async () => {
  const { useEffect } = await import('react');
  return {
    audioSource: (src: string) => ({ src }),
    audioLoaders: [],
    MediaProvider: () => null,
    StudioMediaPlayer: (props: Handlers) => {
      players.live = props;
      useEffect(() => {
        players.mounted.push(props);
        return () => {
          if (players.live === props) players.live = null;
        };
      }, []);
      return null;
    },
  };
});
import { AudioPreviewButton } from './audio-preview-button';
afterEach(() => {
  cleanup();
  players.mounted = [];
  players.live = null;
});
const fire = (name: string) => act(() => players.live?.[name]?.());
it('mounts no player until the first click', () => {
  render(<AudioPreviewButton src="/a" source="s" />);
  expect(players.mounted).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'clone.preview_voice' }));
  expect(players.mounted).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'common.loading · common.cancel' })).toBeEnabled();
});
it('lets a slow load be cancelled instead of freezing the button', () => {
  render(<AudioPreviewButton src="/a" source="s" />);
  fireEvent.click(screen.getByRole('button'));
  fireEvent.click(screen.getByRole('button', { name: 'common.loading · common.cancel' }));
  expect(players.live).toBeNull();
  expect(screen.getByRole('button', { name: 'clone.preview_voice' })).toBeEnabled();
});
it('retries an errored preview with a fresh request', () => {
  render(<AudioPreviewButton src="/a" source="s" />);
  fireEvent.click(screen.getByRole('button'));
  fire('onError');
  const retry = screen.getByRole('button', { name: 'player.unavailable · common.retry' });
  expect(retry).toBeEnabled();
  fireEvent.click(retry);
  expect(players.mounted).toHaveLength(2);
  fire('onPlaying');
  expect(screen.getByRole('button', { name: 'player.pause' })).toBeInTheDocument();
});
it('falls back once before reporting an error', () => {
  render(<AudioPreviewButton src="/a" fallbackSrc="/a?local" source="s" />);
  fireEvent.click(screen.getByRole('button'));
  fire('onError');
  expect(players.live?.src.src).toBe('/a?local');
  expect(screen.getByRole('button', { name: 'common.loading · common.cancel' })).toBeVisible();
  fire('onError');
  expect(screen.getByRole('button', { name: 'player.unavailable · common.retry' })).toBeVisible();
});
it('defers onReady until playback stops so a refetch cannot cut the clip', () => {
  const onReady = vi.fn();
  render(<AudioPreviewButton src="/a" source="s" onReady={onReady} />);
  fireEvent.click(screen.getByRole('button'));
  fire('onPlaying');
  expect(onReady).not.toHaveBeenCalled();
  fire('onEnded');
  expect(onReady).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'clone.preview_voice' })).toBeVisible();
});
