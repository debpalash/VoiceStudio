/**
 * Synced-lyrics player: the word under `audio.currentTime` is highlighted and
 * advances with playback (mocked here by setting currentTime and firing the
 * media events — jsdom has no real media pipeline).
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import SyncedLyricsPlayer from './SyncedLyricsPlayer.jsx';

const t = (key) => key;

const SCRIPT = '# One\nAlpha beta gamma delta.\n# Two\nEpsilon zeta.';
const CHAPTERS = [
  { title: 'One', status: 'done', duration_s: 8 },
  { title: 'Two', status: 'done', duration_s: 4 },
];

function renderPlayer(props = {}) {
  const utils = render(
    <SyncedLyricsPlayer
      t={t}
      src="/audio/book.m4b"
      script={SCRIPT}
      chapters={CHAPTERS}
      {...props}
    />,
  );
  const audio = utils.container.querySelector('audio');
  const seek = (time) => {
    audio.currentTime = time;
    fireEvent.timeUpdate(audio);
  };
  const activeText = () => utils.container.querySelector('.is-active')?.textContent ?? null;
  return { ...utils, audio, seek, activeText };
}

describe('SyncedLyricsPlayer', () => {
  it('renders the chapter text with nothing highlighted before playback', () => {
    const { container, activeText } = renderPlayer();
    const words = [...container.querySelectorAll('.synced-lyrics__word')];
    expect(words.map((w) => w.textContent)).toEqual([
      'Alpha',
      'beta',
      'gamma',
      'delta.',
      'Epsilon',
      'zeta.',
    ]);
    expect(container.querySelectorAll('.synced-lyrics__title')).toHaveLength(2);
    expect(activeText()).toBeNull();
  });

  it('highlights the current word from currentTime and advances with it', () => {
    const { seek, activeText } = renderPlayer();
    seek(0.5); // chapter One is 0–8s, 4 words → 2s per word
    expect(activeText()).toBe('Alpha');
    seek(2.5);
    expect(activeText()).toBe('beta');
    seek(9); // into chapter Two (8–12s, 2 words)
    expect(activeText()).toBe('Epsilon');
  });

  it('marks words already spoken as past, across chapter boundaries', () => {
    const { container, seek } = renderPlayer();
    seek(9);
    const past = [...container.querySelectorAll('.is-past')].map((w) => w.textContent);
    expect(past).toEqual(['Alpha', 'beta', 'gamma', 'delta.']);
  });

  it('moves backwards too (a seek is just another currentTime)', () => {
    const { seek, activeText, container } = renderPlayer();
    seek(9);
    seek(0.5);
    expect(activeText()).toBe('Alpha');
    expect(container.querySelectorAll('.is-past')).toHaveLength(0);
  });

  it('clicking a word seeks the audio to that word', () => {
    const { getByText, audio } = renderPlayer();
    fireEvent.click(getByText('Epsilon'));
    expect(audio.currentTime).toBe(8);
  });

  it('keeps words out of sequential focus and exposes one seek button per chapter', () => {
    const { container, audio } = renderPlayer();
    expect(
      [...container.querySelectorAll('.synced-lyrics__word')].every((word) => word.tabIndex === -1),
    ).toBe(true);

    const chapter = screen.getByRole('button', { name: 'Two' });
    expect(chapter.tabIndex).toBe(0);
    fireEvent.click(chapter);
    expect(audio.currentTime).toBe(8);
  });

  it('falls back to an even split over the file duration when stream timings are absent', () => {
    const { audio, seek, activeText } = renderPlayer({ chapters: [] });
    expect(activeText()).toBeNull();
    Object.defineProperty(audio, 'duration', { value: 12, configurable: true });
    fireEvent.loadedMetadata(audio);
    seek(2.5); // 6 words over 12s → 2s each
    expect(activeText()).toBe('beta');
  });

  it('degrades to a bare player when there is nothing to sync', () => {
    const { container } = renderPlayer({ script: '', chapters: [] });
    expect(container.querySelector('audio')).toBeTruthy();
    expect(container.querySelector('.synced-lyrics__pane')).toBeNull();
  });

  it('runs a rAF loop only while playing (timeupdate alone is too coarse)', () => {
    const raf = vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(1);
    const caf = vi.spyOn(window, 'cancelAnimationFrame');
    const { audio } = renderPlayer();
    fireEvent.play(audio);
    expect(raf).toHaveBeenCalled();
    fireEvent.pause(audio);
    expect(caf).toHaveBeenCalled();
    raf.mockRestore();
    caf.mockRestore();
  });
});
