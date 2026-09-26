// Regression guard: VoiceGallery/CommunityZone/ImportsZone catch blocks used
// to discard the real error and show a hardcoded, often-wrong generic guess
// (e.g. "the engine may be loading" on ANY failure, including ones that had
// nothing to do with loading). Fixed to interpolate the real `e.message`
// (already a clean, user-facing string from api/client.js's ApiError),
// matching the `{{message}}` convention used everywhere else in this file.
// This test only pins the i18n keys, not the call sites, deliberately: it's
// a cheap net against reverting to a hardcoded string, not a full behavior test.
import { describe, it, expect } from 'vitest';
import en from '../i18n/locales/en.json';

describe('gallery error messages interpolate the real error', () => {
  const keys = [
    'use_failed',
    'preview_failed',
    'search_failed',
    'upload_failed',
    'save_failed',
    'delete_failed',
    'trim_load_failed',
  ];

  it.each(keys)('gallery.%s contains {{message}}', (key) => {
    expect(en.gallery[key]).toContain('{{message}}');
  });
});
