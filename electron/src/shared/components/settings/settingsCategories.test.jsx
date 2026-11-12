import { describe, it, expect } from 'vitest';

import {
  CATEGORIES,
  CATEGORY_BY_ID,
  matchCategories,
  resolveCategoryId,
} from './settingsCategories';
import en from '../../i18n/locales/en.json';

describe('matchCategories — search matching', () => {
  it('an empty query returns every category', () => {
    expect(matchCategories('')).toEqual(CATEGORIES.map((c) => c.id));
  });

  it('a query matching nothing returns an empty list (drives the sidebar empty state)', () => {
    expect(matchCategories('zzz-no-such-setting')).toEqual([]);
  });

  it('English keywords match in every locale (no translate fn needed)', () => {
    expect(matchCategories('proxy')).toContain('network');
    expect(matchCategories('ui scale')).toContain('appearance');
    expect(matchCategories('language')).toContain('appearance');
  });

  it('finds a provider by its spaced display name and by the joined spelling', () => {
    expect(matchCategories('cheaper inference')).toContain('llm-providers');
    expect(matchCategories('cheaperinference')).toContain('llm-providers');
  });

  it('folds the former General category into Appearance', () => {
    expect(CATEGORY_BY_ID.general).toBeUndefined();
    expect(resolveCategoryId('general')).toBe('appearance');
  });

  it('routes the moved panels: mirror searches open Network, models-directory searches open Storage', () => {
    expect(CATEGORY_BY_ID.models).toBeUndefined();
    expect(CATEGORY_BY_ID.engines).toBeUndefined();
    expect(resolveCategoryId('models')).toBe('storage');
    expect(matchCategories('mirror')).toContain('network');
    expect(matchCategories('mirror')).not.toContain('audio-tools');
    expect(matchCategories('hf_endpoint')).toEqual(['network']);
    expect(matchCategories('models directory')).toContain('storage');
  });

  it('uses the concise VoiceStudio API label', () => {
    expect(CATEGORY_BY_ID.openapi.defaultLabel).toBe('VoiceStudio API');
    expect(en.settings.openapi).toBe('VoiceStudio API');
  });

  it('keywordKeys match through the active locale, so localized setting names find their category', () => {
    // Simulate a German UI: settings.font resolves to "Schriftart".
    const t = (key) => (key === 'settings.font' ? 'Schriftart' : key);
    expect(matchCategories('schriftart', undefined, t)).toContain('appearance');
    // English keywords keep working alongside the translated titles.
    expect(matchCategories('font', undefined, t)).toContain('appearance');
  });

  it('every keywordKey points at a real en.json string (typo guard)', () => {
    for (const c of CATEGORIES) {
      for (const key of c.keywordKeys || []) {
        const value = key.split('.').reduce((node, part) => node?.[part], en);
        expect(typeof value, `${c.id}: ${key} missing from en.json`).toBe('string');
      }
    }
  });
});
