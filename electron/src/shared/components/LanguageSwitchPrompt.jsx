/**
 * LanguageSwitchPrompt — first-run-only offer to switch the UI to English.
 *
 * VoiceStudio opens in the user's OS/browser language when it's one of the
 * shipped locales (i18n LanguageDetector → navigator). Some users on a
 * non-English OS still prefer the English UI (it matches the docs and the
 * community). On the FIRST run only, if the auto-detected UI language isn't
 * English and the user hasn't already chosen a language, this shows a compact,
 * dismissible banner offering the switch. It is a UI-language convenience only
 * — nothing leaves the machine.
 *
 * Shows only when ALL hold:
 *   - the user has never made an explicit language choice (`localeChosen` false
 *     — the Settings picker and this very offer are the only things that set
 *     it), AND
 *   - the resolved UI language is not English, AND
 *   - the one-time seen flag (`langPromptSeen`) is unset.
 * Otherwise it renders nothing — never for English users, never again after
 * being answered/dismissed, never once a language was picked in Settings.
 *
 * Bilingual by design: the offer line is shown in English (from the 'en'
 * bundle, regardless of the active locale) AND in the detected language, so
 * the user understands the offer either way. The English copy always renders
 * because it reads from a fixed 'en' translator, not the active one.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Languages, X } from 'lucide-react';
import i18n, { LANGUAGES } from '../i18n';
import { useAppStore } from '../store';
import { Button } from '../ui';

/** Native language name for a locale code (e.g. 'de' → 'Deutsch'), from the
 * i18n LANGUAGES table — never a hardcoded string in component code. */
function nativeName(code) {
  return LANGUAGES.find((l) => l.code === code)?.label || code;
}

export default function LanguageSwitchPrompt() {
  const { t } = useTranslation();
  const locale = useAppStore((s) => s.locale);
  const localeChosen = useAppStore((s) => s.localeChosen);
  const langPromptSeen = useAppStore((s) => s.langPromptSeen);
  const setLocale = useAppStore((s) => s.setLocale);
  const setLangPromptSeen = useAppStore((s) => s.setLangPromptSeen);

  // Gate on persist rehydration so a returning English user (whose `localeChosen`
  // lives in localStorage) never sees a flash of the banner during the async
  // rehydrate window, when the store still holds its navigator-derived default.
  const [hydrated, setHydrated] = useState(() => useAppStore.persist?.hasHydrated?.() ?? true);
  useEffect(() => {
    if (hydrated) return undefined;
    const unsub = useAppStore.persist?.onFinishHydration?.(() => setHydrated(true));
    if (useAppStore.persist?.hasHydrated?.()) setHydrated(true);
    return unsub;
  }, [hydrated]);

  if (!hydrated || localeChosen || langPromptSeen || locale === 'en') return null;

  const language = nativeName(locale);
  // English offer line: always from the 'en' bundle, independent of the active
  // locale — this is what makes the banner readable to an English-preferring
  // user whose UI is currently in another language.
  const enT = i18n.getFixedT('en');
  const englishLine = enT('langPrompt.offer', { language });
  // Same offer in the detected language (active locale). If its lazy bundle
  // isn't loaded yet it falls back to English — then we'd show one line, not two.
  const localizedLine = t('langPrompt.offer', { language });

  const switchToEnglish = () => {
    setLocale('en'); // also sets localeChosen — this is a deliberate choice
    i18n.changeLanguage('en');
    setLangPromptSeen(true);
  };
  const keep = () => setLangPromptSeen(true);

  return (
    <div
      role="dialog"
      aria-label={englishLine}
      className="fixed bottom-[var(--space-4)] left-1/2 z-[70] flex w-[min(560px,92vw)] -translate-x-1/2 items-start gap-[var(--space-3)] rounded-lg border border-border bg-bg-elev-1 px-[var(--space-4)] py-[var(--space-3)] shadow-lg backdrop-blur-md"
      data-testid="language-switch-prompt"
    >
      <Languages size={16} className="mt-[3px] shrink-0 text-primary" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="text-[length:var(--text-sm)] font-semibold text-fg">{englishLine}</div>
        {localizedLine !== englishLine && (
          <div className="mt-[2px] text-[length:var(--text-sm)] text-fg-muted">{localizedLine}</div>
        )}
        <div className="mt-[var(--space-2)] flex flex-wrap gap-[var(--space-2)]">
          <Button
            variant="primary"
            size="sm"
            onClick={switchToEnglish}
            data-testid="language-switch-english"
          >
            {t('langPrompt.switch')}
          </Button>
          <Button variant="ghost" size="sm" onClick={keep} data-testid="language-switch-keep">
            {t('langPrompt.keep', { language })}
          </Button>
        </div>
      </div>
      <Button
        variant="ghost"
        size="sm"
        iconSize="sm"
        onClick={keep}
        title={t('langPrompt.keep', { language })}
      >
        <X size={12} />
      </Button>
    </div>
  );
}
