/** Settings destinations the command palette can jump to. Labels are existing i18n keys. */
export interface SettingsEntry {
  to: string;
  label: string;
}

export const SETTINGS_ENTRIES: SettingsEntry[] = [
  { to: '/settings/general', label: 'settings.language' },
  { to: '/settings/general', label: 'settings.review_mode' },
  { to: '/settings/appearance', label: 'preferences.theme' },
  { to: '/settings/appearance', label: 'preferences.font' },
  { to: '/settings/credentials', label: 'settings.credentials' },
  { to: '/settings/credentials', label: 'settings.hf_token_input' },
  { to: '/settings/credentials', label: 'settings.translation_providers' },
  { to: '/settings/credentials', label: 'credentials.deepl_key' },
  { to: '/settings/credentials', label: 'credentials.microsoft_key' },
  { to: '/settings/network', label: 'settings.network' },
  { to: '/settings/network', label: 'settings.proxy' },
  { to: '/settings/sharing', label: 'sharing.title' },
  { to: '/settings/sharing', label: 'settings.remote_backend_title' },
  { to: '/settings/performance', label: 'settings.compute_device_title' },
  { to: '/settings/usage', label: 'settings.usage' },
  { to: '/settings/privacy', label: 'settings.privacy' },
  { to: '/settings/privacy', label: 'privacy.analytics_title' },
  { to: '/settings/permissions', label: 'permissions.title' },
  { to: '/settings/permissions', label: 'permissions.microphone' },
  { to: '/settings/storage', label: 'settings.storage' },
  { to: '/settings/pronunciation', label: 'pronunciation.title' },
  { to: '/settings/pronunciation', label: 'pauseFill.room_tone' },
  { to: '/settings/media', label: 'settings.audio_tools' },
  { to: '/settings/logs', label: 'settings.logs' },
  { to: '/settings/updates', label: 'updates.tab' },
  { to: '/settings/support', label: 'donate.title' },
  { to: '/settings/diagnostics', label: 'about.diagnostics' },
  { to: '/settings/openapi', label: 'openapi.title' },
];
