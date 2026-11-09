import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertCircleIcon, LanguagesIcon } from 'lucide-react';
// Shared JSX controls are also consumed by the maintained shared UI modules.
// @ts-expect-error Shared JSX has no declaration file.
import LanguageFlag from '@shared/components/LanguageFlag';
import type { SearchOption } from '@/lib/language-search';
import { LANGUAGES } from '@/lib/languages';
import { languageOptions, languageSupported, type LanguageOption } from '@/lib/language-options';
import {
  canonicalLanguage,
  dialectOutcome,
  languageDialect,
  languageTagName,
} from '@/lib/language-variants';
import { setCloneSetting, useCloneSetting } from '@/lib/store/clone-settings';
import { LanguageMenu } from './language-menu';

export interface LanguagePickerProps {
  value?: string;
  onValueChange?: (value: string) => void;
  options?: string[];
  supportedOptions?: readonly string[] | null;
  disabled?: boolean;
  className?: string;
  /** Accessible name of the trigger; defaults to "Language". */
  ariaLabel?: string;
  modelLabel?: string;
  capabilityState?: 'known' | 'unknown' | 'loading' | 'error';
  recentsScope?: string;
  /** Regional accents the model renders itself (#3143); null = unknown. */
  renderedDialects?: readonly string[] | null;
}

export function LanguagePicker({
  value,
  onValueChange,
  options = LANGUAGES,
  supportedOptions,
  disabled = false,
  className,
  ariaLabel,
  modelLabel,
  capabilityState,
  recentsScope = 'language',
  renderedDialects,
}: LanguagePickerProps = {}) {
  const { t, i18n } = useTranslation();
  const saved = useCloneSetting('language');
  // A value saved before the regional split ("Portuguese") shows as the
  // variant it means when this picker offers variants; the stored value
  // itself is left untouched (#3143). Pickers without variants (dub source)
  // keep showing it as-is.
  const raw = value ?? saved;
  const canonical = canonicalLanguage(raw);
  const language = canonical !== raw && options.includes(canonical) ? canonical : raw;
  const dialect = modelLabel ? languageDialect(language) : undefined;
  const outcome = dialectOutcome(dialect, renderedDialects);
  const dialectNote =
    outcome === 'voice' && dialect
      ? t('languagePicker.dialectFollowsVoice', {
          model: modelLabel,
          language: languageTagName(dialect.split('-')[0], i18n.language),
        })
      : outcome && typeof outcome === 'object'
        ? t('languagePicker.dialectUsed', {
            model: modelLabel,
            variant: languageTagName(outcome.used, i18n.language),
          })
        : '';
  const entries = useMemo(
    () =>
      languageOptions(options, i18n.language, t('languagePicker.auto')).map((option) => ({
        ...option,
        disabled: !languageSupported(option.value, supportedOptions),
      })),
    [options, i18n.language, t, supportedOptions],
  );
  const invalid = entries.some((option) => option.value === language && option.disabled);
  const unavailable = modelLabel
    ? t('languagePicker.unsupportedBy', { model: modelLabel })
    : t('languagePicker.unavailable');
  const renderOption = (entry: SearchOption) => {
    const option = entry as LanguageOption;
    return (
      <>
        {option.code ? (
          <LanguageFlag code={option.code === 'zh' ? 'cmn-Hans' : option.code} />
        ) : (
          <LanguagesIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate leading-tight" title={option.label}>
            {option.label}
          </span>
          {option.native && option.native.toLowerCase() !== option.label.toLowerCase() && (
            <span
              className="block truncate text-[11px] leading-tight text-muted-foreground"
              lang={option.code}
            >
              {option.native}
            </span>
          )}
        </span>
        <span
          className="shrink-0 font-mono text-[10px] uppercase text-muted-foreground"
          aria-hidden="true"
        >
          {option.code}
        </span>
      </>
    );
  };
  return (
    <LanguageMenu
      value={language}
      options={entries}
      disabled={disabled}
      ariaLabel={ariaLabel || t('clone.language')}
      buttonClassName={className}
      unavailableLabel={unavailable}
      recentsKey={`voicestudio.language-recents.${recentsScope}.v1`}
      onChange={(next) => {
        if (onValueChange) onValueChange(next);
        else setCloneSetting('language', next);
      }}
      renderOption={renderOption}
      renderValue={(entry) => {
        const option = entry as LanguageOption | undefined;
        return (
          <>
            {invalid ? (
              <AlertCircleIcon className="size-4 text-warning" aria-hidden="true" />
            ) : option?.code ? (
              <LanguageFlag code={option.code === 'zh' ? 'cmn-Hans' : option.code} />
            ) : (
              <LanguagesIcon className="size-4 text-muted-foreground" aria-hidden="true" />
            )}
            <span className="max-w-48 truncate">{option?.label || language}</span>
          </>
        );
      }}
      header={
        <span className="min-w-0 truncate" title={modelLabel}>
          {modelLabel || t('clone.language')}
          {capabilityState && capabilityState !== 'known'
            ? ` · ${t(`languagePicker.${capabilityState}`)}`
            : ''}
        </span>
      }
      footer={
        modelLabel ? (
          <div className="space-y-1">
            {dialectNote && <p role="status">{dialectNote}</p>}
            <div className="flex items-center justify-between gap-2">
              <span>
                {invalid ? t('languagePicker.chooseSupported') : t('languagePicker.modelLanguages')}
              </span>
              <a
                href="#/settings/models/tts"
                className="shrink-0 rounded text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring"
              >
                {t('languagePicker.changeModel')}
              </a>
            </div>
          </div>
        ) : undefined
      }
    />
  );
}
