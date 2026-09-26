import { describe, it, expect, afterEach } from 'vitest';
import i18n from '../i18n';

// Settings → General → Language offers Arabic; picking it (or any future RTL
// locale) must flip the document to RTL — and switching back must restore LTR.
// The wiring lives in the languageChanged handler in src/i18n/index.ts.
describe('i18n — document direction follows the active locale', () => {
  afterEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('switching to Arabic sets <html dir="rtl" lang="ar">', async () => {
    await i18n.changeLanguage('ar');
    expect(document.documentElement.dir).toBe('rtl');
    expect(document.documentElement.lang).toBe('ar');
  });

  it('switching back to English restores <html dir="ltr" lang="en">', async () => {
    await i18n.changeLanguage('ar');
    await i18n.changeLanguage('en');
    expect(document.documentElement.dir).toBe('ltr');
    expect(document.documentElement.lang).toBe('en');
  });

  it('LTR locales stay LTR', async () => {
    await i18n.changeLanguage('de');
    expect(document.documentElement.dir).toBe('ltr');
    expect(document.documentElement.lang).toBe('de');
  });
});
