/**
 * Settings → Storage → Generation history retention (generation takes).
 *
 * Every synthesis is kept as a "take" (row + WAV in outputs/). Without a cap
 * they grow unbounded, so the backend prunes the oldest UNstarred takes over
 * this limit after each generation. Starred takes are never pruned. 0 = keep
 * everything.
 *
 * Endpoints:
 *   GET /api/settings/history-retention → {cap, default}
 *   PUT /api/settings/history-retention  body {cap}
 */
import React, { useCallback, useEffect, useState } from 'react';
import { History } from 'lucide-react';
import toast from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { apiJson, apiFetch } from '../../api/client';
import { Button } from '../../ui';
import { SettingsSection, SettingRow, InfoHint } from './primitives';

export default function HistoryRetentionPanel() {
  const { t } = useTranslation();
  const [cap, setCap] = useState('');
  const [def, setDef] = useState(200);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await apiJson('/api/settings/history-retention');
      setCap(String(d?.cap ?? ''));
      if (Number.isInteger(d?.default)) setDef(d.default);
      setLoaded(true);
    } catch (e) {
      if (e?.status === 404) {
        // Backend older than this panel — leave the default hint in place.
        setLoaded(true);
      } else {
        // Transport failure / 500: the shown default may not be the real cap,
        // so say so and hold Save until a load succeeds.
        setError(
          e?.message ||
            t('settings.history_retention_load_failed', {
              defaultValue: 'Could not load the current retention limit',
            }),
        );
      }
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const save = async () => {
    const n = Number.parseInt(cap, 10);
    if (!Number.isInteger(n) || n < 0) {
      toast.error(t('settings.history_retention_invalid', { defaultValue: 'Enter 0 or more' }));
      return;
    }
    setSaving(true);
    try {
      // apiFetch throws ApiError on any non-OK response.
      const res = await apiFetch('/api/settings/history-retention', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cap: n }),
      });
      const b = await res.json();
      setCap(String(b?.cap ?? n));
      toast.success(
        t('settings.history_retention_saved', { defaultValue: 'Retention limit saved' }),
      );
    } catch (e) {
      toast.error(
        e?.message ||
          t('settings.history_retention_save_failed', { defaultValue: 'Could not save' }),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection
      icon={History}
      title={t('settings.history_retention', { defaultValue: 'Generation history' })}
      description={t('settings.history_retention_desc', {
        defaultValue: 'How many takes to keep before the oldest are cleaned up.',
      })}
      actions={
        <InfoHint label={t('settings.history_retention', { defaultValue: 'Generation history' })}>
          {t('settings.history_retention_help', {
            defaultValue:
              'After each generation, the oldest unstarred takes over this limit are removed along with their audio files. Starred takes are always kept. Set 0 to keep everything.',
          })}
        </InfoHint>
      }
    >
      {error && (
        <div className="perfpanel__error" role="alert">
          {error}
        </div>
      )}
      <SettingRow
        title={t('settings.history_retention_cap', { defaultValue: 'Takes to keep' })}
        subtitle={t('settings.history_retention_cap_hint', {
          defaultValue: 'Starred takes never count against cleanup · 0 = unlimited',
          count: def,
        })}
        control={
          <div className="flex items-center gap-[var(--space-3)]">
            <input
              className="box-border w-[110px] rounded-[var(--chrome-radius-pill)] [border:1px_solid_var(--chrome-border)] bg-[var(--chrome-hover-bg)] px-[var(--space-3)] py-[var(--space-2)] font-[family-name:var(--chrome-font-mono)] text-[length:var(--text-base)] text-[var(--chrome-fg)] focus-visible:border-[var(--chrome-accent)] focus-visible:shadow-[var(--focus-ring)] focus-visible:outline-none"
              type="number"
              min="0"
              step="1"
              value={cap}
              placeholder={String(def)}
              onChange={(e) => setCap(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !saving && !loading && loaded) {
                  e.preventDefault();
                  save();
                }
              }}
              disabled={saving || loading}
              aria-label={t('settings.history_retention_cap', { defaultValue: 'Takes to keep' })}
              data-testid="history-retention-input"
            />
            <Button
              variant="primary"
              size="sm"
              onClick={save}
              loading={saving}
              disabled={loading || !loaded}
              data-testid="history-retention-save"
            >
              {saving
                ? t('common.saving', { defaultValue: 'Saving…' })
                : t('common.save', { defaultValue: 'Save' })}
            </Button>
          </div>
        }
      />
    </SettingsSection>
  );
}
