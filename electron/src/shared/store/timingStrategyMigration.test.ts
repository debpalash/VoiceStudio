import { beforeEach, describe, expect, it } from 'vitest';

import { discardPendingWrites } from '../utils/coalescedJsonStorage';
import { discardLongformPendingWrites } from '../utils/longformPersistence';
import { APP_STORE_KEY, useAppStore } from './index';

async function rehydrate(state: object, version: number) {
  discardLongformPendingWrites();
  discardPendingWrites((key) => key === APP_STORE_KEY);
  localStorage.setItem(APP_STORE_KEY, JSON.stringify({ state, version }));
  await useAppStore.persist.rehydrate();
}

describe('timing-strategy v8 migration', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState(useAppStore.getInitialState(), true);
    discardLongformPendingWrites();
    discardPendingWrites((key) => key === APP_STORE_KEY);
  });

  it('uses lip sync for fresh installs and old records without a timing choice', async () => {
    expect(useAppStore.getState().timingStrategy).toBe('strict_slot');

    await rehydrate({ translateQuality: 'fast' }, 7);

    expect(useAppStore.getState().timingStrategy).toBe('strict_slot');
  });

  it('keeps the historical concise behavior for pre-v4 persisted installs', async () => {
    await rehydrate({ translateQuality: 'fast' }, 3);

    expect(useAppStore.getState().timingStrategy).toBe('concise');
  });

  it('preserves every explicit timing choice from v7', async () => {
    for (const timingStrategy of ['concise', 'smart_fit', 'stretch_video', 'strict_slot']) {
      useAppStore.setState(useAppStore.getInitialState(), true);
      await rehydrate({ timingStrategy }, 7);
      expect(useAppStore.getState().timingStrategy).toBe(timingStrategy);
    }
  });
});
