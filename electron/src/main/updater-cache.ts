import { readFile, rm } from 'node:fs/promises';
import { basename, join } from 'node:path';

// Release artifacts are `<prefix>-<version>-<os>-<arch>.<ext>` (see
// electron-builder.config.mjs); only versions this app tags are recognised.
const ARTIFACT_VERSION = /-(\d+\.\d+\.\d+(?:-beta\.\d+)?)(?=-(?:mac|win|linux)-)/;

/** The version of a downloaded update file, or null when the name is not one of ours. */
export function pendingUpdateVersion(fileName: string): string | null {
  return ARTIFACT_VERSION.exec(fileName)?.[1] ?? null;
}

function errorCode(error: unknown): string {
  const code = (error as { code?: unknown } | null)?.code;
  return typeof code === 'string' ? code : ((error as Error | null)?.name ?? 'unknown');
}

/**
 * electron-updater keeps the last downloaded update in `<cache>/pending` and
 * never removes it once that version is installed (a few hundred MB). Remove it
 * when it holds the running version or an older one. A newer download is a
 * ready-to-install update and stays, as does anything not clearly ours. The
 * cache root (`update.zip`, `current.blockmap`) is the base for differential
 * downloads and is never touched. Never throws.
 */
export async function removeStalePendingUpdate(
  pendingDir: string,
  currentVersion: string,
  compareVersions: (left: string, right: string) => number,
): Promise<boolean> {
  let fileName: unknown;
  try {
    fileName = (
      JSON.parse(await readFile(join(pendingDir, 'update-info.json'), 'utf8')) as {
        fileName?: unknown;
      } | null
    )?.fileName;
  } catch (error) {
    if (errorCode(error) !== 'ENOENT')
      console.warn(`[updater] unreadable pending update info: ${errorCode(error)}`);
    return false;
  }
  if (typeof fileName !== 'string' || basename(fileName) !== fileName) return false;
  const version = pendingUpdateVersion(fileName);
  if (!version || compareVersions(version, currentVersion) > 0) return false;
  try {
    // The update file first: if it is still locked (Windows), the info file
    // stays so the next start retries.
    await rm(join(pendingDir, fileName), { force: true });
    await rm(pendingDir, { recursive: true, force: true });
    return true;
  } catch (error) {
    console.warn(`[updater] could not remove the installed update download: ${errorCode(error)}`);
    return false;
  }
}

interface DownloadHelperOwner {
  getOrCreateDownloadHelper?: () => Promise<{ cacheDirForPendingUpdate?: unknown }>;
}

/**
 * The `pending` directory electron-updater itself uses: its download helper
 * joins the per-OS cache base with `updaterCacheDirName` from app-update.yml.
 * Null when this electron-updater does not expose it.
 */
export async function pendingUpdateDir(updater: unknown): Promise<string | null> {
  const owner = updater as DownloadHelperOwner;
  if (typeof owner.getOrCreateDownloadHelper !== 'function') return null;
  const dir = (await owner.getOrCreateDownloadHelper()).cacheDirForPendingUpdate;
  return typeof dir === 'string' && dir ? dir : null;
}

/**
 * Startup cleanup for packaged builds; null when this electron-updater exposes
 * no download helper. Logs by error code and never rejects.
 */
export function cleanInstalledUpdateDownload(
  updater: unknown,
  currentVersion: string,
  compareVersions: (left: string, right: string) => number,
): Promise<void> | null {
  if (typeof (updater as DownloadHelperOwner).getOrCreateDownloadHelper !== 'function') return null;
  return (async () => {
    try {
      const dir = await pendingUpdateDir(updater);
      if (dir) await removeStalePendingUpdate(dir, currentVersion, compareVersions);
    } catch (error) {
      console.warn(`[updater] could not locate the update cache: ${errorCode(error)}`);
    }
  })();
}
