import { beforeEach, expect, it } from 'vitest';
import {
  readLastSettingsRoute,
  rememberSettingsRoute,
  SETTINGS_ROUTE_STORAGE_KEY,
} from './settings-route';

beforeEach(() => localStorage.clear());

it('restores only real settings routes and defaults to General', () => {
  expect(readLastSettingsRoute()).toBe('/settings/general');
  rememberSettingsRoute('/settings/models/diarisation');
  expect(readLastSettingsRoute()).toBe('/settings/models/diarisation');

  localStorage.setItem(SETTINGS_ROUTE_STORAGE_KEY, '/clone');
  expect(readLastSettingsRoute()).toBe('/settings/general');

  localStorage.setItem(SETTINGS_ROUTE_STORAGE_KEY, '/settings/workers');
  expect(readLastSettingsRoute()).toBe('/settings/general');
});

it('ignores non-settings paths rather than replacing the last page', () => {
  rememberSettingsRoute('/settings/storage');
  rememberSettingsRoute('/projects');
  expect(readLastSettingsRoute()).toBe('/settings/storage');
});
