import { describe, expect, test, vi } from 'vitest';

import {
  DISTRIBUTION_TARGETS,
  distributionProfile,
  resolveDistributionTarget,
} from '../../../config/distributions.mjs';
import {
  applicationUpdatesSupported,
  applicationUpdatesUnsupportedReason,
  runtimeSetupSupported,
} from './distribution';

describe('distribution contract', () => {
  test('accepts only the four maintained distribution identities', () => {
    expect(DISTRIBUTION_TARGETS).toEqual([
      'direct',
      'microsoft-store',
      'snap-store',
      'mac-app-store',
    ]);
    expect(resolveDistributionTarget('')).toBe('direct');
    expect(resolveDistributionTarget(' snap-store ')).toBe('snap-store');
    expect(() => resolveDistributionTarget('unknown')).toThrow(
      'Unsupported VoiceStudio distribution',
    );
  });

  test('uses the build environment only when no target is passed', () => {
    vi.stubEnv('VOICESTUDIO_DISTRIBUTION', 'snap-store');
    try {
      expect(resolveDistributionTarget()).toBe('snap-store');
      expect(resolveDistributionTarget('direct')).toBe('direct');
    } finally {
      vi.unstubAllEnvs();
    }
  });

  test('records update ownership without treating Microsoft EXE listings as managed packages', () => {
    expect(distributionProfile('direct').applicationUpdates).toBe('application');
    expect(distributionProfile('microsoft-store').applicationUpdates).toBe('application');
    expect(distributionProfile('snap-store').applicationUpdates).toBe('store');
    expect(distributionProfile('mac-app-store').applicationUpdates).toBe('store');
  });

  test('store ownership cannot be bypassed by a runtime environment variable', () => {
    expect(applicationUpdatesSupported(true, 'direct', false)).toBe(true);
    expect(applicationUpdatesSupported(true, 'direct', true)).toBe(false);
    expect(applicationUpdatesSupported(true, 'microsoft-store', false)).toBe(true);
    expect(applicationUpdatesSupported(true, 'snap-store', false)).toBe(false);
    expect(applicationUpdatesSupported(true, 'mac-app-store', false)).toBe(false);
    expect(applicationUpdatesSupported(false, 'direct', false)).toBe(false);
  });

  test('names why updates are unsupported, development first', () => {
    expect(applicationUpdatesUnsupportedReason(true, 'direct', false)).toBeNull();
    expect(applicationUpdatesUnsupportedReason(false, 'direct', true)).toBe('development');
    expect(applicationUpdatesUnsupportedReason(false, 'snap-store', false)).toBe('development');
    expect(applicationUpdatesUnsupportedReason(true, 'snap-store', true)).toBe('store');
    expect(applicationUpdatesUnsupportedReason(true, 'mac-app-store', false)).toBe('store');
    expect(applicationUpdatesUnsupportedReason(true, 'direct', true)).toBe('package-manager');
    expect(applicationUpdatesUnsupportedReason(true, 'microsoft-store', true)).toBe(
      'package-manager',
    );
  });

  test('bundled-only distributions cannot invoke runtime setup downloads', () => {
    expect(runtimeSetupSupported('direct')).toBe(true);
    expect(runtimeSetupSupported('microsoft-store')).toBe(true);
    expect(runtimeSetupSupported('snap-store')).toBe(true);
    expect(runtimeSetupSupported('mac-app-store')).toBe(false);
  });
});
