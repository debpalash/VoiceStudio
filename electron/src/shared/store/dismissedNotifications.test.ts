// Dismissed system notifications — the persisted id list behind the bell /
// footer filter (prefsSlice.dismissedNotificationIds). The contract under
// test: append-only with dedupe, newest-50 cap, and level gating via
// isDismissibleNotification (info/warn hideable, errors never).
import { describe, it, expect, beforeEach } from 'vitest';
import { useAppStore } from '../store';
import { isDismissibleNotification } from '../api/hooks';

beforeEach(() => {
  useAppStore.setState({ dismissedNotificationIds: [] });
});

describe('dismissNotification', () => {
  it('records a dismissed id', () => {
    useAppStore.getState().dismissNotification('gpu-unavailable');
    expect(useAppStore.getState().dismissedNotificationIds).toEqual(['gpu-unavailable']);
  });

  it('dedupes: re-dismissing the same id keeps a single entry', () => {
    const { dismissNotification } = useAppStore.getState();
    dismissNotification('gpu-unavailable');
    dismissNotification('disk-low');
    dismissNotification('gpu-unavailable');
    expect(useAppStore.getState().dismissedNotificationIds).toEqual([
      'disk-low',
      'gpu-unavailable',
    ]);
  });

  it('caps the list at 50, aging out the oldest', () => {
    const { dismissNotification } = useAppStore.getState();
    for (let i = 0; i < 55; i += 1) dismissNotification(`last-run-crash-${i}`);
    const ids = useAppStore.getState().dismissedNotificationIds;
    expect(ids).toHaveLength(50);
    expect(ids[0]).toBe('last-run-crash-5'); // 0..4 aged out
    expect(ids.at(-1)).toBe('last-run-crash-54');
  });
});

describe('isDismissibleNotification', () => {
  it('lets info and warn notes be hidden', () => {
    expect(isDismissibleNotification({ level: 'info' })).toBe(true);
    expect(isDismissibleNotification({ level: 'warn' })).toBe(true);
  });

  it('keeps error notes visible — broken things stay on screen until fixed', () => {
    expect(isDismissibleNotification({ level: 'error' })).toBe(false);
    expect(isDismissibleNotification({})).toBe(false);
  });
});
