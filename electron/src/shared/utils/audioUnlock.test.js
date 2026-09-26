import { describe, it, expect, beforeAll, beforeEach } from 'vitest';

// Regression guard for the AudioContext autoplay-policy fix.
// On Linux Firefox/Chrome and Android Chrome, AudioContexts created before
// a user gesture stay suspended — decodeAudioData hangs, WaveSurfer's `ready`
// never fires, play button stays disabled. The fix patches `window.AudioContext`
// to track every instance and exposes unlockAudio() to resume them.
//
// Test strategy: install a fake AudioContext BEFORE importing audioUnlock.js
// (the patch wraps window.AudioContext at module-load time). Each test resets
// the singleton's unlocked flag so the resume path runs fresh.

class FakeAudioContext {
  constructor() {
    this.state = 'suspended';
    this.resumeCalls = 0;
    this.resumeImpl = () => {
      this.state = 'running';
      return Promise.resolve();
    };
  }
  resume() {
    this.resumeCalls += 1;
    return this.resumeImpl();
  }
  close() {
    this.state = 'closed';
    return Promise.resolve();
  }
}

let audioUnlock;
beforeAll(async () => {
  // Install fake BEFORE the module's top-level patch runs.
  window.AudioContext = FakeAudioContext;
  audioUnlock = await import('./audioUnlock.js');
});

describe('audioUnlock', () => {
  beforeEach(() => audioUnlock.__resetForTesting());

  it('AudioContexts are wrapped at construction (proves patch is applied)', () => {
    const ctx = new AudioContext();
    // The patched class adds the __omnivoiceTracked marker and extends the
    // fake (so state is 'suspended' from FakeAudioContext's constructor).
    expect(window.AudioContext.__omnivoiceTracked).toBe(true);
    expect(ctx.state).toBe('suspended');
    expect(ctx.resumeCalls).toBe(0);
  });

  it('unlockAudio() resumes all suspended tracked contexts', async () => {
    const ctx1 = new AudioContext();
    const ctx2 = new AudioContext();
    const ctx3 = new AudioContext();

    await audioUnlock.unlockAudio();

    expect(ctx1.state).toBe('running');
    expect(ctx2.state).toBe('running');
    expect(ctx3.state).toBe('running');
    expect(ctx1.resumeCalls).toBe(1);
    expect(ctx2.resumeCalls).toBe(1);
    expect(ctx3.resumeCalls).toBe(1);
  });

  it('unlockAudio() is idempotent — repeated calls do not re-resume', async () => {
    const ctx = new AudioContext();
    await audioUnlock.unlockAudio();
    expect(ctx.resumeCalls).toBe(1);

    await audioUnlock.unlockAudio();
    await audioUnlock.unlockAudio();
    await audioUnlock.unlockAudio();
    expect(ctx.resumeCalls).toBe(1);
  });

  it('contexts created AFTER unlock are not re-resumed by a second unlock', async () => {
    const before = new AudioContext();
    await audioUnlock.unlockAudio();
    expect(before.resumeCalls).toBe(1);

    const after = new AudioContext();
    await audioUnlock.unlockAudio(); // no-op now (idempotent)
    expect(after.resumeCalls).toBe(0);
  });

  it('resume() rejections are swallowed — one bad context does not block others', async () => {
    const good = new AudioContext();
    const bad = new AudioContext();
    bad.resumeImpl = () => Promise.reject(new Error('policy blocked'));

    // Should NOT throw — the catch in unlockAudio isolates failures.
    await audioUnlock.unlockAudio();

    expect(good.state).toBe('running');
    expect(bad.state).toBe('suspended'); // its resume rejected; state unchanged
  });

  it('installAudioUnlock is idempotent — repeated calls are a no-op', () => {
    // We can't directly enumerate window listeners, but the internal _installed
    // gate guarantees one-time wiring. Calling repeatedly must not throw.
    expect(() => {
      audioUnlock.installAudioUnlock();
      audioUnlock.installAudioUnlock();
      audioUnlock.installAudioUnlock();
    }).not.toThrow();
  });
});
