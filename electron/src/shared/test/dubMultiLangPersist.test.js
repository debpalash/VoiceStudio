import { describe, it, expect, beforeEach } from 'vitest';
import { useAppStore } from '../store';
import { restoreProjectExtras } from '../utils/projectState';
import appSrc from '../App.jsx?raw';

// P1.4 — multi-language picks live in the dub store slice (not DubTab-local
// state) and ride the project save/load payload, so "Generate 3 dubs" setups
// survive tab switches and project reopens. Legacy payloads (saved before
// these fields existed) must default cleanly: multi-lang off/empty, and the
// in-session exportTracks left untouched.

const baseState = useAppStore.getState();

describe('dub slice — multiLangMode / multiLangs', () => {
  beforeEach(() => {
    useAppStore.setState(baseState, true);
  });

  it('defaults to off/empty', () => {
    expect(useAppStore.getState().multiLangMode).toBe(false);
    expect(useAppStore.getState().multiLangs).toEqual([]);
  });

  it('setters accept values and functional updaters (slice pattern)', () => {
    const s = useAppStore.getState();
    s.setMultiLangMode(true);
    s.setMultiLangs([{ lang: 'Bengali', code: 'bn' }]);
    expect(useAppStore.getState().multiLangMode).toBe(true);
    expect(useAppStore.getState().multiLangs).toEqual([{ lang: 'Bengali', code: 'bn' }]);
    s.setMultiLangs((prev) => [...prev, { lang: 'Spanish', code: 'es' }]);
    expect(useAppStore.getState().multiLangs).toHaveLength(2);
    s.setMultiLangMode((prev) => !prev);
    expect(useAppStore.getState().multiLangMode).toBe(false);
  });

  it('resetDubState clears the picks with the rest of the pipeline state', () => {
    const s = useAppStore.getState();
    s.setMultiLangMode(true);
    s.setMultiLangs([{ lang: 'Bengali', code: 'bn' }]);
    s.resetDubState();
    expect(useAppStore.getState().multiLangMode).toBe(false);
    expect(useAppStore.getState().multiLangs).toEqual([]);
  });
});

describe('project payload — save/load round-trip (restoreProjectExtras)', () => {
  beforeEach(() => {
    useAppStore.setState(baseState, true);
  });

  it('round-trips multiLangMode, multiLangs and exportTracks through the payload', () => {
    const s = useAppStore.getState();
    s.setMultiLangMode(true);
    s.setMultiLangs([
      { lang: 'Bengali', code: 'bn' },
      { lang: 'Spanish', code: 'es' },
    ]);
    s.setExportTracks({ original: true, bn: true, es: false });
    // Mirror App.jsx's saveProject: the store values land in state as-is.
    const cur = useAppStore.getState();
    const payload = {
      multiLangMode: cur.multiLangMode,
      multiLangs: cur.multiLangs,
      exportTracks: cur.exportTracks,
    };
    const restored = restoreProjectExtras(JSON.parse(JSON.stringify(payload)));
    expect(restored.multiLangMode).toBe(true);
    expect(restored.multiLangs).toEqual([
      { lang: 'Bengali', code: 'bn' },
      { lang: 'Spanish', code: 'es' },
    ]);
    expect(restored.exportTracks).toEqual({ original: true, bn: true, es: false });
  });

  it('legacy payload (fields absent) defaults to off/empty and leaves exportTracks alone', () => {
    const restored = restoreProjectExtras({ dubJobId: 'old', dubSegments: [] });
    expect(restored.multiLangMode).toBe(false);
    expect(restored.multiLangs).toEqual([]);
    expect(restored.exportTracks).toBeNull(); // null = don't touch the current value
  });

  it('is shape-safe: malformed picks are dropped, junk exportTracks is ignored', () => {
    const restored = restoreProjectExtras({
      multiLangMode: 'yes', // not boolean true → off
      multiLangs: [{ lang: 'Bengali', code: 'bn' }, { code: 'es' }, 'fr', null],
      exportTracks: ['original'],
    });
    expect(restored.multiLangMode).toBe(false);
    expect(restored.multiLangs).toEqual([{ lang: 'Bengali', code: 'bn' }]);
    expect(restored.exportTracks).toBeNull();
    expect(restoreProjectExtras(undefined)).toEqual({
      multiLangMode: false,
      multiLangs: [],
      exportTracks: null,
    });
  });
});

describe('App.jsx wiring guard (raw source — keeps the util honest)', () => {
  it('saveProject persists multi-language and source-language fields', () => {
    const start = appSrc.indexOf('const statePayload');
    expect(start).toBeGreaterThan(-1);
    const block = appSrc.slice(start, appSrc.indexOf('apiSaveProject', start));
    for (const key of ['multiLangMode', 'multiLangs', 'exportTracks', 'dubSourceLangCode']) {
      expect(block, `statePayload.state must include ${key}`).toContain(key);
    }
  });

  it('loadProject restores through restoreProjectExtras', () => {
    const start = appSrc.indexOf('const loadProject');
    expect(start).toBeGreaterThan(-1);
    const block = appSrc.slice(start, start + 3000);
    expect(block).toContain('restoreProjectExtras');
    expect(block).toContain('setMultiLangMode');
    expect(block).toContain('setMultiLangs');
    expect(block).toContain("setDubSourceLangCode(s.dubSourceLangCode || 'auto')");
  });

  it('resetDub clears an auto-detected language before the next upload', () => {
    const start = appSrc.indexOf('const resetDub');
    expect(start).toBeGreaterThan(-1);
    const block = appSrc.slice(start, appSrc.indexOf('// ═══ STUDIO PROJECT CRUD', start));
    expect(block).toContain("setDubSourceLangCode('auto')");
  });
});
