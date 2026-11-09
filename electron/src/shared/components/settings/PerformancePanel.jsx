/**
 * Settings → Performance panel (Wave 2 INST-12 UI half).
 *
 * Toggles the `Disable torch.compile` setting read by
 * `services.engine_env.build_engine_env()`, on every platform since #2135.
 *
 * Endpoints:
 *   GET /api/settings/perf/torch-compile-disabled
 *     → {"enabled": bool, "platform": "darwin"|"linux"|"win32"}
 *   PUT /api/settings/perf/torch-compile-disabled
 *     body {"enabled": bool}  (loopback-only; GET returns enabled + platform)
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Cpu } from 'lucide-react';
import { Trans, useTranslation } from 'react-i18next';
import { apiJson, apiFetch } from '../../api/client';
import { SettingsSection, SettingRow, SettingsToggle } from './primitives';
import RestartBadge from './RestartBadge';

export default function PerformancePanel() {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiJson('/api/settings/perf/torch-compile-disabled');
      setEnabled(Boolean(data?.enabled));
    } catch (e) {
      setError(
        e?.message ||
          t('settings.perf_load_failed', { defaultValue: 'Failed to load performance settings' }),
      );
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onToggle = async (next) => {
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch('/api/settings/perf/torch-compile-disabled', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      });
      const body = await res.json().catch(() => ({}));
      setEnabled(Boolean(body?.enabled ?? next));
    } catch (err) {
      setError(
        err?.message || t('settings.perf_save_failed', { defaultValue: 'Failed to save setting' }),
      );
      // Re-sync on failure so the UI doesn't show a stale state
      refresh();
    } finally {
      setSaving(false);
    }
  };

  const toggleLabel = t('settings.perf_torch_compile', {
    defaultValue: 'Disable torch.compile',
  });

  return (
    <SettingsSection icon={Cpu} title={t('settings.perf_title', { defaultValue: 'Performance' })}>
      {error && (
        <div className="perfpanel__error" role="alert">
          {error}
        </div>
      )}

      <SettingRow
        title={
          <>
            {toggleLabel}
            <RestartBadge />
          </>
        }
        note={t('settings.perf_torch_compile_note', {
          defaultValue: 'Uses eager execution to avoid compiler and GPU graph failures.',
        })}
        hint={
          <Trans
            i18nKey="settings.perf_torch_compile_hint"
            defaults="Sets <code>TORCH_COMPILE_DISABLE=1</code> for the engine and uses slower, dependable eager mode. Enable it when loading or generation reports Triton / <code>torch.compile</code> errors, or when the backend exits during generation. See <issueLink>#65</issueLink> for Windows OOMs below 16 GB VRAM and <crashLink>#2135</crashLink> for CUDA-graph crashes on older NVIDIA GPUs."
            components={{
              // Trans injects the link text ("#65") from the translation string.
              issueLink: (
                <a
                  href="https://github.com/debpalash/VoiceStudio/issues/65"
                  target="_blank"
                  rel="noopener noreferrer"
                />
              ),
              crashLink: (
                <a
                  href="https://github.com/debpalash/VoiceStudio/issues/2135"
                  target="_blank"
                  rel="noopener noreferrer"
                />
              ),
              code: <code />,
            }}
          />
        }
        control={
          <SettingsToggle
            checked={enabled}
            onChange={onToggle}
            disabled={saving || loading}
            aria-label={toggleLabel}
            data-testid="torch-compile-toggle"
          />
        }
      />
    </SettingsSection>
  );
}
