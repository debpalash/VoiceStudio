/**
 * Settings → Models tab → Hugging Face mirror panel (Wave 4.3).
 *
 * Default mode is **Auto (recommended)**: the backend probes the official
 * endpoint and the community mirror, picks whichever actually works
 * (preferring huggingface.co unless the mirror is decisively faster), and
 * remembers the pick — restricted-network users (e.g. behind the Great
 * Firewall) get working downloads without hunting for this panel. Downloads
 * are checksum-verified by Hugging Face regardless of endpoint.
 *
 * Explicit choices stay explicit: picking a preset or saving a custom URL
 * pins that endpoint (HF_ENDPOINT, persisted to the durable per-user env; HF
 * reads it at import time, so loads apply after a restart) and auto never
 * switches it. Existing configured endpoints load as the matching manual
 * mode — never migrated to Auto.
 *
 * Endpoints (loopback-only):
 *   GET  /api/settings/hf-mirror      → {configured, effective, presets, mode, auto}
 *   PUT  /api/settings/hf-mirror      body {url, mode}  (mode 'auto' clears the url)
 *   POST /api/settings/hf-mirror/test → re-run the probe race ("Test again")
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Globe, RefreshCw, Zap } from 'lucide-react';
import toast from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { apiJson, apiFetch } from '../../api/client';
import { SettingsSection, SettingRow, SettingsInput } from './primitives';
import { Button } from '../../ui';
import RestartBadge from './RestartBadge';

/** Normalize a mirror URL for equality checks (trailing slashes, whitespace). */
const normalizeMirror = (u) => (u || '').trim().replace(/\/+$/, '');

/** Host of the auto pick ("hf-mirror.com"), for compact display. */
const hostOf = (url) => {
  try {
    return new URL(url).hostname || url;
  } catch {
    return url || '';
  }
};

export default function HFMirrorPanel() {
  const { t } = useTranslation();
  const [state, setState] = useState(null);
  const [url, setUrl] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState(null);
  const [restart, setRestart] = useState(false);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const d = await apiJson('/api/settings/hf-mirror');
      setState(d);
      setUrl(d?.configured || '');
    } catch (e) {
      setError(e?.message || t('models.mirror_load_error'));
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const save = async (value, mode = 'manual') => {
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch('/api/settings/hf-mirror', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: value, mode }),
      });
      const d = await res.json();
      setState(d);
      setUrl(d.configured || '');
      setRestart(Boolean(d.restart_required));
      toast.success(t('models.mirror_saved', { defaultValue: 'Mirror setting saved' }));
    } catch (e) {
      setError(e?.message || t('models.mirror_save_error'));
    } finally {
      setSaving(false);
    }
  };

  const testAgain = async () => {
    setTesting(true);
    setError(null);
    try {
      const res = await apiFetch('/api/settings/hf-mirror/test', { method: 'POST' });
      const d = await res.json();
      setState(d);
      setUrl(d.configured || '');
    } catch (e) {
      setError(
        e?.message || t('models.mirror_auto_test_error', { defaultValue: 'Endpoint test failed' }),
      );
    } finally {
      setTesting(false);
    }
  };

  const configured = normalizeMirror(state?.configured);
  const isAuto = state?.mode === 'auto';
  const auto = state?.auto || null;

  // Auto status line: pick + latency + last checked, or the untested/offline states.
  let autoStatus = null;
  if (isAuto) {
    if (!auto) {
      autoStatus = t('models.mirror_auto_untested', {
        defaultValue: 'Not tested yet — the next download picks the best endpoint automatically.',
      });
    } else if (!auto.reachable) {
      autoStatus = t('models.mirror_auto_offline', {
        defaultValue:
          'No Hugging Face endpoint reachable right now — cached models keep working; auto retries on the next download.',
      });
    } else {
      const parts = [hostOf(auto.endpoint)];
      if (typeof auto.latency_ms === 'number') {
        parts.push(
          t('models.mirror_auto_latency', {
            ms: Math.round(auto.latency_ms),
            defaultValue: '{{ms}} ms',
          }),
        );
      }
      if (auto.checked_at) {
        parts.push(
          t('models.mirror_auto_checked', {
            when: new Date(auto.checked_at * 1000).toLocaleString(),
            defaultValue: 'checked {{when}}',
          }),
        );
      }
      autoStatus = parts.join(' · ');
    }
  }

  // Always render the section shell: a restricted-network user whose backend
  // GET failed is exactly the user who needs this panel — never let it vanish.
  return (
    <SettingsSection
      className="models-settings-compact !mb-[8px] !px-[10px] !py-[9px] [&>header]:!mb-[6px] [&>header]:!gap-[8px] [&>header]:!pb-[6px] [&>header_h2]:!text-[length:var(--text-md)] [&>header_p]:!leading-[1.3] [&>header_[data-slot=settings-section-icon]]:!h-[24px] [&>header_[data-slot=settings-section-icon]]:!w-[24px]"
      compact
      icon={Globe}
      title={t('models.mirror_title')}
      description={t('models.mirror_description')}
      actions={<RestartBadge />}
    >
      {error && (
        <div className="perfpanel__error" role="alert">
          {error}
        </div>
      )}

      {!state && !error && (
        <div
          data-testid="hf-mirror-loading"
          className="py-[var(--space-4)] text-[color:var(--chrome-fg-muted)] text-[length:var(--text-sm)]"
        >
          {t('common.loading')}
        </div>
      )}

      {!state && error && (
        <Button
          variant="subtle"
          size="sm"
          leading={<RefreshCw size={13} aria-hidden="true" />}
          onClick={refresh}
          data-testid="hf-mirror-retry"
        >
          {t('models.mirror_retry', { defaultValue: 'Retry' })}
        </Button>
      )}

      {state && (
        <>
          <SettingRow
            className="!px-[6px] !py-[5px]"
            title={t('models.mirror_preset_title')}
            hint={t('models.mirror_preset_hint')}
            control={
              <div className="flex min-w-0 max-w-full flex-wrap items-center gap-[4px]">
                <Button
                  variant="preset"
                  className="!px-[7px] !py-[3px]"
                  active={isAuto}
                  onClick={() => save('', 'auto')}
                  disabled={saving}
                  leading={<Zap size={12} aria-hidden="true" />}
                  data-testid="hf-preset-auto"
                >
                  {t('models.mirror_mode_auto', { defaultValue: 'Auto (recommended)' })}
                </Button>
                {state.presets.map((p) => (
                  <Button
                    variant="preset"
                    className="!px-[7px] !py-[3px]"
                    key={p.label}
                    active={!isAuto && normalizeMirror(p.url) === configured}
                    onClick={() => save(p.url, 'manual')}
                    disabled={saving}
                    data-testid={`hf-preset-${p.url || 'official'}`}
                  >
                    {p.label}
                  </Button>
                ))}
              </div>
            }
          />

          {isAuto && (
            <SettingRow
              className="!px-[6px] !py-[5px]"
              title={t('models.mirror_auto_title', { defaultValue: 'Automatic selection' })}
              hint={t('models.mirror_auto_hint', {
                defaultValue:
                  'VoiceStudio probes huggingface.co and the community mirror, then uses whichever actually works — preferring the official endpoint unless the mirror is decisively faster. Downloads are checksum-verified by Hugging Face regardless of endpoint, so a mirror can never corrupt models.',
              })}
              control={
                <>
                  <span
                    className="min-w-0 flex-1 truncate font-mono text-[length:var(--text-xs)] text-[color:var(--chrome-fg-muted)]"
                    data-testid="hf-mirror-auto-status"
                  >
                    {autoStatus}
                  </span>
                  <Button
                    variant="subtle"
                    size="sm"
                    leading={<RefreshCw size={13} aria-hidden="true" />}
                    onClick={testAgain}
                    loading={testing}
                    disabled={testing || saving}
                    data-testid="hf-mirror-test"
                  >
                    {testing
                      ? t('models.mirror_auto_testing', { defaultValue: 'Testing…' })
                      : t('models.mirror_auto_test', { defaultValue: 'Test again' })}
                  </Button>
                </>
              }
            />
          )}

          <SettingRow
            className="!px-[6px] !py-[5px]"
            title={t('models.mirror_custom_url', { defaultValue: 'Custom mirror URL' })}
            hint={t('models.mirror_custom_url_note', {
              defaultValue: 'Sets the HF_ENDPOINT environment variable for Hugging Face downloads.',
            })}
            subtitle={restart ? t('models.mirror_restart_note') : undefined}
            control={
              <>
                <SettingsInput
                  mono
                  type="text"
                  className="!max-w-none !rounded-[8px] !px-[8px] !py-[5px]"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://hf-mirror.com"
                  name="hugging-face-mirror-url"
                  autoComplete="off"
                  spellCheck={false}
                  aria-label={t('models.mirror_custom_url', { defaultValue: 'Custom mirror URL' })}
                  data-testid="hf-mirror-url"
                />
                <Button
                  variant="subtle"
                  size="sm"
                  onClick={() => save(url, 'manual')}
                  loading={saving}
                  disabled={saving}
                  data-testid="hf-mirror-save"
                >
                  {t('common.save')}
                </Button>
              </>
            }
          />
        </>
      )}
    </SettingsSection>
  );
}
