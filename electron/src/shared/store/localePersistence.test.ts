import { beforeEach, describe, expect, it } from 'vitest';

import { discardPendingWrites } from '../utils/coalescedJsonStorage';
import { discardLongformPendingWrites } from '../utils/longformPersistence';
import { APP_STORE_KEY, useAppStore } from './index';

describe('locale persistence', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState(useAppStore.getInitialState(), true);
    discardLongformPendingWrites();
    discardPendingWrites((key) => key === APP_STORE_KEY);
  });

  it('restores the selected locale after rehydration', async () => {
    localStorage.setItem(
      APP_STORE_KEY,
      JSON.stringify({ state: { locale: 'zh-CN', localeChosen: true }, version: 8 }),
    );
    await useAppStore.persist.rehydrate();

    expect(useAppStore.getState().locale).toBe('zh-CN');
  });
});
