// Unit tests for the global single-playback manager (issue #316) and its
// tracked-playback extension (global mini-player).
import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  claimPlayback,
  claimTrackedPlayback,
  stopActivePlayback,
  activePlaybackSource,
  getPlaybackTrack,
  seekActivePlayback,
  pauseActivePlayback,
  resumeActivePlayback,
  subscribePlayback,
} from './playback';

afterEach(() => {
  // Leave the singleton idle between tests.
  stopActivePlayback();
});

describe('playback manager', () => {
  it('is idle by default', () => {
    expect(activePlaybackSource()).toBeNull();
  });

  it('tracks the source of the active playback', () => {
    claimPlayback(vi.fn(), 'design-preview');
    expect(activePlaybackSource()).toBe('design-preview');
  });

  it('claiming stops the previous playback (no overlap)', () => {
    const stopA = vi.fn();
    const stopB = vi.fn();
    claimPlayback(stopA, 'a');
    claimPlayback(stopB, 'b');
    expect(stopA).toHaveBeenCalledTimes(1);
    expect(stopB).not.toHaveBeenCalled();
    expect(activePlaybackSource()).toBe('b');
  });

  it('stopActivePlayback halts the current playback and goes idle', () => {
    const stop = vi.fn();
    claimPlayback(stop, 'output');
    stopActivePlayback();
    expect(stop).toHaveBeenCalledTimes(1);
    expect(activePlaybackSource()).toBeNull();
    // Idempotent: a second stop is a no-op.
    stopActivePlayback();
    expect(stop).toHaveBeenCalledTimes(1);
  });

  it('release() clears only its own claim', () => {
    const releaseA = claimPlayback(vi.fn(), 'a');
    const stopB = vi.fn();
    claimPlayback(stopB, 'b');
    // Stale release from A must not clear (or stop) B.
    releaseA();
    expect(activePlaybackSource()).toBe('b');
    expect(stopB).not.toHaveBeenCalled();
  });

  it('release() after natural end goes idle without calling stop', () => {
    const stop = vi.fn();
    const release = claimPlayback(stop, 'output');
    release();
    expect(activePlaybackSource()).toBeNull();
    expect(stop).not.toHaveBeenCalled();
    // Safe to call twice.
    release();
    expect(activePlaybackSource()).toBeNull();
  });

  it('survives a stop callback that throws', () => {
    claimPlayback(() => {
      throw new Error('already closed');
    }, 'a');
    expect(() => stopActivePlayback()).not.toThrow();
    expect(activePlaybackSource()).toBeNull();
  });

  it('notifies subscribers on claim, stop, and release', () => {
    const listener = vi.fn();
    const unsubscribe = subscribePlayback(listener);

    const release = claimPlayback(vi.fn(), 'a');
    expect(listener).toHaveBeenCalledTimes(1);

    release();
    expect(listener).toHaveBeenCalledTimes(2);

    claimPlayback(vi.fn(), 'b');
    stopActivePlayback();
    expect(listener).toHaveBeenCalledTimes(4);

    unsubscribe();
    claimPlayback(vi.fn(), 'c');
    expect(listener).toHaveBeenCalledTimes(4);
  });
});

describe('tracked playback (global mini-player)', () => {
  it('exposes label + transport capabilities in the track snapshot', () => {
    claimTrackedPlayback({
      stop: vi.fn(),
      source: 'output',
      label: 'Generated audio',
      seek: vi.fn(),
      pause: vi.fn(),
      resume: vi.fn(),
    });
    const track = getPlaybackTrack();
    expect(track).toMatchObject({
      source: 'output',
      label: 'Generated audio',
      canSeek: true,
      canPause: true,
      paused: false,
      currentTime: 0,
      duration: 0,
      peaks: null,
    });
  });

  it('update() patches track state and notifies subscribers', () => {
    const listener = vi.fn();
    const unsubscribe = subscribePlayback(listener);
    const session = claimTrackedPlayback({ stop: vi.fn(), source: 'output' });
    listener.mockClear();
    session.update({ currentTime: 3.2, duration: 10, peaks: [0.1, 1] });
    expect(listener).toHaveBeenCalledTimes(1);
    expect(getPlaybackTrack()).toMatchObject({
      currentTime: 3.2,
      duration: 10,
      peaks: [0.1, 1],
    });
    unsubscribe();
  });

  it('a stale update() after another claim must not clobber the new owner', () => {
    const stale = claimTrackedPlayback({ stop: vi.fn(), source: 'output', label: 'old' });
    claimTrackedPlayback({ stop: vi.fn(), source: 'output', label: 'new' });
    stale.update({ currentTime: 99 });
    expect(getPlaybackTrack()).toMatchObject({ label: 'new', currentTime: 0 });
    stale.release();
    expect(getPlaybackTrack()).toMatchObject({ label: 'new' });
  });

  it('seek/pause/resume route to the active claim and no-op when idle', () => {
    const seek = vi.fn();
    const pause = vi.fn();
    const resume = vi.fn();
    claimTrackedPlayback({ stop: vi.fn(), source: 'output', seek, pause, resume });
    seekActivePlayback(4.5);
    pauseActivePlayback();
    resumeActivePlayback();
    expect(seek).toHaveBeenCalledWith(4.5);
    expect(pause).toHaveBeenCalledTimes(1);
    expect(resume).toHaveBeenCalledTimes(1);
    stopActivePlayback();
    expect(() => {
      seekActivePlayback(1);
      pauseActivePlayback();
      resumeActivePlayback();
    }).not.toThrow();
    expect(seek).toHaveBeenCalledTimes(1);
  });

  it('legacy claimPlayback claims are visible as capability-less tracks', () => {
    claimPlayback(vi.fn(), 'output');
    expect(getPlaybackTrack()).toMatchObject({
      source: 'output',
      label: null,
      canSeek: false,
      canPause: false,
    });
  });

  it('snapshot goes null on stop and on release', () => {
    const session = claimTrackedPlayback({ stop: vi.fn(), source: 'output' });
    session.release();
    expect(getPlaybackTrack()).toBeNull();
    claimTrackedPlayback({ stop: vi.fn(), source: 'output' });
    stopActivePlayback();
    expect(getPlaybackTrack()).toBeNull();
  });
});
