/**
 * Settings → Capture → Dictation refinement panel (parity program Wave 2.1).
 *
 * Toggles the optional local-LLM cleanup of dictation finals: filler-word
 * removal, self-correction collapse, technical-term preservation. The
 * backend only runs refinement when an LLM backend is configured
 * (Settings → Credentials / TRANSLATE_BASE_URL) — without one, dictation
 * behaves exactly as before on every platform.
 *
 * Endpoints (loopback-only):
 *   GET /api/settings/dictation-refinement
 *     → {auto, smart_cleanup, self_correction, preserve_technical, llm_ready}
 *   PUT /api/settings/dictation-refinement  body: partial of the above flags
 *
 * The section shell always renders: while loading it shows a muted loading
 * line, and when the initial GET fails it shows the error with a Retry button
 * instead of silently disappearing from Settings (the backend may just be
 * restarting). All strings go through i18n (`dictation.*`).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Wand2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { apiJson, apiFetch } from '../../api/client';
import { useAppStore } from '../../store';
import { refineFailureNoteKey } from './refineStatus';
import { SettingsSection, SettingRow, SettingsToggle } from './primitives';
import { Button } from '../../ui';

// Flag key → i18n label/hint pair (`dictation.flag_<key>` / `…_hint`).
const FLAG_KEYS = ['auto', 'smart_cleanup', 'self_correction', 'preserve_technical'];

export default function RefinementPanel() {
  const { t } = useTranslation();
  const [cfg, setCfg] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      setCfg(await apiJson('/api/settings/dictation-refinement'));
    } catch (e) {
      setError(e?.message || t('dictation.load_error'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onToggle = async (key, next) => {
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch('/api/settings/dictation-refinement', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: next }),
      });
      setCfg(await res.json());
    } catch (err) {
      setError(err?.message || t('dictation.save_error'));
      refresh();
    } finally {
      setSaving(false);
    }
  };

  const openLlmProviders = () => useAppStore.getState().openSettingsTab('llm-providers');

  const llmReady = Boolean(cfg?.llm_ready);
  const failureNoteKey = refineFailureNoteKey(cfg?.last_refine_status);

  return (
    <SettingsSection
      icon={Wand2}
      title={t('dictation.title')}
      description={cfg && !llmReady ? t('dictation.needs_llm') : undefined}
      actions={
        // A first-time user with no LLM shouldn't dead-end on the description —
        // the configure step is one click away, before the first failure.
        cfg && !llmReady ? (
          <Button
            variant="subtle"
            size="sm"
            onClick={openLlmProviders}
            data-testid="refine-open-llm"
          >
            {t('dictation.open_llm_providers')}
          </Button>
        ) : undefined
      }
    >
      {error && (
        <div className="perfpanel__error" role="alert">
          {error}
          {!cfg && (
            <>
              {' '}
              <button
                type="button"
                className="underline"
                onClick={refresh}
                data-testid="refine-retry"
              >
                {t('dictation.retry')}
              </button>
            </>
          )}
        </div>
      )}

      {!cfg && !error && loading && <p className="perfpanel__help">{t('common.loading')}</p>}

      {cfg && failureNoteKey && (
        <div className="perfpanel__error" role="status">
          {t(failureNoteKey)}{' '}
          <button type="button" className="underline" onClick={openLlmProviders}>
            {t('dictation.open_llm_providers')}
          </button>
        </div>
      )}

      {cfg &&
        FLAG_KEYS.map((key) => (
          <SettingRow
            key={key}
            title={t(`dictation.flag_${key}`)}
            subtitle={key === 'auto' && !llmReady ? t('dictation.no_llm_configured') : undefined}
            hint={t(`dictation.flag_${key}_hint`)}
            control={
              <SettingsToggle
                checked={Boolean(cfg[key])}
                onChange={(next) => onToggle(key, next)}
                disabled={saving || (key !== 'auto' && !cfg.auto)}
                aria-label={t(`dictation.flag_${key}`)}
              />
            }
          />
        ))}
    </SettingsSection>
  );
}
