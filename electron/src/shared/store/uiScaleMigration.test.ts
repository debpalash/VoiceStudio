import { beforeEach, describe, expect, it } from 'vitest';
import { discardPendingWrites } from '../utils/coalescedJsonStorage';
import { discardLongformPendingWrites } from '../utils/longformPersistence';
import { APP_STORE_KEY, useAppStore } from './index';

const persist = async (state: object, version: number) => {
  // The test deliberately replaces durable storage with an old fixture. A
  // pending current-state write must not mask that fixture during rehydrate.
  discardLongformPendingWrites();
  discardPendingWrites((key) => key === APP_STORE_KEY);
  localStorage.setItem(APP_STORE_KEY, JSON.stringify({ state, version }));
  await useAppStore.persist.rehydrate();
};

describe('UI scale persistence migration', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ uiScale: 1, uiScaleConfigured: false });
  });

  it.each([1, 1.2])('does not re-onboard an existing v6 install at scale %s', async (uiScale) => {
    await persist({ uiScale }, 6);
    expect(useAppStore.getState()).toMatchObject({ uiScale, uiScaleConfigured: true });
  });

  it('preserves the unconfigured state for a new current-schema install', async () => {
    await persist({ uiScale: 1, uiScaleConfigured: false }, 7);
    expect(useAppStore.getState().uiScaleConfigured).toBe(false);
  });
});
