/**
 * Pure, framework-free mappings for the status-bar update surface.
 * Kept free of React/Tauri so it is unit-testable in isolation.
 */

/**
 * Map updater state to the bar chip's presentation, or null to hide it.
 * `icon` is a stable token the component maps to a lucide icon.
 */
export function chipPresentation(status, { appVersion = null, version = null, progress = 0 } = {}) {
  switch (status) {
    case 'available':
      return { variant: 'available', label: version || '', icon: 'up' };
    case 'downloading':
      return { variant: 'downloading', label: `${Math.round(progress)}%`, icon: 'spin' };
    case 'ready':
      return { variant: 'ready', label: '', icon: 'restart' };
    case 'error':
      return { variant: 'error', label: '', icon: 'alert' };
    case 'idle':
    case 'checking':
    default:
      // Up to date (or mid-check): show the current version chip, or hide if unknown.
      return appVersion ? { variant: 'idle', label: `v${appVersion}`, icon: 'check' } : null;
  }
}

/** Filter a releases array by channel, sort newest-first, and mark the running version. */
export function prepareReleases(releases, channel, appVersion) {
  if (!Array.isArray(releases)) return [];
  const includePre = channel === 'preview';
  return releases
    .filter((r) => includePre || !r.prerelease)
    .slice()
    .sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')))
    .map((r) => ({ ...r, current: !!appVersion && r.version === appVersion }));
}
