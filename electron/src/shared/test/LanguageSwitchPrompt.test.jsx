import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';
import { useAppStore } from '../store';
import LanguageSwitchPrompt from '../components/LanguageSwitchPrompt';
import { configureLongformDurableStoreForTests } from '../utils/longformPersistence';

const withI18n = (node) => <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;

function seed(partial) {
  useAppStore.setState({
    locale: 'de',
    localeChosen: false,
    langPromptSeen: false,
    ...partial,
  });
}

beforeEach(async () => {
  let durableRecord = null;
  configureLongformDurableStoreForTests({
    read: async () => durableRecord,
    write: async (record) => {
      durableRecord = structuredClone(record);
    },
    clear: async () => {
      durableRecord = null;
    },
  });
  // Production bootstrap now awaits async IndexedDB hydration before render.
  await useAppStore.persist.rehydrate();
  seed({});
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe('LanguageSwitchPrompt — first-run offer to switch the UI to English', () => {
  it('shows for a non-English first run with no prior choice', async () => {
    render(withI18n(<LanguageSwitchPrompt />));
    expect(await screen.findByTestId('language-switch-prompt')).toBeInTheDocument();
    // Bilingual: the English offer line is always present (native name = Deutsch).
    expect(
      screen.getByText(/VoiceStudio opened in Deutsch\. Prefer English\?/),
    ).toBeInTheDocument();
    expect(screen.getByTestId('language-switch-english')).toBeInTheDocument();
  });

  it('never shows for an English UI', () => {
    seed({ locale: 'en' });
    const { container } = render(withI18n(<LanguageSwitchPrompt />));
    expect(container.firstChild).toBeNull();
  });

  it('never shows once the seen flag is set (one-time)', () => {
    seed({ langPromptSeen: true });
    const { container } = render(withI18n(<LanguageSwitchPrompt />));
    expect(container.firstChild).toBeNull();
  });

  it('never shows once the user has explicitly chosen a language', () => {
    seed({ localeChosen: true });
    const { container } = render(withI18n(<LanguageSwitchPrompt />));
    expect(container.firstChild).toBeNull();
  });

  it('"Switch to English" changes the language, records the choice, sets the flag', async () => {
    const changeSpy = vi.spyOn(i18n, 'changeLanguage');
    render(withI18n(<LanguageSwitchPrompt />));
    fireEvent.click(await screen.findByTestId('language-switch-english'));

    expect(changeSpy).toHaveBeenCalledWith('en');
    const s = useAppStore.getState();
    expect(s.locale).toBe('en');
    expect(s.localeChosen).toBe(true); // setLocale records the explicit choice
    expect(s.langPromptSeen).toBe(true);
    await waitFor(() =>
      expect(screen.queryByTestId('language-switch-prompt')).not.toBeInTheDocument(),
    );
  });

  it('"Keep" dismisses and sets the flag without touching the language', async () => {
    render(withI18n(<LanguageSwitchPrompt />));
    fireEvent.click(await screen.findByTestId('language-switch-keep'));

    const s = useAppStore.getState();
    expect(s.locale).toBe('de'); // language untouched
    expect(s.localeChosen).toBe(false);
    expect(s.langPromptSeen).toBe(true);
    await waitFor(() =>
      expect(screen.queryByTestId('language-switch-prompt')).not.toBeInTheDocument(),
    );
  });
});
