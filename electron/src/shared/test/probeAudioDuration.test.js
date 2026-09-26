import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// probeAudioDuration's contract: settles with a number or null, NEVER
// rejects. Its only caller (ingestRefAudio, useTTS.js) awaits it without a
// try/catch — a rejection there is an unhandled error and the reference clip
// silently never gets set. A clip whose codec the webview can't decode (the
// media element fires 'error' — common under Tauri WebKit) must still be
// accepted, since the backend decodes it with ffmpeg. And a media element
// that never fires anything must not hang the ingest forever (#1162).

let behavior; // 'metadata' | 'error' | 'silent'
let lastAudio;

class FakeAudio {
  constructor() {
    this.duration = NaN;
    this._listeners = {};
    lastAudio = this;
  }
  addEventListener(ev, cb) {
    this._listeners[ev] = cb;
    if (behavior === 'metadata' && ev === 'loadedmetadata') {
      this.duration = 12.5;
      queueMicrotask(cb);
    }
    if (behavior === 'error' && ev === 'error') queueMicrotask(cb);
    // 'silent': never fire — only the timeout can settle the promise.
  }
  set src(_) {}
}

let probeAudioDuration;
let revoked;

const realCreate = URL.createObjectURL;
const realRevoke = URL.revokeObjectURL;

beforeEach(async () => {
  revoked = 0;
  vi.stubGlobal('Audio', FakeAudio);
  // Patch only the static methods — jsdom needs the real URL constructor.
  URL.createObjectURL = () => 'blob:probe-test';
  URL.revokeObjectURL = () => {
    revoked += 1;
  };
  ({ probeAudioDuration } = await import('../utils/format.js'));
});

afterEach(() => {
  URL.createObjectURL = realCreate;
  URL.revokeObjectURL = realRevoke;
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('probeAudioDuration', () => {
  it('returns the duration when metadata loads', async () => {
    behavior = 'metadata';
    await expect(probeAudioDuration({ name: 'ok.wav' })).resolves.toBe(12.5);
    expect(revoked).toBe(1);
  });

  it('resolves null — never rejects — when the media element errors (undecodable codec)', async () => {
    behavior = 'error';
    await expect(probeAudioDuration({ name: 'exotic-codec.wav' })).resolves.toBeNull();
    expect(revoked).toBe(1);
  });

  it('resolves null via the timeout when no event ever fires, instead of hanging (#1162)', async () => {
    behavior = 'silent';
    vi.useFakeTimers();
    const p = probeAudioDuration({ name: 'stuck.wav' });
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(p).resolves.toBeNull();
    expect(revoked).toBe(1);
  });

  it('does not double-settle or double-revoke when a late event fires after the timeout', async () => {
    behavior = 'silent';
    vi.useFakeTimers();
    const p = probeAudioDuration({ name: 'late.wav' });
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(p).resolves.toBeNull();
    expect(() => lastAudio._listeners['loadedmetadata']?.()).not.toThrow(); // straggler after settle
    expect(revoked).toBeGreaterThanOrEqual(1);
  });
});
