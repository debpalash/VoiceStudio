/**
 * Browser autoplay-policy unlock for AudioContext.
 *
 * WaveSurfer.js (and several utils here) construct an `AudioContext` at
 * component-mount time — i.e. before any user gesture. On Linux Firefox/Chrome
 * and Android Chrome, browsers leave such a context in `"suspended"` state;
 * `decodeAudioData` then hangs → WaveSurfer's `ready` event never fires →
 * the play button stays disabled → no `/audio/` request is ever made.
 *
 * macOS Safari/Chrome are more lenient (typically auto-resume on first
 * interaction) which is why the bug only manifests cross-platform.
 *
 * Fix: monkey-patch `window.AudioContext` to track every instance ever
 * created, then resume all of them on the first user gesture (pointerdown /
 * keydown). The patch MUST install before any module constructs an
 * AudioContext — so this file is imported once at the top of main.jsx.
 *
 * Once unlocked, the document stays unlocked — we don't need to re-resume
 * on every subsequent gesture.
 */

const _tracked = new WeakSet();
const _resumeQueue = new Set();

const _patch = (Ctor) => {
  if (!Ctor || Ctor.__omnivoiceTracked) return Ctor;
  class TrackedAudioContext extends Ctor {
    constructor(...args) {
      super(...args);
      _tracked.add(this);
      // Some browsers create the context already suspended; queue a resume
      // attempt so that when the user gesture arrives, we sweep them all.
      if (this.state === 'suspended') _resumeQueue.add(this);
    }
  }
  TrackedAudioContext.__omnivoiceTracked = true;
  return TrackedAudioContext;
};

if (typeof window !== 'undefined') {
  if (window.AudioContext) window.AudioContext = _patch(window.AudioContext);
  if (window.webkitAudioContext && window.webkitAudioContext !== window.AudioContext) {
    window.webkitAudioContext = _patch(window.webkitAudioContext);
  }
}

let _unlocked = false;
export function unlockAudio() {
  if (_unlocked) return Promise.resolve();
  _unlocked = true;
  // Snapshot then clear — new contexts created post-unlock will start in
  // "running" state on their own (the document is now gesture-activated).
  const pending = Array.from(_resumeQueue);
  _resumeQueue.clear();
  return Promise.all(
    pending.map((ac) =>
      ac.state === 'suspended' ? ac.resume().catch(() => {}) : Promise.resolve(),
    ),
  ).then(() => {});
}

let _installed = false;
export function installAudioUnlock() {
  if (_installed || typeof window === 'undefined') return;
  _installed = true;
  const opts = { once: true, capture: true };
  const handler = () => {
    unlockAudio().catch(() => {});
    window.removeEventListener('pointerdown', handler, opts);
    window.removeEventListener('keydown', handler, opts);
    window.removeEventListener('touchstart', handler, opts);
  };
  // pointerdown beats click — fires earlier, so the resume completes before
  // any click handler that depends on the AudioContext running.
  window.addEventListener('pointerdown', handler, opts);
  window.addEventListener('keydown', handler, opts);
  // touchstart for mobile Safari/Chrome which sometimes don't synthesize
  // pointerdown fast enough on the very first tap.
  window.addEventListener('touchstart', handler, opts);
}

// Test-only escape hatch. Not for production use — the unlock is meant to be
// a one-shot per page load. Resetting lets unit tests exercise the unlock
// path repeatedly against the same module instance.
export function __resetForTesting() {
  _unlocked = false;
  _installed = false;
  _resumeQueue.clear();
  // Clear any listeners installAudioUnlock may have wired (capture-phase,
  // once: true). removeEventListener is a no-op if the listener isn't
  // registered, so call unconditionally — there's no way to reach the
  // handler reference from outside, so we accept that already-fired listeners
  // are gone (which is what `once:true` guarantees anyway).
}
