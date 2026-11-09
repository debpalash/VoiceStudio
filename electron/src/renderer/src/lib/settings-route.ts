export const SETTINGS_ROUTE_STORAGE_KEY = 'voicestudio.settings.last-route';

const settingsRoutes = new Set([
  '/settings/appearance',
  '/settings/general',
  '/settings/models',
  '/settings/media',
  '/settings/pronunciation',
  '/settings/network',
  '/settings/sharing',
  '/settings/credentials',
  '/settings/performance',
  '/settings/usage',
  '/settings/privacy',
  '/settings/permissions',
  '/settings/storage',
  '/settings/support',
  '/settings/updates',
  '/settings/openapi',
  '/settings/diagnostics',
]);
const modelFamilyRoute = /^\/settings\/models\/(?:tts|asr|dictation|diarisation|translation|llm)$/;

function validSettingsRoute(value: string | null): value is string {
  return Boolean(value && (settingsRoutes.has(value) || modelFamilyRoute.test(value)));
}

export function readLastSettingsRoute(): string {
  try {
    const value = localStorage.getItem(SETTINGS_ROUTE_STORAGE_KEY);
    return validSettingsRoute(value) ? value : '/settings/general';
  } catch {
    return '/settings/general';
  }
}

export function rememberSettingsRoute(pathname: string): void {
  if (!validSettingsRoute(pathname)) return;
  try {
    localStorage.setItem(SETTINGS_ROUTE_STORAGE_KEY, pathname);
  } catch {
    // The current route remains usable when storage is unavailable.
  }
}
