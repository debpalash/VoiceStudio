// Decoupled far-end (playback) reference bus for dictate-over-playback AEC
// (parity Action 8). The audio player publishes Float32 mono frames of what it
// is currently playing; the dictation capture subscribes and forwards them to
// the backend as the echo reference. A singleton bus means the player and the
// capture widget never need to know about each other, and when nothing is
// subscribed (the common case) publishing is a cheap no-op.

const subscribers = new Set();

/** Publish one Float32 mono frame of current playback. No-op with no listeners. */
export function publishFarEnd(frame) {
  if (subscribers.size === 0) return;
  for (const cb of subscribers) {
    try {
      cb(frame);
    } catch {
      /* a bad subscriber must not break playback */
    }
  }
}

/** Subscribe to far-end frames. Returns an unsubscribe function. */
export function subscribeFarEnd(cb) {
  subscribers.add(cb);
  return () => {
    subscribers.delete(cb);
  };
}

/** Number of active subscribers — lets the player skip tapping when nobody listens. */
export function farEndListenerCount() {
  return subscribers.size;
}
