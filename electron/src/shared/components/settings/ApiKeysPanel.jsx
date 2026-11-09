/**
 * Settings → API Keys panel (Wave 2 AUTH-03 UI half).
 *
 * Consumes the Wave 1 resolver state endpoint at
 *   GET    /api/settings/hf-token/state
 *   POST   /api/settings/hf-token
 *   DELETE /api/settings/hf-token?also_clear_hf_cli={bool}
 *
 * Renders one row per source (App / Env / HF CLI) with set/unset indicator,
 * masked token preview, whoami username + green check on success, and an
 * "Active" badge on whichever row is currently serving the cascade.
 *
 * Threat T-02-02: the panel never displays the full token. The masked
 * value comes from the resolver state endpoint; the full token only
 * crosses the IPC boundary on Save (POST) and is cleared from local
 * state on success.
 *
 * Note: the GET + POST go through `apiJson` / `apiPost` from
 * `../../api/client` (the canonical base-URL site). The DELETE uses raw
 * fetch with the same `API` base so query params can be appended cleanly.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, KeyRound, RefreshCw, Save, Trash2, XCircle } from 'lucide-react';
import { apiJson, apiPost, apiFetch, API } from '../../api/client';
import { SettingsSection, InfoHint } from './primitives';

// i18n keys + English fallbacks for each token source, keyed by source id.
const SOURCE_TEXT = {
  app: {
    label: ['settings.hf_source_app_label', 'In-app store (encrypted, recommended)'],
    help: [
      'settings.hf_source_app_help',
      'Kept encrypted on this computer. You can set or remove it here.',
    ],
  },
  env: {
    label: ['settings.hf_source_env_label', 'Environment variable'],
    help: [
      'settings.hf_source_env_help',
      'Taken from HF_TOKEN in the shell that launched the app. It cannot be edited here.',
    ],
  },
  'hf-cli': {
    label: ['settings.hf_source_cli_label', 'Hugging Face CLI'],
    help: [
      'settings.hf_source_cli_help',
      'Saved when you signed in with the Hugging Face command-line tool. It cannot be edited here.',
    ],
  },
};

const BTN =
  'inline-flex items-center gap-[var(--space-2)] rounded-[var(--chrome-radius-pill)] px-[var(--space-4)] py-[var(--space-2)] text-[length:var(--text-sm)] disabled:cursor-not-allowed disabled:opacity-50';
const BTN_SUBTLE = `${BTN} bg-[var(--chrome-hover-bg)] text-[var(--chrome-fg)]`;
const BTN_DANGER = `${BTN} bg-[var(--chrome-severity-err)] text-[var(--chrome-bg)]`;

export default function ApiKeysPanel() {
  const { t } = useTranslation();
  const [state, setState] = useState(null);
  const [loading, setLoading] = useState(false);
  const [tokenInput, setTokenInput] = useState('');
  const [saving, setSaving] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [alsoClearCli, setAlsoClearCli] = useState(false);
  const [error, setError] = useState('');

  const tt = useCallback(([key, fallback]) => t(key, { defaultValue: fallback }), [t]);

  const refresh = useCallback(
    async ({ fresh = false } = {}) => {
      setLoading(true);
      setError('');
      try {
        const path = `/api/settings/hf-token/state${fresh ? '?fresh=1' : ''}`;
        setState(await apiJson(path));
      } catch (e) {
        setError(
          e?.message ||
            t('settings.hf_token_load_error', {
              defaultValue: 'Could not read the token status.',
            }),
        );
      } finally {
        setLoading(false);
      }
    },
    [t],
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  const save = async () => {
    const token = tokenInput.trim();
    if (saving || !token) return;
    setSaving(true);
    setError('');
    try {
      await apiPost('/api/settings/hf-token', { token });
      setTokenInput('');
      await refresh();
    } catch (e) {
      setError(
        e?.message ||
          t('settings.hf_token_save_error', { defaultValue: 'Could not store the token.' }),
      );
    } finally {
      setSaving(false);
    }
  };

  const clear = async () => {
    setSaving(true);
    setError('');
    try {
      const query = alsoClearCli ? '?also_clear_hf_cli=true' : '';
      await apiFetch(`${API}/api/settings/hf-token${query}`, { method: 'DELETE' });
      setConfirmOpen(false);
      setAlsoClearCli(false);
      await refresh();
    } catch {
      // The raw server message may echo paths; show only the generic text.
      setError(t('common.error', { defaultValue: 'Something went wrong' }));
    } finally {
      setSaving(false);
    }
  };

  const closeConfirm = () => {
    setConfirmOpen(false);
    setAlsoClearCli(false);
  };

  const testNowLabel = t('settings.hf_token_test_now', { defaultValue: 'Test now' });

  const verification = (row) => {
    if (row.whoami_ok == null) {
      return (
        <span className="text-[var(--chrome-fg-muted)]">
          {t('settings.hf_token_not_checked', { defaultValue: 'Not checked yet' })}
        </span>
      );
    }
    if (row.whoami_ok) {
      return (
        <span className="inline-flex items-center gap-[var(--space-1)] text-[var(--color-success)]">
          <CheckCircle2 size={12} aria-hidden="true" />
          {row.whoami_user || t('settings.hf_token_verified', { defaultValue: 'confirmed' })}
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-[var(--space-1)] text-[var(--chrome-severity-err)]">
        <XCircle size={12} aria-hidden="true" />
        {t('settings.hf_token_whoami_failed', { defaultValue: 'account check failed' })}
      </span>
    );
  };

  const renderRow = (row) => {
    const text = SOURCE_TEXT[row.source];
    const active = state.active === row.source;
    const setLabel = t('settings.hf_token_set', { defaultValue: 'present' });
    return (
      <div
        key={row.source}
        role="listitem"
        data-source={row.source}
        className={`apikeys-row${active ? ' apikeys-row--active' : ''} flex flex-col gap-[var(--space-3)] rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-accent-bg)] p-[var(--space-4)]`}
      >
        <div className="flex flex-wrap items-center gap-[var(--space-3)]">
          <span className="font-semibold">{text ? tt(text.label) : row.source}</span>
          {text && <InfoHint>{tt(text.help)}</InfoHint>}
          {active && (
            <span className="apikeys-badge apikeys-badge--active rounded-[var(--chrome-radius-pill)] bg-[color-mix(in_srgb,var(--color-success)_14%,transparent)] px-[var(--space-2)] text-[length:var(--text-xs)] text-[var(--color-success)]">
              {t('settings.hf_token_active', { defaultValue: 'In use' })}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-[var(--space-3)] text-[length:var(--text-sm)]">
          {row.set ? (
            <>
              <span
                aria-label={setLabel}
                className="inline-flex items-center gap-[var(--space-1)] text-[var(--color-success)]"
              >
                <CheckCircle2 size={12} aria-hidden="true" />
                {setLabel}
              </span>
              {row.masked && <code className="font-mono">{row.masked}</code>}
              {verification(row)}
            </>
          ) : (
            <span className="inline-flex items-center gap-[var(--space-1)] text-[var(--chrome-severity-warn)]">
              <XCircle size={12} aria-hidden="true" />
              {t('settings.hf_token_not_set', { defaultValue: 'missing' })}
            </span>
          )}
        </div>
        {row.source === 'app' && (
          <div className="flex flex-wrap items-center gap-[var(--space-3)]">
            <input
              type="password"
              className="min-w-0 flex-1 rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-bg)] px-[var(--space-3)] py-[var(--space-2)] font-mono text-[length:var(--text-sm)]"
              placeholder="hf_…"
              aria-label={t('settings.hf_token_input', {
                defaultValue: 'Hugging Face access token',
              })}
              autoComplete="off"
              spellCheck={false}
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') save();
              }}
            />
            <button
              type="button"
              className={BTN_SUBTLE}
              onClick={save}
              disabled={saving || !tokenInput.trim()}
            >
              <Save size={12} aria-hidden="true" />
              {t('common.save', { defaultValue: 'Save' })}
            </button>
            {row.set && (
              <button
                type="button"
                className={BTN_SUBTLE}
                onClick={() => setConfirmOpen(true)}
                disabled={saving}
              >
                <Trash2 size={12} aria-hidden="true" />
                {t('settings.hf_token_clear_short', { defaultValue: 'Clear' })}
              </button>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <SettingsSection
      className="apikeys-panel"
      icon={KeyRound}
      title={t('settings.hf_token_title', { defaultValue: 'Hugging Face token' })}
      description={t('settings.hf_token_desc', {
        defaultValue: 'Looked up in three places, first match wins: App, then Env, then HF CLI.',
      })}
      actions={
        <button
          type="button"
          className={BTN_SUBTLE}
          aria-label={testNowLabel}
          title={t('settings.hf_token_test_now_title', {
            defaultValue: 'Check every source against Hugging Face again',
          })}
          onClick={() => refresh({ fresh: true })}
          disabled={loading}
        >
          <RefreshCw size={12} aria-hidden="true" />
          {testNowLabel}
        </button>
      }
    >
      <div className="flex flex-col gap-[var(--space-4)]">
        {error && (
          <div
            role="alert"
            className="rounded-[var(--chrome-radius-pill)] bg-[color-mix(in_srgb,var(--chrome-severity-err)_10%,transparent)] p-[var(--space-3)] text-[length:var(--text-sm)] text-[var(--chrome-severity-err)]"
          >
            {error}
          </div>
        )}

        {state === null ? (
          <div
            role="status"
            data-testid="hf-token-loading"
            className="text-[length:var(--text-sm)] text-[var(--chrome-fg-muted)]"
          >
            {loading
              ? t('settings.hf_token_checking', { defaultValue: 'Looking for tokens…' })
              : null}
          </div>
        ) : (
          <div
            role="list"
            aria-label={t('settings.hf_token_sources', { defaultValue: 'Token sources' })}
            className="flex flex-col gap-[var(--space-3)]"
          >
            {(state.sources ?? []).map(renderRow)}
          </div>
        )}

        {confirmOpen && (
          <div
            role="dialog"
            aria-label={t('settings.hf_token_clear_dialog', { defaultValue: 'Remove token' })}
            className="flex flex-col gap-[var(--space-3)] rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-hover-bg)] p-[var(--space-4)]"
          >
            <p className="m-0">
              {t('settings.hf_token_clear_confirm', {
                defaultValue: 'Remove the token kept in the app store?',
              })}
            </p>
            <label className="inline-flex items-center gap-[var(--space-2)] text-[length:var(--text-sm)]">
              <input
                type="checkbox"
                checked={alsoClearCli}
                onChange={(e) => setAlsoClearCli(e.target.checked)}
              />
              {t('settings.hf_token_also_clear', {
                defaultValue: 'Also delete the token files the Hugging Face CLI saved',
              })}
            </label>
            <div className="flex flex-wrap justify-end gap-[var(--space-3)]">
              <button type="button" className={BTN_SUBTLE} onClick={closeConfirm}>
                {t('settings.cancel', { defaultValue: 'Cancel' })}
              </button>
              <button type="button" className={BTN_DANGER} onClick={clear} disabled={saving}>
                <Trash2 size={12} aria-hidden="true" />
                {t('settings.hf_token_clear_btn', { defaultValue: 'Remove token' })}
              </button>
            </div>
          </div>
        )}
      </div>
    </SettingsSection>
  );
}
