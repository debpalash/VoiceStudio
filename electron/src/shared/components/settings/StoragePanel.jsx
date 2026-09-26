/**
 * Settings → Models tab → Models directory panel (#64).
 *
 * Lets the user choose where model weights download (the HuggingFace / Torch
 * cache). The backend persists it to the durable per-user env file as
 * OMNIVOICE_CACHE_DIR, which main.py maps to HF_HOME / HF_HUB_CACHE / TORCH_HOME
 * on the next launch — so changes apply after a restart.
 *
 * Endpoints:
 *   GET /api/settings/storage/models-dir
 *     → {configured, effective, default, restart_required}
 *   Native IPC authorizes the path, then PUT sends only the one-shot token.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { HardDrive } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import { apiJson, apiFetch } from '../../api/client';
import { SettingsSection, SettingRow, InfoHint } from './primitives';
import RestartBadge from './RestartBadge';

export default function StoragePanel() {
  const { t } = useTranslation();
  const [configured, setConfigured] = useState('');
  const [effective, setEffective] = useState('');
  const [def, setDef] = useState('');
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [restart, setRestart] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await apiJson('/api/settings/storage/models-dir');
      setConfigured(d?.configured || '');
      setEffective(d?.effective || '');
      setDef(d?.default || '');
      setInput(d?.configured || '');
    } catch (e) {
      setError(e?.message || t('settings.storage_load_failed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const save = async (reset = false) => {
    setSaving(true);
    setError(null);
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const selection = await invoke('authorize_host_path', { kind: 'models_dir', reset });
      if (!selection) return;
      const res = await apiFetch('/api/settings/storage/models-dir', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ authorization: selection.authorization }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b?.detail || `HTTP ${res.status}`);
      }
      const b = await res.json();
      setConfigured(b?.configured || '');
      setRestart(Boolean(b?.restart_required));
      toast.success(
        selection.path ? t('settings.models_dir_saved') : t('settings.models_dir_reverted'),
      );
      refresh();
    } catch (e) {
      setError(e?.message || t('settings.perf_save_failed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection
      className="storagepanel models-settings-compact !mb-[8px] !px-[10px] !py-[9px] [&>header]:!mb-[6px] [&>header]:!gap-[8px] [&>header]:!pb-[6px] [&>header_h2]:!text-[length:var(--text-md)] [&>header_[data-slot=settings-section-icon]]:!h-[24px] [&>header_[data-slot=settings-section-icon]]:!w-[24px]"
      compact
      icon={HardDrive}
      title={t('firstrun.models_dir')}
      actions={
        <>
          <RestartBadge />
          <InfoHint label={t('firstrun.models_dir')}>{t('settings.models_dir_help')}</InfoHint>
        </>
      }
    >
      {error && (
        <div
          className="mb-[var(--space-3)] text-[length:var(--text-base)] text-[var(--chrome-severity-err)]"
          role="alert"
        >
          {error}
        </div>
      )}

      <SettingRow
        align="start"
        className="!px-[6px] !py-[5px]"
        title={t('settings.storage_cat_hf_cache')}
        subtitle={t('firstrun.models_dir_desc')}
        control={
          <div className="flex w-full flex-wrap items-center gap-[5px]">
            <input
              className="box-border min-w-0 max-w-[520px] flex-[1_1_280px] rounded-[8px] [border:1px_solid_var(--chrome-border)] bg-[var(--chrome-hover-bg)] px-[8px] py-[5px] font-[family-name:var(--chrome-font-mono)] text-[length:var(--text-sm)] text-[var(--chrome-fg)] placeholder:text-[var(--chrome-fg-dim)] focus-visible:border-[var(--chrome-accent)] focus-visible:shadow-[var(--focus-ring)] focus-visible:outline-none"
              type="text"
              value={input}
              placeholder={def || '~/.cache/huggingface'}
              readOnly
              disabled={saving || loading}
              spellCheck={false}
              name="models-directory"
              autoComplete="off"
              aria-label={t('firstrun.models_dir')}
              data-testid="models-dir-input"
            />
            <button
              type="button"
              className="flex-none cursor-pointer rounded-[7px] [border:1px_solid_transparent] bg-[var(--chrome-accent)] px-[9px] py-[5px] font-sans text-[length:var(--text-sm)] text-[var(--chrome-bg)] disabled:cursor-default disabled:opacity-50"
              onClick={() => save(false)}
              disabled={saving || loading}
              data-testid="models-dir-save"
            >
              {saving ? t('common.saving') : t('common.save')}
            </button>
            <button
              type="button"
              className="flex-none cursor-pointer rounded-[7px] [border:1px_solid_var(--chrome-border)] bg-transparent px-[9px] py-[5px] font-sans text-[length:var(--text-sm)] text-[var(--chrome-fg-muted)] hover:enabled:bg-[var(--chrome-hover-bg)] hover:enabled:text-[var(--chrome-fg)] disabled:cursor-default disabled:opacity-50"
              onClick={() => {
                save(true);
              }}
              disabled={saving || loading || !configured}
              title={t('settings.models_dir_reset_hint')}
            >
              {t('settings.reset')}
            </button>
          </div>
        }
      />

      <div className="grid grid-cols-2 gap-[5px] px-[6px] pt-[5px] max-[560px]:grid-cols-1">
        <div className="flex min-w-0 items-center gap-[6px] rounded-[7px] bg-[var(--chrome-bg)] px-[8px] py-[4px]">
          <span className="shrink-0 text-[length:var(--text-xs)] text-[var(--chrome-fg-dim)]">
            {t('settings.models_dir_effective')}
          </span>
          <code className="min-w-0 flex-1 truncate text-right text-[length:var(--text-xs)] text-[var(--chrome-fg-muted)]">
            {effective || '…'}
          </code>
        </div>
        <div className="flex min-w-0 items-center gap-[6px] rounded-[7px] bg-[var(--chrome-bg)] px-[8px] py-[4px]">
          <span className="shrink-0 text-[length:var(--text-xs)] text-[var(--chrome-fg-dim)]">
            {t('settings.models_dir_configured')}
          </span>
          <code className="min-w-0 flex-1 truncate text-right text-[length:var(--text-xs)] text-[var(--chrome-fg-muted)]">
            {configured || t('settings.models_dir_default')}
          </code>
        </div>
      </div>

      {restart && (
        <p className="mx-[6px] mb-0 mt-[5px] text-[length:var(--text-xs)] text-[var(--chrome-severity-warn)]">
          {t('settings.models_dir_restart')}
        </p>
      )}
    </SettingsSection>
  );
}
