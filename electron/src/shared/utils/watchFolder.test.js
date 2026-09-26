import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const invoke = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({ invoke: (...args) => invoke(...args) }));

import {
  createIngestTracker,
  entryKey,
  isVideoFile,
  openWatchSource,
  videoMimeFor,
} from './watchFolder';

const entry = (name, size = 100, mtime = 1000) => ({ name, size, mtime });

describe('isVideoFile', () => {
  it('accepts common video containers, case-insensitively', () => {
    for (const name of ['a.mp4', 'B.MOV', 'c.mkv', 'd.WebM', 'e.avi', 'f.m4v']) {
      expect(isVideoFile(name), name).toBe(true);
    }
  });

  it('rejects non-video and extension-less names', () => {
    for (const name of ['notes.txt', 'audio.wav', 'subs.srt', 'noext', '.mp4', '']) {
      expect(isVideoFile(name), name).toBe(false);
    }
  });
});

describe('entryKey — dedup identity is name+size+mtime', () => {
  it('is stable for identical entries and distinct when any part changes', () => {
    expect(entryKey(entry('a.mp4', 10, 1))).toBe(entryKey(entry('a.mp4', 10, 1)));
    const base = entryKey(entry('a.mp4', 10, 1));
    expect(entryKey(entry('b.mp4', 10, 1))).not.toBe(base);
    expect(entryKey(entry('a.mp4', 11, 1))).not.toBe(base);
    expect(entryKey(entry('a.mp4', 10, 2))).not.toBe(base);
  });
});

describe('createIngestTracker', () => {
  it('never re-ingests primed (pre-existing) files', () => {
    const tracker = createIngestTracker();
    tracker.prime([entry('old.mp4')]);
    expect(tracker.next([entry('old.mp4')])).toEqual([]);
    expect(tracker.next([entry('old.mp4')])).toEqual([]);
  });

  it('releases a new file only after two scans agree (copy-in-progress guard)', () => {
    const tracker = createIngestTracker();
    tracker.prime([]);
    expect(tracker.next([entry('new.mp4', 10, 1)])).toEqual([]); // first sighting
    expect(tracker.next([entry('new.mp4', 10, 1)])).toEqual([entry('new.mp4', 10, 1)]);
    // …and exactly once: later identical scans are deduped.
    expect(tracker.next([entry('new.mp4', 10, 1)])).toEqual([]);
    expect(tracker.next([entry('new.mp4', 10, 1)])).toEqual([]);
  });

  it('keeps waiting while a file is still growing', () => {
    const tracker = createIngestTracker();
    expect(tracker.next([entry('big.mp4', 10, 1)])).toEqual([]);
    expect(tracker.next([entry('big.mp4', 20, 2)])).toEqual([]); // changed → not stable
    expect(tracker.next([entry('big.mp4', 30, 3)])).toEqual([]);
    expect(tracker.next([entry('big.mp4', 30, 3)])).toEqual([entry('big.mp4', 30, 3)]);
  });

  it('ignores non-video files entirely', () => {
    const tracker = createIngestTracker();
    expect(tracker.next([entry('readme.txt')])).toEqual([]);
    expect(tracker.next([entry('readme.txt')])).toEqual([]);
  });

  it('a rewritten file (same name, new size/mtime) is ingested again', () => {
    const tracker = createIngestTracker();
    tracker.prime([entry('take.mp4', 10, 1)]);
    expect(tracker.next([entry('take.mp4', 99, 2)])).toEqual([]);
    expect(tracker.next([entry('take.mp4', 99, 2)])).toEqual([entry('take.mp4', 99, 2)]);
  });

  it('forgets a pending file that vanishes before settling', () => {
    const tracker = createIngestTracker();
    expect(tracker.next([entry('gone.mp4', 10, 1)])).toEqual([]);
    expect(tracker.next([])).toEqual([]); // vanished mid-copy
    // Reappears: must settle across two fresh scans again.
    expect(tracker.next([entry('gone.mp4', 10, 1)])).toEqual([]);
    expect(tracker.next([entry('gone.mp4', 10, 1)])).toEqual([entry('gone.mp4', 10, 1)]);
  });

  it('unsee() hands a released entry back so it re-releases on the next scan', () => {
    const tracker = createIngestTracker();
    tracker.next([entry('raced.mp4', 10, 1)]);
    expect(tracker.next([entry('raced.mp4', 10, 1)])).toEqual([entry('raced.mp4', 10, 1)]);
    // Released but never enqueued (e.g. paused mid-poll) → given back…
    tracker.unsee(entry('raced.mp4', 10, 1));
    // …and released again on the very next scan, exactly once.
    expect(tracker.next([entry('raced.mp4', 10, 1)])).toEqual([entry('raced.mp4', 10, 1)]);
    expect(tracker.next([entry('raced.mp4', 10, 1)])).toEqual([]);
  });

  it('retries a stable file after its asynchronous ingest fails (full re-settle)', () => {
    const tracker = createIngestTracker();
    const clip = entry('retry.mp4', 10, 1);
    expect(tracker.next([clip])).toEqual([]);
    expect(tracker.next([clip])).toEqual([clip]);
    tracker.retry(clip);
    expect(tracker.next([clip])).toEqual([]);
    expect(tracker.next([clip])).toEqual([clip]);
  });
});

describe('openWatchSource — Tauri backend', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.__TAURI_INTERNALS__ = {};
  });
  afterEach(() => {
    delete window.__TAURI_INTERNALS__;
  });

  it('returns null when the native picker is cancelled', async () => {
    invoke.mockResolvedValueOnce(null);
    expect(await openWatchSource()).toBeNull();
    expect(invoke).toHaveBeenCalledWith('watch_folder_pick');
  });

  it('returns a capability descriptor without sending file bytes through IPC', async () => {
    const token = 'f'.repeat(64);
    invoke.mockImplementation(async (cmd) => {
      if (cmd === 'watch_folder_pick') return { token, path: '/Users/me/Watched Drop' };
      if (cmd === 'watch_folder_scan') return [{ name: 'clip.mp4', size: 4, mtime: 1234 }];
      return undefined;
    });

    const source = await openWatchSource();
    expect(source.label).toBe('Watched Drop');

    const entries = await source.listEntries();
    expect(entries).toEqual([{ name: 'clip.mp4', size: 4, mtime: 1234 }]);

    const file = await source.readFile(entries[0]);
    expect(file.__voiceStudioNativeWatchUpload).toBe(true);
    expect(file.token).toBe(token);
    expect(file.name).toBe('clip.mp4');
    expect(file.size).toBe(4);
    expect(file.type).toBe('video/mp4');
    expect(file.mtime).toBe(1234);

    source.close();
    expect(invoke).toHaveBeenCalledWith('watch_folder_forget', { token });

    // Scan/forget IPC calls carry only the opaque token. File bytes are never
    // copied into the renderer; enqueueBatchJob gives the descriptor to the
    // native streaming command later.
    for (const [cmd, args] of invoke.mock.calls) {
      if (cmd === 'watch_folder_pick') continue;
      expect(JSON.stringify(args)).not.toContain('/Users/me');
      expect(JSON.stringify(args)).not.toContain('Watched Drop');
    }
    expect(invoke.mock.calls.some(([cmd]) => cmd === 'watch_folder_read')).toBe(false);
  });

  it('keeps descriptor memory constant for multi-gigabyte watched files', async () => {
    const token = 'a'.repeat(64);
    invoke.mockImplementation(async (cmd) => {
      if (cmd === 'watch_folder_pick') return { token, path: '/w/Drop' };
      return undefined;
    });

    const source = await openWatchSource();
    const size = 8 * 1024 * 1024 * 1024;
    const file = await source.readFile({ name: 'big.mp4', size, mtime: 7 });
    expect(file).toMatchObject({
      __voiceStudioNativeWatchUpload: true,
      token,
      name: 'big.mp4',
      size,
      mtime: 7,
    });
    expect(invoke.mock.calls.some(([cmd]) => cmd === 'watch_folder_read')).toBe(false);
  });
});

describe('openWatchSource — File System Access backend', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    delete window.__TAURI_INTERNALS__;
  });
  afterEach(() => {
    delete window.showDirectoryPicker;
  });

  function fakeDirectoryHandle(files) {
    return {
      name: 'Drop',
      kind: 'directory',
      async *values() {
        for (const file of files) yield { kind: 'file', getFile: async () => file };
      },
      async getFileHandle(name) {
        const file = files.find((f) => f.name === name);
        if (!file) throw new Error('NotFound');
        return { getFile: async () => file };
      },
    };
  }

  it('refuses a file whose bytes changed after the scan settled', async () => {
    const settled = new File(['settled'], 'clip.mp4', { type: 'video/mp4', lastModified: 111 });
    window.showDirectoryPicker = vi.fn(async () => fakeDirectoryHandle([settled]));

    const source = await openWatchSource();
    const [entry] = await source.listEntries();
    expect(entry).toEqual({ name: 'clip.mp4', size: settled.size, mtime: 111 });

    // Read against the settled snapshot works…
    expect(await source.readFile(entry)).toBe(settled);
    // …but a stale snapshot (the file was replaced after settling) is refused.
    await expect(source.readFile({ ...entry, size: entry.size + 5 })).rejects.toThrow(/changed/);
    await expect(source.readFile({ ...entry, mtime: 999 })).rejects.toThrow(/changed/);
  });
});

describe('openWatchSource — unsupported web context', () => {
  it('throws an identifiable watch-unsupported error where no folder access exists', async () => {
    delete window.__TAURI_INTERNALS__;
    delete window.showDirectoryPicker;
    await expect(openWatchSource()).rejects.toMatchObject({ code: 'watch-unsupported' });
  });
});

describe('videoMimeFor', () => {
  it('maps known containers and falls back to octet-stream', () => {
    expect(videoMimeFor('a.mp4')).toBe('video/mp4');
    expect(videoMimeFor('a.mov')).toBe('video/quicktime');
    expect(videoMimeFor('a.unknown')).toBe('application/octet-stream');
  });
});
