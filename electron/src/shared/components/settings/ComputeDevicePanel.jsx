/**
 * Settings → Performance: the compute-device override.
 *
 * Lets the user pin which device family the backend uses (auto / CUDA /
 * ROCm / XPU / MPS / CPU) instead of trusting auto-detect — the fix for the
 * "auto-detect picked wrong" issue class. Options are limited to families
 * that actually exist on this host (plus Auto and CPU, which always do);
 * the pick applies at the next backend start, same restart contract as the
 * rest of this tab. `OMNIVOICE_DEVICE` pins the value and disables the
 * control rather than pretending the UI choice would win.
 *
 * Endpoints:
 *   GET /api/settings/compute-device
 *     → {value, applied, restart_required, effective_family, auto_family,
 *        available_families, env_pinned, choices}
 *   PUT /api/settings/compute-device  body {"value": "auto"|family}
 */
import React, { useCallback, useEffect, useState } from 'react';
import { MonitorCog } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { apiJson, apiFetch } from '../../api/client';
import { Select } from '../../ui';
import { SettingsSection, SettingRow } from './primitives';
import RestartBadge from './RestartBadge';

// English fallbacks; the rendered label comes from the locale files
// (settings.device_family_*) so localized builds stay localized.
const FAMILY_FALLBACKS = {
  cuda: 'NVIDIA GPU (CUDA)',
  rocm: 'AMD GPU (ROCm)',
  xpu: 'Intel GPU (XPU)',
  mps: 'Apple GPU (MPS)',
  cpu: 'CPU',
};

export default function ComputeDevicePanel() {
  const { t } = useTranslation();
  const [state, setState] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      setState(await apiJson('/api/settings/compute-device'));
    } catch (e) {
      setError(
        e?.message ||
          t('settings.device_load_failed', { defaultValue: 'Failed to load device setting' }),
      );
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onChange = async (e) => {
    const value = e.target.value;
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch('/api/settings/compute-device', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value }),
      });
      const body = await res.json().catch(() => null);
      if (body?.value) setState(body);
      else refresh();
    } catch (err) {
      setError(
        err?.message || t('settings.perf_save_failed', { defaultValue: 'Failed to save setting' }),
      );
      // Re-sync so the UI never shows a pick that didn't persist — but keep
      // the save error visible (refresh() would clear it).
      try {
        setState(await apiJson('/api/settings/compute-device'));
      } catch {
        /* the save error already on screen covers this */
      }
    } finally {
      setSaving(false);
    }
  };

  const label = t('settings.compute_device', { defaultValue: 'Compute device' });
  const families = state?.available_families || [];
  const familyLabel = (f) =>
    t(`settings.device_family_${f}`, { defaultValue: FAMILY_FALLBACKS[f] || f });

  return (
    <SettingsSection
      icon={MonitorCog}
      title={t('settings.compute_device_title', { defaultValue: 'Compute device' })}
      description={t('settings.compute_device_desc', {
        defaultValue: 'Which device the backend runs models on. Auto is right for almost everyone.',
      })}
    >
      {error && (
        <div className="perfpanel__error" role="alert">
          {error}
        </div>
      )}

      <SettingRow
        title={
          <>
            {label}
            <RestartBadge />
          </>
        }
        subtitle={(() => {
          const pinned = state?.env_pinned
            ? t('settings.compute_device_env_pinned', {
                defaultValue: 'Pinned by the OMNIVOICE_DEVICE environment variable',
              })
            : null;
          const ignored = state?.override_ignored
            ? t('settings.compute_device_ignored', {
                defaultValue: 'That device was not detected on this machine — Auto is in effect',
              })
            : null;
          // An env pin naming absent hardware needs BOTH facts: why the
          // control is disabled, and that the pin is not actually in effect.
          if (pinned && ignored) return `${pinned} · ${ignored}`;
          if (pinned) return pinned;
          if (ignored) return ignored;
          return state?.restart_required
            ? t('settings.compute_device_restart', {
                defaultValue: 'Takes effect after the app restarts',
              })
            : undefined;
        })()}
        note={t('settings.compute_device_note', {
          defaultValue:
            'Only devices detected on this machine are listed. CPU always works; pinning a device never invents hardware.',
        })}
        control={
          <Select
            size="sm"
            value={state?.value ?? 'auto'}
            onChange={onChange}
            disabled={!state || saving || state?.env_pinned}
            aria-label={label}
            data-testid="compute-device-select"
          >
            <option value="auto">
              {t('settings.compute_device_auto', {
                defaultValue: 'Auto (recommended)',
              })}
              {state?.auto_family ? ` — ${familyLabel(state.auto_family)}` : ''}
            </option>
            {families.map((f) => (
              <option key={f} value={f}>
                {familyLabel(f)}
              </option>
            ))}
          </Select>
        }
      />
    </SettingsSection>
  );
}
