import { describe, it, expect } from 'vitest';
import { createUpdaterSlice } from './updaterSlice';

function harness() {
  let state: any = {};
  const set = (p: any) => {
    state = { ...state, ...(typeof p === 'function' ? p(state) : p) };
  };
  state = createUpdaterSlice(set as any, (() => state) as any, {} as any);
  return { get: () => state };
}

describe('updaterSlice', () => {
  it('starts idle', () => {
    const { get } = harness();
    expect(get().updateStatus).toBe('idle');
    expect(get().updateProgress).toBe(0);
  });

  it('available carries version + notes', () => {
    const { get } = harness();
    get().setUpdateAvailable('0.3.1', 'release notes');
    expect(get().updateStatus).toBe('available');
    expect(get().updateVersion).toBe('0.3.1');
    expect(get().updateNotes).toBe('release notes');
  });

  it('progress sets downloading and clamps 0–100', () => {
    const { get } = harness();
    get().setUpdateProgress(150);
    expect(get().updateStatus).toBe('downloading');
    expect(get().updateProgress).toBe(100);
    get().setUpdateProgress(-5);
    expect(get().updateProgress).toBe(0);
  });

  it('ready pins 100; error sets status + message', () => {
    const { get } = harness();
    get().setUpdateReady();
    expect(get().updateStatus).toBe('ready');
    expect(get().updateProgress).toBe(100);
    get().setUpdateError('boom');
    expect(get().updateStatus).toBe('error');
    expect(get().updateError).toBe('boom');
  });

  it('checking clears error; idle resets progress', () => {
    const { get } = harness();
    get().setUpdateError('x');
    get().setUpdateChecking();
    expect(get().updateStatus).toBe('checking');
    expect(get().updateError).toBeNull();
    get().setUpdateIdle();
    expect(get().updateStatus).toBe('idle');
    expect(get().updateProgress).toBe(0);
  });

  it('dismiss clears the error pill back to idle', () => {
    const { get } = harness();
    get().setUpdateError('boom');
    expect(get().updateStatus).toBe('error');
    get().dismissUpdate();
    expect(get().updateStatus).toBe('idle');
    expect(get().updateError).toBeNull();
    expect(get().updateProgress).toBe(0);
  });

  it('tracks app version', () => {
    const { get } = harness();
    expect(get().appVersion).toBeNull();
    get().setAppVersion('0.3.0');
    expect(get().appVersion).toBe('0.3.0');
  });

  it('holds the update channel, defaulting to stable', () => {
    const { get } = harness();
    expect(get().updateChannel).toBe('stable');
    get().setUpdateChannelValue('preview');
    expect(get().updateChannel).toBe('preview');
    get().setUpdateChannelValue('bogus'); // normalized
    expect(get().updateChannel).toBe('stable');
  });
});
