/**
 * playback.js — global single-playback manager (issue #316).
 *
 * Only one preview/output plays at a time across the whole app. Every
 * playback site claims the manager before (or right as) it starts audio;
 * claiming stops whatever was playing before. `claimPlayback` returns a
 * `release` function the owner calls when its audio ends naturally (or
 * fails to start), so the manager never holds a stale handle.
 *
 * UI can subscribe — or use the `usePlaybackSource` / `usePlaybackTrack`
 * hooks — to render a visible affordance while something is playing.
 *
 * Tracked playback (global mini-player): `claimTrackedPlayback` is the
 * richer registration used by utils/media.js — it carries a user-facing
 * label, transport controls (seek/pause/resume) and a live track state
 * (currentTime/duration/paused/peaks) that the owner pushes via
 * `session.update(...)` from its `timeupdate` events. GlobalAudioPlayer
 * renders off that snapshot. `claimPlayback` remains the simple wrapper for
 * sites with their own visible player UI.
 *
 * Plain module-level singleton: no React dependency in the core API, so it
 * is unit-testable without a DOM and usable from non-component code
 * (e.g. utils/media.js).
 */
import { useSyncExternalStore } from 'react';

let _current = null; // { stop, source, label, controls, track }
let _snapshot = null; // immutable copy of the active track for React consumers
const _listeners = new Set();

const notify = () => {
  for (const l of _listeners) {
    try {
      l();
    } catch {
      /* listener errors must not break playback */
    }
  }
};

// Rebuild the immutable snapshot exposed to useSyncExternalStore. A new
// object identity per change, a stable one between changes.
const publish = () => {
  _snapshot = _current
    ? {
        source: _current.source,
        label: _current.label ?? null,
        canSeek: typeof _current.controls.seek === 'function',
        canPause:
          typeof _current.controls.pause === 'function' &&
          typeof _current.controls.resume === 'function',
        paused: _current.track.paused,
        currentTime: _current.track.currentTime,
        duration: _current.track.duration,
        peaks: _current.track.peaks,
      }
    : null;
  notify();
};

/**
 * Register a new playback as the single active one, with transport metadata
 * for the global mini-player. Any previously claimed playback is stopped
 * first (its `stop` callback runs).
 *
 * @param {object} opts
 * @param {() => void} opts.stop   Halts this playback immediately.
 * @param {string} [opts.source]   Label for UI routing, e.g. 'output'.
 * @param {string} [opts.label]    User-facing "what is playing" text.
 * @param {(t: number) => void} [opts.seek]    Jump to `t` seconds.
 * @param {() => void} [opts.pause]  Pause without releasing the claim.
 * @param {() => void} [opts.resume] Resume a paused playback.
 * @returns {{ release: () => void, update: (patch: object) => void }}
 *   `release()` — call when playback ends on its own (idempotent; stale
 *   releases after another claim are no-ops). `update(patch)` — push
 *   track-state changes ({ currentTime, duration, paused, peaks }); may also
 *   carry `label` to retitle the bar mid-playback (the streaming TTS preview
 *   flips "streaming" → "generated" on completion); stale updates after
 *   another claim are no-ops.
 */
export function claimTrackedPlayback({
  stop,
  source = 'audio',
  label = null,
  seek = null,
  pause = null,
  resume = null,
} = {}) {
  stopActivePlayback();
  const entry = {
    stop,
    source,
    label,
    controls: { seek, pause, resume },
    track: { currentTime: 0, duration: 0, paused: false, peaks: null },
  };
  _current = entry;
  publish();
  return {
    release: () => {
      if (_current === entry) {
        _current = null;
        publish();
      }
    },
    update: (patch) => {
      if (_current !== entry) return; // superseded — must not clobber the new owner
      const { label: nextLabel, ...trackPatch } = patch;
      if (nextLabel !== undefined) entry.label = nextLabel;
      Object.assign(entry.track, trackPatch);
      publish();
    },
  };
}

/**
 * Register a new playback as the single active one. Any previously claimed
 * playback is stopped first (its `stop` callback runs).
 *
 * @param {() => void} stop  Halts this playback immediately (pause element /
 *                           stop buffer source / close context).
 * @param {string} source    Label for UI affordances, e.g. 'output',
 *                           'design-preview', 'history'.
 * @returns {() => void}     release() — call when playback ends on its own.
 *                           Safe to call multiple times; a stale release
 *                           (after another claim) is a no-op.
 */
export function claimPlayback(stop, source = 'audio') {
  return claimTrackedPlayback({ stop, source }).release;
}

/** Stop whatever is currently playing (no-op when idle). */
export function stopActivePlayback() {
  if (!_current) return;
  const { stop } = _current;
  _current = null; // clear first so re-entrant release() calls are no-ops
  try {
    stop();
  } catch {
    /* already-stopped handles must not throw */
  }
  publish();
}

/** Seek the active playback to `t` seconds (no-op when idle/unsupported). */
export function seekActivePlayback(t) {
  _current?.controls.seek?.(t);
}

/** Pause the active playback WITHOUT releasing its claim (no-op if unsupported). */
export function pauseActivePlayback() {
  _current?.controls.pause?.();
}

/** Resume a paused active playback (no-op if unsupported). */
export function resumeActivePlayback() {
  _current?.controls.resume?.();
}

/** Source label of the active playback, or null when idle. */
export function activePlaybackSource() {
  return _current ? _current.source : null;
}

/** Snapshot of the active tracked playback ({source,label,currentTime,…}), or null. */
export function getPlaybackTrack() {
  return _snapshot;
}

/** Subscribe to active-playback changes. Returns an unsubscribe function. */
export function subscribePlayback(listener) {
  _listeners.add(listener);
  return () => _listeners.delete(listener);
}

/** React hook: source label of the active playback (null when idle). */
export function usePlaybackSource() {
  return useSyncExternalStore(subscribePlayback, activePlaybackSource, () => null);
}

/** React hook: live track snapshot of the active playback (null when idle). */
export function usePlaybackTrack() {
  return useSyncExternalStore(subscribePlayback, getPlaybackTrack, () => null);
}
