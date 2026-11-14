import { describe, it, expect, vi } from 'vitest';
import { createReleasesSlice } from './releasesSlice';

function harness() {
  let state: any = {};
  const set = (p: any) => {
    state = { ...state, ...(typeof p === 'function' ? p(state) : p) };
  };
  state = createReleasesSlice(set as any, (() => state) as any, {} as any);
  return { get: () => state };
}

describe('releasesSlice', () => {
  it('starts idle/empty', () => {
    const { get } = harness();
    expect(get().releases).toEqual([]);
    expect(get().releasesStatus).toBe('idle');
  });

  it('loadReleases → loaded on success', async () => {
    const data = [
      { version: '0.3.0', name: 'v0.3.0', date: '2026-05-20', prerelease: false, notes: 'x' },
    ];
    const { get } = harness();
    await get().loadReleases('stable', () => Promise.resolve(data));
    expect(get().releasesStatus).toBe('loaded');
    expect(get().releases).toEqual(data);
  });

  it('loadReleases → error on failure (keeps app usable)', async () => {
    const { get } = harness();
    await get().loadReleases('stable', () => Promise.reject(new Error('offline')));
    expect(get().releasesStatus).toBe('error');
    expect(get().releases).toEqual([]);
  });

  it.each(['stable', 'preview', 'unknown'])(
    'the retained default adapter loads %s releases',
    async (channel) => {
      const data = [{ version: '0.3.0', notes: 'release notes' }];
      const listReleases = vi.fn(async () => data);
      vi.stubGlobal('window', { voicestudio: { updates: { listReleases } } });
      try {
        const { get } = harness();
        await get().loadReleases(channel);
        expect(listReleases).toHaveBeenCalledWith(channel === 'preview' ? 'preview' : 'stable');
        expect(get().releasesStatus).toBe('loaded');
        expect(get().releases).toEqual(data);
      } finally {
        vi.unstubAllGlobals();
      }
    },
  );
});
