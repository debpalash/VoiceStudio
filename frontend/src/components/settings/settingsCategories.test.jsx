import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  CATEGORIES,
  CATEGORY_BY_ID,
  matchCategories,
  resolveCategoryId,
} from './settingsCategories';
import en from '../../i18n/locales/en.json';

const SETTINGS_DIR = path.dirname(fileURLToPath(import.meta.url));

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

  it('folds the former General category into Appearance', () => {
    expect(CATEGORY_BY_ID.general).toBeUndefined();
    expect(resolveCategoryId('general')).toBe('appearance');
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

describe('restart flag ↔ RestartBadge lockstep', () => {
  // Panel file → hosting category (per Settings.jsx renderCategory). Any panel
  // that renders the "Restart required" badge must live in a category flagged
  // restart: true, or the sidebar ↻ glyph / header badge contract breaks
  // (that drift is exactly how Network shipped without its glyph).
  const PANEL_CATEGORY = {
    'StoragePanel.jsx': 'models',
    'HFMirrorPanel.jsx': 'models',
    'RemoteBackendPanel.jsx': 'sharing',
    'AudioToolsPanel.jsx': 'audio-tools',
    'PerformancePanel.jsx': 'performance',
    'ComputeDevicePanel.jsx': 'performance',
    'GenerateBudgetPanel.jsx': 'performance',
  };

  const panelsUsingRestartBadge = fs
    .readdirSync(SETTINGS_DIR)
    .filter((f) => f.endsWith('.jsx') && !f.includes('.test.') && f !== 'RestartBadge.jsx')
    .filter((f) => {
      const src = fs.readFileSync(path.join(SETTINGS_DIR, f), 'utf8');
      // Only the restart-warning form counts; `<RestartBadge applies` is the
      // "Applies now" affordance and needs no category flag.
      return /<RestartBadge(?!\s+applies)/.test(src);
    });

  it('finds the known restart-badge panels (scan sanity check)', () => {
    expect(panelsUsingRestartBadge.length).toBeGreaterThan(0);
  });

  it.each(panelsUsingRestartBadge)('%s belongs to a restart-flagged category', (file) => {
    const categoryId = PANEL_CATEGORY[file];
    expect(
      categoryId,
      `${file} renders <RestartBadge /> but has no category mapping — add it to PANEL_CATEGORY and flag the category`,
    ).toBeDefined();
    expect(
      CATEGORY_BY_ID[categoryId]?.restart,
      `category "${categoryId}" hosts ${file} (restart-bound setting) but lacks restart: true in settingsCategories.jsx`,
    ).toBe(true);
  });
});
