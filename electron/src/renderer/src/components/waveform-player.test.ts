import { expect, it, vi } from 'vitest';
import { displayDuration, subscribeMediaPaused, waveformUrl } from './waveform-player';

it('tracks the native media element play and pause state for its button', () => {
  const media = Object.assign(new EventTarget(), { paused: true });
  const onPaused = vi.fn();
  const unsubscribe = subscribeMediaPaused(media as HTMLMediaElement, onPaused);

  media.paused = false;
  media.dispatchEvent(new Event('playing'));
  media.paused = true;
  media.dispatchEvent(new Event('pause'));

  expect(onPaused.mock.calls.map(([paused]) => paused)).toEqual([true, false, true]);

  unsubscribe();
  media.paused = false;
  media.dispatchEvent(new Event('playing'));
  expect(onPaused).toHaveBeenCalledTimes(3);
});

it('hands WaveSurfer the exact source the media element reports, so it never swaps it', () => {
  const media = document.createElement('audio');
  for (const src of [
    '/api/audio/take%201.wav',
    'blob:http://localhost/abc',
    'http://127.0.0.1:8000/api/audio/a.wav',
  ]) {
    media.src = src;
    expect(waveformUrl(src)).toBe(media.src);
  }
});

it('shows the known clip length until the audio metadata loads', () => {
  // Before metadata, Vidstack reports 0 (or NaN): the readout must not say 0:00 for a 14 s take.
  expect(displayDuration(0, 14.64)).toBe(14.64);
  expect(displayDuration(Number.NaN, 14.64)).toBe(14.64);
  expect(displayDuration(14.7, 14.64)).toBe(14.7);
  expect(displayDuration(0)).toBe(0);
  expect(displayDuration(0, Number.NaN)).toBe(0);
});
