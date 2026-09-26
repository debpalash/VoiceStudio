/**
 * Settings → System → LLM Providers (v0.3.8; test/UX/i18n pass for v0.3.9).
 *
 * One place to configure the high-quality LLM that powers Cinematic and
 * Autofit translation (fitting each line to its segment's time budget). Every
 * provider is OpenAI-compatible, so the same client drives all of them — pick
 * one, paste its key, mark it active. Keys are stored ENCRYPTED on the backend
 * and never returned to the UI.
 *
 * Endpoints (loopback-only):
 *   GET  /api/settings/llm-providers
 *     → {active, providers:[{id,display_name,local,needs_account,signup_url,
 *         notes,base_url,model,has_key,key_from_env,configured}]}
 *   PUT  /api/settings/llm-providers/{id}  {api_key?,base_url?,model?,account_id?,make_active?}
 *   POST /api/settings/llm-providers/active {provider}
 *   POST /api/settings/llm-providers/{id}/test
 *     → {ok, model?, reply?, latency_ms?, kind?, detail?}   (kind: config|auth|
 *       not_found|rate_limit|network|error → localized message below)
 *   GET  /api/settings/llm-providers/{id}/models → {ok, models[], kind?}
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Brain, ExternalLink } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { apiJson, apiFetch, apiPost } from '../../api/client';
import { useAppStore } from '../../store';
import { SettingsSection, SettingRow, SettingsInput } from './primitives';
import { Button, Badge, Select } from '../../ui';

const MODELS_DATALIST_ID = 'llm-provider-models-list';

export default function LLMProvidersPanel() {
  const { t } = useTranslation();
  const [providers, setProviders] = useState([]);
  const [active, setActive] = useState(null);
  const [editing, setEditing] = useState('');
  const [fields, setFields] = useState({ base_url: '', model: '', api_key: '', account_id: '' });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState(null);
  const [models, setModels] = useState(null); // null = not fetched; [] = fetched, none
  const [modelsTruncated, setModelsTruncated] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);
  const [error, setError] = useState(null);
  // True after a save/Test whose provider is still NOT the active one — the
  // save persisted fine but translation keeps using another provider, so be
  // honest about it instead of letting a green Test read as "done" (#963).
  const [savedInactive, setSavedInactive] = useState(false);

  const current = useMemo(
    () => providers.find((p) => p.id === editing) || null,
    [providers, editing],
  );

  // Failure kinds from /test and /models → localized, actionable messages.
  const kindMessage = useCallback(
    (res) => {
      const byKind = {
        config: t('settings.llmp_err_config'),
        auth: t('settings.llmp_err_auth'),
        not_found: t('settings.llmp_err_not_found'),
        rate_limit: t('settings.llmp_err_rate_limit'),
        network: t('settings.llmp_err_network'),
      };
      return byKind[res?.kind] || res?.detail || t('settings.llmp_err_error');
    },
    [t],
  );

  const populate = useCallback((list, id) => {
    const p = list.find((x) => x.id === id);
    if (!p) return;
    // base_url/model/account prefill with the resolved value so the user edits
    // from a sane default; api_key is never echoed (only the has_key flag
    // comes back). account_id round-trips so a saved Cloudflare id is visible.
    setFields({
      base_url: p.base_url || '',
      model: p.model || '',
      api_key: '',
      account_id: p.account_id || '',
    });
    setTest(null);
    setModels(null);
    setModelsTruncated(false);
    setSavedInactive(false);
  }, []);

  const refresh = useCallback(
    async (keepEditing) => {
      setError(null);
      try {
        const data = await apiJson('/api/settings/llm-providers');
        setProviders(data.providers || []);
        setActive(data.active || null);
        const pick =
          keepEditing ||
          data.active ||
          data.providers?.find((p) => p.configured)?.id ||
          data.providers?.[0]?.id ||
          '';
        setEditing(pick);
        populate(data.providers || [], pick);
        return data;
      } catch (e) {
        setError(e?.message || t('settings.llmp_load_failed'));
      }
    },
    [populate, t],
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onSelect = (id) => {
    setEditing(id);
    populate(providers, id);
  };

  // Returns true when the PUT (and refresh) succeeded — Test / Fetch models
  // gate on it so they never probe the previously-stored config after a
  // failed save (which could show a green "Test ok" beside a save error).
  const save = async (makeActive) => {
    if (!current) return false;
    setSaving(true);
    setError(null);
    try {
      const body = {
        base_url: fields.base_url.trim(),
        model: fields.model.trim(),
        make_active: !!makeActive,
      };
      if (fields.api_key) body.api_key = fields.api_key; // only when typed
      if (current.needs_account) body.account_id = fields.account_id.trim();
      await apiFetch(`/api/settings/llm-providers/${current.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await refresh(current.id);
      // Saved but another provider stays active → say so (populate() above
      // cleared the previous notice). Suppress while LLM_DEFAULT_PROVIDER
      // pins the choice — the env banner already explains and the suggested
      // button is disabled.
      setSavedInactive(Boolean(data) && data.active !== current.id && !current.active_from_env);
      return true;
    } catch (e) {
      setError(e?.message || t('settings.llmp_save_failed'));
      return false;
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    if (!current) return;
    setTesting(true);
    setTest(null);
    setError(null);
    try {
      // Save first so the probe sees the just-typed key/URL. If the save
      // failed, stop: probing the stale stored config would contradict the
      // save error with a misleading green badge.
      if (!(await save(false))) return;
      const res = await apiPost(`/api/settings/llm-providers/${current.id}/test`);
      setTest(res);
    } catch (e) {
      setTest({ ok: false, detail: e?.message || t('settings.llmp_err_error') });
    } finally {
      setTesting(false);
    }
  };

  const fetchModels = async () => {
    if (!current) return;
    setLoadingModels(true);
    setError(null);
    try {
      // Save non-key fields first so the probe uses the just-typed base URL;
      // abort on a failed save (same stale-config trap as runTest).
      if (!(await save(false))) return;
      const res = await apiJson(`/api/settings/llm-providers/${current.id}/models`);
      if (res.ok) {
        setModels(res.models || []);
        setModelsTruncated(!!res.truncated);
      } else {
        setModels([]);
        setModelsTruncated(false);
        setTest({ ok: false, kind: res.kind, detail: res.detail });
      }
    } catch (e) {
      setModels([]);
      setTest({ ok: false, detail: e?.message || t('settings.llmp_err_error') });
    } finally {
      setLoadingModels(false);
    }
  };

  if (!providers.length) {
    // A failed initial GET used to dead-end here (nothing re-runs refresh
    // without a remount) — the Retry button is the way back in.
    return (
      <SettingsSection
        icon={Brain}
        title={t('settings.llm_providers')}
        description={t('settings.llmp_desc')}
      >
        {error && (
          <div className="perfpanel__error" role="alert">
            <span className="mr-[8px]">{error}</span>
            <Button
              variant="subtle"
              size="sm"
              onClick={() => refresh()}
              data-testid="llm-provider-retry"
            >
              {t('settings.retry', { defaultValue: 'Retry' })}
            </Button>
          </div>
        )}
      </SettingsSection>
    );
  }

  const isActive = current && active === current.id;

  return (
    <SettingsSection
      icon={Brain}
      title={t('settings.llm_providers')}
      description={t('settings.llmp_desc')}
    >
      {/* Backlink half of the catalogue↔providers door: the provider picked
          here is what the catalogue's LLM family actually calls through. */}
      <p className="m-0 text-[length:var(--text-xs)] text-[color:var(--chrome-fg-muted)]">
        {t('settings.llmp_catalogue_note')}{' '}
        <button
          type="button"
          className="cursor-pointer border-0 bg-transparent p-0 text-[length:var(--text-xs)] text-[color:var(--chrome-accent)] underline-offset-2 hover:underline"
          onClick={() => useAppStore.getState().openCatalogue?.({ family: 'llm' })}
          data-testid="llmp-open-catalogue"
        >
          {t('settings.llmp_catalogue_link')}
        </button>
      </p>
      <SettingRow
        title={t('settings.llmp_provider')}
        hint={t('settings.llmp_provider_hint')}
        control={
          <Select
            value={editing}
            onChange={(e) => onSelect(e.target.value)}
            data-testid="llm-provider-select"
          >
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.display_name}
                {p.local ? ` · ${t('settings.llmp_local_tag')}` : ''}
                {p.configured ? ' ✓' : ''}
                {active === p.id ? ` (${t('settings.llmp_active_badge')})` : ''}
              </option>
            ))}
          </Select>
        }
      />

      {current && (
        <>
          {(current.notes || current.signup_url) && (
            <SettingRow
              title={t('settings.llmp_about')}
              control={
                <div className="flex flex-col gap-[4px] min-w-0">
                  {current.notes && <span className="text-[12px] opacity-70">{current.notes}</span>}
                  {current.signup_url && (
                    <a
                      href={current.signup_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-[12px] inline-flex items-center gap-[4px] opacity-80 hover:opacity-100"
                    >
                      {t('settings.llmp_get_key')} <ExternalLink size={12} />
                    </a>
                  )}
                </div>
              }
            />
          )}

          {current.needs_account && (
            <SettingRow
              title={t('settings.llmp_account_id')}
              note={current.account_from_env ? t('settings.llmp_env_override') : undefined}
              control={
                <SettingsInput
                  mono
                  type="text"
                  value={fields.account_id}
                  onChange={(e) => setFields((f) => ({ ...f, account_id: e.target.value }))}
                  placeholder={t('settings.llmp_account_placeholder')}
                  disabled={current.account_from_env}
                  data-testid="llm-account-id"
                />
              }
            />
          )}

          {!current.local && (
            <SettingRow
              title={t('settings.llmp_api_key')}
              control={
                <SettingsInput
                  mono
                  type="password"
                  value={fields.api_key}
                  onChange={(e) => setFields((f) => ({ ...f, api_key: e.target.value }))}
                  placeholder={
                    current.key_from_env
                      ? t('settings.llmp_key_env')
                      : current.has_key
                        ? t('settings.llmp_key_stored')
                        : t('settings.llmp_key_paste')
                  }
                  disabled={current.key_from_env}
                  data-testid="llm-provider-key"
                />
              }
            />
          )}

          <SettingRow
            title={t('settings.llmp_base_url')}
            note={current.base_url_from_env ? t('settings.llmp_env_override') : undefined}
            control={
              <SettingsInput
                mono
                type="text"
                value={fields.base_url}
                onChange={(e) => setFields((f) => ({ ...f, base_url: e.target.value }))}
                placeholder="https://api.provider.com/v1"
                disabled={current.base_url_from_env}
                data-testid="llm-provider-base-url"
              />
            }
          />
          <SettingRow
            title={t('settings.llmp_model')}
            note={current.model_from_env ? t('settings.llmp_env_override') : undefined}
            hint={
              models?.length
                ? modelsTruncated
                  ? t('settings.llmp_models_truncated', { count: models.length })
                  : t('settings.llmp_models_loaded', { count: models.length })
                : undefined
            }
            control={
              <div className="flex items-center gap-[8px] min-w-0">
                <SettingsInput
                  mono
                  type="text"
                  value={fields.model}
                  onChange={(e) => setFields((f) => ({ ...f, model: e.target.value }))}
                  placeholder={t('settings.llmp_model_placeholder')}
                  list={models?.length ? MODELS_DATALIST_ID : undefined}
                  disabled={current.model_from_env}
                  data-testid="llm-provider-model"
                />
                <Button
                  variant="subtle"
                  size="sm"
                  onClick={fetchModels}
                  loading={loadingModels}
                  disabled={saving || testing || loadingModels}
                  data-testid="llm-provider-models"
                >
                  {t('settings.llmp_fetch_models')}
                </Button>
                {models?.length ? (
                  <datalist id={MODELS_DATALIST_ID}>
                    {models.map((m) => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                ) : null}
              </div>
            }
          />

          {error && (
            <div className="perfpanel__error" role="alert">
              {error}
            </div>
          )}

          {current.active_from_env && (
            <div
              role="status"
              data-testid="llm-active-env-banner"
              className="text-[length:var(--text-xs)] text-[color:var(--chrome-fg-dim)] leading-[1.5] py-[var(--space-2)]"
            >
              {t('settings.llmp_active_env_pin')}
            </div>
          )}

          <SettingRow
            title={t('settings.llmp_status')}
            control={
              <div className="flex flex-wrap items-center gap-[8px]">
                <Button
                  variant="subtle"
                  size="sm"
                  onClick={() => save(false)}
                  loading={saving}
                  disabled={saving || testing}
                  data-testid="llm-provider-save"
                >
                  {t('settings.llmp_save')}
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => save(true)}
                  loading={saving}
                  disabled={saving || testing || current.active_from_env}
                  data-testid="llm-provider-activate"
                >
                  {isActive ? t('settings.llmp_save_keep') : t('settings.llmp_save_active')}
                </Button>
                <Button
                  variant="subtle"
                  size="sm"
                  onClick={runTest}
                  loading={testing}
                  disabled={saving || testing}
                  data-testid="llm-provider-test"
                >
                  {t('settings.llmp_test')}
                </Button>
                {isActive && (
                  <Badge tone="success" dot role="status">
                    {t('settings.llmp_active_badge')}
                  </Badge>
                )}
                {test && (
                  <Badge tone={test.ok ? 'success' : 'warn'} role="status">
                    {test.ok
                      ? t('settings.llmp_test_ok', {
                          model: test.model || '',
                          ms: test.latency_ms ?? '—',
                        })
                      : kindMessage(test)}
                  </Badge>
                )}
              </div>
            }
          />

          {savedInactive && (
            <div
              role="status"
              data-testid="llm-not-active-notice"
              className="text-[length:var(--text-xs)] text-[color:var(--chrome-fg-dim)] leading-[1.5] py-[var(--space-2)]"
            >
              {t('settings.llmp_saved_not_active')}
            </div>
          )}
        </>
      )}
    </SettingsSection>
  );
}
