/**
 * Backend data for Settings → Updates (feat/safe-updates):
 * - the shipped CHANGELOG.md, parsed server-side into structured releases
 *   (`GET /api/settings/changelog`) for the "What's new" viewer;
 * - the newest pre-migration DB backup (`GET /api/settings/db-backup`) for
 *   the "your data is backed up before every update" line.
 * Both degrade to safe empties on any failure — the panel just hides them.
 */
import { apiJson } from '../api/client';

export async function fetchChangelog(limitVersions = 5) {
  try {
    const data = await apiJson(`/api/settings/changelog?limit_versions=${limitVersions}`);
    return data && data.available && Array.isArray(data.releases) ? data.releases : [];
  } catch {
    return [];
  }
}

export async function fetchBackupState() {
  try {
    const data = await apiJson('/api/settings/db-backup');
    return data && typeof data === 'object' ? data : { available: false, latest: null };
  } catch {
    return { available: false, latest: null };
  }
}
