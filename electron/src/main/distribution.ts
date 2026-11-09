import {
  DISTRIBUTION_PROFILES,
  type DistributionProfile,
  type DistributionTarget,
} from '../../../config/distributions.mjs';

export const CURRENT_DISTRIBUTION = __DISTRIBUTION_TARGET__ as DistributionTarget;

export function currentDistributionProfile(): DistributionProfile {
  return DISTRIBUTION_PROFILES[CURRENT_DISTRIBUTION];
}

/**
 * Why this build cannot update itself, or null when it can. A fixed enum so
 * the renderer can say the real reason without seeing paths or settings.
 */
export type UpdateUnsupportedReason = 'development' | 'store' | 'package-manager';

export function applicationUpdatesUnsupportedReason(
  packaged: boolean,
  target: DistributionTarget = CURRENT_DISTRIBUTION,
  disabledByEnvironment = process.env.VOICESTUDIO_DISABLE_UPDATER === '1',
): UpdateUnsupportedReason | null {
  if (!packaged) return 'development';
  if (DISTRIBUTION_PROFILES[target].applicationUpdates !== 'application') return 'store';
  if (disabledByEnvironment) return 'package-manager';
  return null;
}

export function applicationUpdatesSupported(
  packaged: boolean,
  target: DistributionTarget = CURRENT_DISTRIBUTION,
  disabledByEnvironment = process.env.VOICESTUDIO_DISABLE_UPDATER === '1',
): boolean {
  return applicationUpdatesUnsupportedReason(packaged, target, disabledByEnvironment) === null;
}

export function runtimeSetupSupported(target: DistributionTarget = CURRENT_DISTRIBUTION): boolean {
  return DISTRIBUTION_PROFILES[target].runtimeCode === 'user-initiated-download';
}
