import React from 'react';
import { useTranslation } from 'react-i18next';
import { Settings2, Globe, ClipboardCheck } from 'lucide-react';
import i18n, { LANGUAGES } from '../../i18n';
import { Segmented, Select } from '../../ui';
import { SettingsSection, SettingRow } from './primitives';
import { useAppStore } from '../../store';

/**
 * Settings → General.
 *
 * Now scoped to the core interface preferences: language/locale and review
 * mode (the staged-checkpoint nudges that gate dub/ASR stages). The proxy +
 * FFmpeg "Advanced" controls moved to the Network category; pronunciation and
 * performance were promoted to their own categories. Theme moved to Appearance.
 */
export default function GeneralTab() {
  const { t } = useTranslation();
  const locale = useAppStore((s) => s.locale);
  const setLocale = useAppStore((s) => s.setLocale);
  const reviewMode = useAppStore((s) => s.reviewMode);
  const setReviewMode = useAppStore((s) => s.setReviewMode);

  const handleLocaleChange = (e) => {
    const id = e.target.value;
    setLocale(id);
    i18n.changeLanguage(id);
  };

  return (
    <SettingsSection icon={Settings2} title={t('settings.general')}>
      <SettingRow
        icon={Globe}
        title={t('settings.language')}
        subtitle={t('settings.language_desc')}
        control={
          <Select size="sm" value={locale} onChange={handleLocaleChange}>
            {LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </Select>
        }
      />

      <SettingRow
        icon={ClipboardCheck}
        title={t('settings.review_mode', { defaultValue: 'Review mode' })}
        subtitle={t('settings.review_mode_desc', {
          defaultValue: 'Pause between pipeline stages so you can review ASR / translation output.',
        })}
        control={
          <Segmented
            size="sm"
            value={reviewMode}
            onChange={setReviewMode}
            items={[
              {
                value: 'on',
                label: t('settings.review_mode_on', { defaultValue: 'Pause for review' }),
              },
              {
                value: 'off',
                label: t('settings.review_mode_off', { defaultValue: 'Run straight through' }),
              },
            ]}
          />
        }
      />
    </SettingsSection>
  );
}
