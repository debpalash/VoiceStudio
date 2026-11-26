import { describe, expect, it } from 'vitest';
import { isProPlan, planLook, storageSize } from './account-format';

describe('storageSize', () => {
  it('formats sizes as KB, MB or GB', () => {
    expect(storageSize(0)).toBe('0 KB');
    expect(storageSize(12 * 1024)).toBe('12 KB');
    expect(storageSize(820 * 1024 ** 2)).toBe('820.0 MB');
    expect(storageSize(1.4 * 1024 ** 3)).toBe('1.4 GB');
  });

  it('moves to the next unit when rounding reaches 1024', () => {
    expect(storageSize(1024 ** 2 - 100)).toBe('1.0 MB'); // 1023.9 KB, was "1024 KB"
    expect(storageSize(1024 ** 3 - 20 * 1024)).toBe('1.0 GB'); // 1023.98 MB, was "1024.0 MB"
    expect(storageSize(1023 * 1024)).toBe('1023 KB');
    expect(storageSize(1023.9 * 1024 ** 2)).toBe('1023.9 MB');
  });
});

describe('isProPlan', () => {
  const plan = (over: Record<string, unknown>) =>
    ({
      planCode: 'free',
      planName: 'Free',
      planOrigin: 'default',
      planEndsAt: null,
      storageUsedBytes: 0,
      storageLimitBytes: 0,
      settingsSync: null,
      proDesktop: null,
      ...over,
    }) as Parameters<typeof isProPlan>[0];

  it('trusts pro.desktop when the entitlements were read', () => {
    expect(isProPlan(plan({ proDesktop: true }))).toBe(true);
    expect(isProPlan(plan({ planCode: 'pro_lifetime', proDesktop: false }))).toBe(false);
  });

  it('recognises Pro and lifetime plan codes when entitlements are unavailable', () => {
    expect(isProPlan(plan({ planCode: 'pro_lifetime', planOrigin: 'purchase' }))).toBe(true);
    expect(isProPlan(plan({ planCode: 'Pro' }))).toBe(true);
    expect(isProPlan(plan({ planCode: 'lifetime' }))).toBe(true);
  });

  it('keeps Free, other paid plans and signed-out accounts non-Pro', () => {
    expect(isProPlan(plan({}))).toBe(false);
    expect(isProPlan(plan({ planCode: 'cloud_starter', planOrigin: 'purchase' }))).toBe(false);
    expect(isProPlan(plan({ planCode: 'prototype' }))).toBe(false);
    expect(isProPlan(null)).toBe(false);
    expect(planLook(null).pro).toBe(false);
  });
});
