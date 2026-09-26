import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  configurePersistenceRole,
  discardPendingWrites,
  flushPendingWrites,
  installPersistenceLifecycleFlush,
  resetCoalescedJsonStorageForTests,
} from '../utils/coalescedJsonStorage';
import {
  configureLongformDurableStoreForTests,
  discardLongformPendingWrites,
  flushLongformPendingWrites,
  type DurableLongformRecord,
  type LongformDurableStore,
} from '../utils/longformPersistence';
import { APP_STORE_KEY, useAppStore } from './index';

describe('app-store persistence scheduling', () => {
  let durableRecord: DurableLongformRecord | null;

  beforeEach(async () => {
    vi.useFakeTimers();
    durableRecord = null;
    const durableStore: LongformDurableStore = {
      read: vi.fn(async () => durableRecord),
      write: vi.fn(async (record) => {
        durableRecord = structuredClone(record);
      }),
      clear: vi.fn(async () => {
        durableRecord = null;
      }),
    };
    configureLongformDurableStoreForTests(durableStore);
    localStorage.clear();
    useAppStore.setState(useAppStore.getInitialState(), true);
    await flushLongformPendingWrites();
    flushPendingWrites();
    localStorage.clear();
    discardLongformPendingWrites();
    discardPendingWrites((key) => key === APP_STORE_KEY);
  });

  afterEach(() => {
    discardLongformPendingWrites();
    discardPendingWrites((key) => key === APP_STORE_KEY);
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('coalesces 100 rapid transient and persisted updates into the latest v9 envelope', async () => {
    const setItem = vi.spyOn(localStorage, 'setItem');
    let latestScale = 1;

    for (let index = 0; index < 100; index += 1) {
      latestScale = 1 + index / 1_000;
      useAppStore.setState({
        uiScale: latestScale,
        uiScalePreviewed: index % 2 === 0,
      });
    }

    expect(setItem).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(249);
    expect(setItem).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    const targetWrites = setItem.mock.calls.filter(([key]) => key === APP_STORE_KEY);
    expect(targetWrites).toHaveLength(1);

    const envelope = JSON.parse(targetWrites[0][1]);
    expect(envelope).toMatchObject({
      version: 9,
      state: { uiScale: latestScale },
    });
    expect(envelope.state).not.toHaveProperty('uiScalePreviewed');
  });

  it('cancels a pending write when persist.clearStorage removes the key', async () => {
    localStorage.setItem(APP_STORE_KEY, JSON.stringify({ state: { uiScale: 1 }, version: 7 }));
    const setItem = vi.spyOn(localStorage, 'setItem');

    useAppStore.setState({ uiScale: 1.25 });
    expect(setItem).not.toHaveBeenCalled();

    useAppStore.persist.clearStorage();
    await vi.waitFor(() => expect(localStorage.getItem(APP_STORE_KEY)).toBeNull());
    const writesAfterClear = setItem.mock.calls.filter(([key]) => key === APP_STORE_KEY).length;
    const cleanupLifecycle = installPersistenceLifecycleFlush();
    window.dispatchEvent(new Event('pagehide'));
    expect(flushPendingWrites().attempted).toBe(0);

    await vi.runAllTimersAsync();
    expect(localStorage.getItem(APP_STORE_KEY)).toBeNull();
    expect(setItem.mock.calls.filter(([key]) => key === APP_STORE_KEY)).toHaveLength(
      writesAfterClear,
    );
    cleanupLifecycle();
  });

  it('keeps store updates and persist.clearStorage read-only in the standalone widget role', () => {
    localStorage.setItem(APP_STORE_KEY, JSON.stringify({ state: { uiScale: 1 }, version: 7 }));
    resetCoalescedJsonStorageForTests();
    configurePersistenceRole('readonly');
    const setItem = vi.spyOn(localStorage, 'setItem');
    const removeItem = vi.spyOn(localStorage, 'removeItem');

    useAppStore.setState({ uiScale: 1.25 });
    useAppStore.persist.clearStorage();
    vi.runAllTimers();
    flushPendingWrites();

    expect(setItem).not.toHaveBeenCalled();
    expect(removeItem).not.toHaveBeenCalled();
    expect(JSON.parse(localStorage.getItem(APP_STORE_KEY) ?? 'null')).toEqual({
      state: { uiScale: 1 },
      version: 7,
    });
  });

  it('round-trips the documented long-form projection without runtime track fields', async () => {
    const runtimeTrack = {
      id: 7,
      character: 'narrator',
      text: 'Chapter one',
      profileId: 'profile-narrator',
      emotion: 'calm',
      speed: 0.95,
      generating: true,
      audioUrl: 'blob:stale-preview',
    };

    const state = useAppStore.getState();
    state.setStoryTracks([runtimeTrack]);
    state.setCast([
      {
        id: 'narrator',
        name: 'Narrator',
        color: '#fabd2f',
        profileId: 'profile-narrator',
      },
    ]);
    state.setScript('# Chapter one\nLong-form body');
    state.setProjectMeta({ title: 'A Book', author: 'A Writer' });
    state.setLexicon({ OmniVoice: 'omni voice' });
    state.setVoiceCast('Narrator', 'profile-narrator');
    state.setCoverRef({ filename: 'cover.png', serverPath: '/covers/cover.png' });
    state.setOutputPrefs({
      outputFormat: 'mp3',
      loudness: 'podcast',
      defaultVoice: 'profile-narrator',
      language: 'English',
    });
    state.setLastOutputSnapshot('a-book.mp3', '# Rendered script', [
      { title: 'Chapter one', status: 'done', duration_s: 4 },
    ]);
    state.convertMode('audiobook');

    await vi.advanceTimersByTimeAsync(250);
    await flushLongformPendingWrites();
    flushPendingWrites();
    const envelope = JSON.parse(localStorage.getItem(APP_STORE_KEY) ?? 'null');
    expect(envelope.version).toBe(9);
    expect(envelope.state).not.toHaveProperty('storyTracks');
    expect(envelope.state).not.toHaveProperty('storyProjects');
    expect(durableRecord?.schema).toBe(1);
    expect({ ...envelope.state, ...durableRecord!.payload }).toMatchObject({
      storyTracks: [
        {
          id: 7,
          character: 'narrator',
          text: 'Chapter one',
          profileId: 'profile-narrator',
          emotion: 'calm',
          speed: 0.95,
        },
      ],
      cast: [
        {
          id: 'narrator',
          name: 'Narrator',
          color: '#fabd2f',
          profileId: 'profile-narrator',
        },
      ],
      script: '# Chapter one\nLong-form body',
      meta: { title: 'A Book', author: 'A Writer' },
      lexicon: { OmniVoice: 'omni voice' },
      voiceCast: { Narrator: 'profile-narrator' },
      coverRef: { filename: 'cover.png', serverPath: '/covers/cover.png' },
      outputFormat: 'mp3',
      loudness: 'podcast',
      defaultVoice: 'profile-narrator',
      language: 'English',
      lastOutput: 'a-book.mp3',
      lastOutputScript: '# Rendered script',
      lastOutputChapters: [{ title: 'Chapter one', status: 'done', duration_s: 4 }],
      projectMode: 'audiobook',
    });
    expect((durableRecord!.payload.storyTracks as any[])[0]).not.toHaveProperty('generating');
    expect((durableRecord!.payload.storyTracks as any[])[0]).not.toHaveProperty('audioUrl');

    useAppStore.setState({
      storyTracks: [],
      cast: [],
      script: '',
      meta: {},
      lexicon: {},
      voiceCast: {},
      coverRef: null,
      outputFormat: 'm4b',
      loudness: 'off',
      defaultVoice: null,
      language: 'Auto',
      lastOutput: '',
      lastOutputScript: '',
      lastOutputChapters: [],
      projectMode: 'stories',
    });
    discardLongformPendingWrites();
    discardPendingWrites((key) => key === APP_STORE_KEY);

    await useAppStore.persist.rehydrate();
    expect(useAppStore.getState()).toMatchObject({
      ...envelope.state,
      ...durableRecord!.payload,
    });
    expect(useAppStore.getState().storyTracks[0]).not.toHaveProperty('generating');
    expect(useAppStore.getState().storyTracks[0]).not.toHaveProperty('audioUrl');
  });

  it('keeps multi-megabyte long-form content out of the localStorage envelope', async () => {
    const largeScript = 'Long-form chapter content. '.repeat(240_000);
    const state = useAppStore.getState();
    state.setScript(largeScript);
    state.saveProject('Large audiobook');

    await vi.advanceTimersByTimeAsync(250);
    await flushLongformPendingWrites();
    flushPendingWrites();

    const rawEnvelope = localStorage.getItem(APP_STORE_KEY) ?? '';
    const envelope = JSON.parse(rawEnvelope);
    expect(rawEnvelope.length).toBeLessThan(100_000);
    expect(envelope.state).not.toHaveProperty('script');
    expect(envelope.state).not.toHaveProperty('storyProjects');
    expect(durableRecord?.payload.script).toBe(largeScript);
    expect(durableRecord).not.toBeNull();
    expect((durableRecord!.payload.storyProjects as any[])[0].script).toBe(largeScript);
  });
});
