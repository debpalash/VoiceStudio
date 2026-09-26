/**
 * Settings → Translation (consolidated category).
 *
 * Pulls the translation controls into one home:
 *   • translateQuality pref (fast | autofit | cinematic) — zustand store binding.
 *   • A pointer to Settings → LLM Providers — the ONE place to configure the
 *     OpenAI-compatible LLM that powers Cinematic/Autofit translate, glossary
 *     extract, and dictation refinement. (Replaces the legacy inline LLM
 *     endpoint panel, whose TRANSLATE_* surface is fully covered by the
 *     registry's `custom` provider — a lone TRANSLATE_BASE_URL still resolves
 *     to `custom`, so nothing is lost.)
 *   • DeepL / Microsoft translator credentials — the non-LLM online translators.
 *     Same `/system/set-env` save path; these keys are in PERSISTENT_KEYS, so
 *     they survive restarts. HF_TOKEN stays in Credentials.
 */
import React, { useState } from 'react';
import { Languages, KeyRound, Brain } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { Segmented } from '../../ui';
import { useAppStore } from '../../store';
import { SettingsSection, SettingRow, SettingsInput, Collapsible } from './primitives';
import { Button } from '../../ui';

// Non-LLM online-translator credentials (DeepL, Microsoft). The OpenAI-compatible
// LLM (TRANSLATE_*) is configured in Settings → LLM Providers, not here, so it
// isn't duplicated. HF_TOKEN lives in the Credentials category.
const PROVIDER_FIELDS = [
  {
    key: 'DEEPL_API_KEY',
    labelKey: 'credentials.deepl_key',
    placeholder: 'DeepL API key',
    helpKey: 'credentials.deepl_key',
    isPassword: true,
  },
  {
    key: 'DEEPL_BASE_URL',
    labelKey: 'credentials.deepl_base_url',
    placeholder: 'https://api.deepl.com/v2',
    helpKey: 'credentials.deepl_base_url_help',
  },
  {
    key: 'MICROSOFT_API_KEY',
    labelKey: 'credentials.microsoft_key',
    placeholder: 'Microsoft API key',
    helpKey: 'credentials.microsoft_key',
    isPassword: true,
  },
  {
    key: 'MICROSOFT_BASE_URL',
    labelKey: 'credentials.microsoft_base_url',
    placeholder: 'https://api.cognitive.microsofttranslator.com',
    helpKey: 'credentials.microsoft_base_url_help',
  },
];

export default function TranslationTab() {
  const { t } = useTranslation();
  const translateQuality = useAppStore((s) => s.translateQuality);
  const setTranslateQuality = useAppStore((s) => s.setTranslateQuality);
  const openSettingsTab = useAppStore((s) => s.openSettingsTab);

  const [values, setValues] = useState({});
  const [saving, setSaving] = useState(null);

  const save = async (key) => {
    const value = (values[key] || '').trim();
    if (!value) return;
    setSaving(key);
    try {
      const { apiFetch } = await import('../../api/client');
      await apiFetch('/system/set-env', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value }),
      });
      // These keys are in the backend's PERSISTENT_KEYS set — they're restored
      // at startup, so the toast must not claim they're session-only.
      toast.success(t('credentials.saved_persisted', { key }));
      setValues((prev) => ({ ...prev, [key]: '' }));
    } catch (e) {
      toast.error(t('credentials.save_error', { message: e.message }));
    } finally {
      setSaving(null);
    }
  };

  return (
    <>
      <SettingsSection
        icon={Languages}
        title={t('settings.translation', { defaultValue: 'Translation' })}
        description={t('settings.translation_desc', {
          defaultValue: 'How dubbing translates dialogue, and which engine does it.',
        })}
      >
        <SettingRow
          title={t('settings.translate_quality', { defaultValue: 'Translation quality' })}
          subtitle={t('settings.translate_quality_desc', {
            defaultValue: 'Fast is literal and quick; Cinematic uses the LLM for natural phrasing.',
          })}
          control={
            <Segmented
              size="sm"
              value={translateQuality}
              onChange={setTranslateQuality}
              items={[
                { value: 'fast', label: t('settings.translate_fast', { defaultValue: 'Fast' }) },
                {
                  value: 'autofit',
                  label: t('settings.translate_autofit', { defaultValue: 'Autofit' }),
                },
                {
                  value: 'cinematic',
                  label: t('settings.translate_cinematic', { defaultValue: 'Cinematic' }),
                },
              ]}
            />
          }
        />
      </SettingsSection>

      <SettingsSection
        icon={Brain}
        title={t('settings.translation_llm', { defaultValue: 'Translation LLM' })}
        description={t('settings.translation_llm_desc', {
          defaultValue: 'Cinematic and Autofit translation use a high-quality LLM.',
        })}
      >
        <SettingRow
          align="start"
          title={t('settings.translation_llm_row', { defaultValue: 'LLM provider' })}
          subtitle={t('settings.translation_llm_row_desc', {
            defaultValue:
              'Choose, test, and activate a provider (OpenAI, OpenRouter, Groq, a local Ollama, …) in LLM Providers. Keys are stored encrypted; local providers stay fully offline.',
          })}
          control={
            <Button
              variant="primary"
              size="sm"
              onClick={() => openSettingsTab('llm-providers')}
              data-testid="translation-open-llm-providers"
            >
              {t('settings.translation_open_llm', { defaultValue: 'Open LLM Providers' })}
            </Button>
          }
        />
      </SettingsSection>

      <SettingsSection
        icon={KeyRound}
        title={t('settings.translation_providers', { defaultValue: 'Translation providers' })}
        description={t('settings.translation_providers_desc', {
          defaultValue:
            'API keys for the DeepL and Microsoft online translators. Saved and restored across restarts.',
        })}
      >
        <Collapsible
          title={t('settings.credentials_more', { defaultValue: 'Provider keys' })}
          icon={KeyRound}
          defaultOpen
        >
          {PROVIDER_FIELDS.map((field) => (
            <SettingRow
              key={field.key}
              align="start"
              stack
              title={t(field.labelKey)}
              note={t(field.helpKey)}
              control={
                <>
                  <SettingsInput
                    type={field.isPassword ? 'password' : 'text'}
                    mono
                    placeholder={field.placeholder}
                    value={values[field.key] || ''}
                    onChange={(e) =>
                      setValues((prev) => ({ ...prev, [field.key]: e.target.value }))
                    }
                    onKeyDown={(e) => e.key === 'Enter' && save(field.key)}
                  />
                  <Button
                    size="sm"
                    variant="subtle"
                    loading={saving === field.key}
                    onClick={() => save(field.key)}
                    disabled={!(values[field.key] || '').trim()}
                  >
                    {t('credentials.save')}
                  </Button>
                </>
              }
            />
          ))}
        </Collapsible>
      </SettingsSection>
    </>
  );
}
