/**
 * Settings → Performance panel (Wave 2 INST-12 UI half).
 *
 * Toggles the `Disable torch.compile` setting that backend engine
 * launchers read via `services.engine_env.build_engine_env()`.
 *
 * Usable on every platform since #2135. It was previously disabled
 * outside Windows on the theory that torch.compile only misbehaves
 * there (issue #65, the Triton kernel-cache OOM). #2135 is the
 * counter-example: a Linux/CUDA host whose engine was killed by
 * torch.compile, where the one control that would have stopped it was
 * greyed out. A toggle the affected user cannot reach is not a
 * safeguard.
 *
 * Endpoints:
 *   GET /api/settings/perf/torch-compile-disabled
 *     → {"enabled": bool, "platform": "darwin"|"linux"|"win32"}
 *   PUT /api/settings/perf/torch-compile-disabled
 *     body {"enabled": bool}  (loopback-only)
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
    defaultValue: 'Disable torch.compile (Windows)',
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
          defaultValue: 'Falls back to eager mode — fixes Triton OOM on <16 GB GPUs.',
        })}
        hint={
          <Trans
            i18nKey="settings.perf_torch_compile_hint"
            defaults="Falls back to eager mode by setting <code>TORCH_COMPILE_DISABLE=1</code> on the engine. Turn this on if model load or generation fails with a Triton / <code>torch.compile</code> error, or if the backend dies mid-generation — see <issueLink>#65</issueLink> (Windows OOM on GPUs under 16 GB) and <crashLink>#2135</crashLink> (CUDA-graph crash on older NVIDIA GPUs). Slower, but it always works."
            components={{
              // Trans injects each link's text ("#65" / "#2135") from the
              // translation string.
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
