import { existsSync } from 'node:fs';
import { join } from 'node:path';
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
export type UpdateUnsupportedReason =
  | 'development'
  | 'store'
  | 'package-manager'
  | 'no-update-config';

/**
 * electron-updater reads `app-update.yml` from the app's resources. Release
 * installers carry it; a custom or unpackaged build may not, and every update
 * check then fails with a raw ENOENT (#3814). Without a resources path (tests,
 * dev runs) there is nothing to check, so the file counts as present.
 */
export function updateConfigPresent(
  resourcesPath: string | undefined = process.resourcesPath,
): boolean {
  return !resourcesPath || existsSync(join(resourcesPath, 'app-update.yml'));
}

export function applicationUpdatesUnsupportedReason(
  packaged: boolean,
  target: DistributionTarget = CURRENT_DISTRIBUTION,
  disabledByEnvironment = process.env.VOICESTUDIO_DISABLE_UPDATER === '1',
  hasUpdateConfig: boolean | (() => boolean) = updateConfigPresent,
): UpdateUnsupportedReason | null {
  if (!packaged) return 'development';
  if (DISTRIBUTION_PROFILES[target].applicationUpdates !== 'application') return 'store';
  if (disabledByEnvironment) return 'package-manager';
  if (!(typeof hasUpdateConfig === 'function' ? hasUpdateConfig() : hasUpdateConfig))
    return 'no-update-config';
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
