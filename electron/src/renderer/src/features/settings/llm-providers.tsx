import { BrainIcon, CheckCircle2Icon, ListFilterIcon, PlugZapIcon, SaveIcon } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ApiError, apiJson } from '@/lib/api/client';
import { queryKeys } from '@/lib/query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
// @ts-expect-error Shared JSX component has no declaration file.
import SearchableSelect from '@shared/components/SearchableSelect';
import { ExternalLink } from '@/components/external-link';
import { SettingsRowsSkeleton, SettingsSection, SettingsRow } from './settings-layout';
const endpoint = '/api/settings/llm-providers';
export interface Provider {
  configured: boolean;
  id: string;
  display_name: string;
  local: boolean;
  needs_account: boolean;
  base_url: string;
  model: string;
  account_id?: string;
  signup_url?: string | null;
  notes?: string | null;
  has_key: boolean;
  has_api_key?: boolean;
  key_from_env: boolean;
  base_url_from_env: boolean;
  model_from_env: boolean;
  account_from_env?: boolean;
  active_from_env: boolean;
  activation_blocked?: boolean;
  transport?: 'openai' | 'sdk' | 'cli';
  supports_model_listing?: boolean;
  allow_plain_http?: boolean;
  supports_plain_http?: boolean;
}
interface Result {
  ok: boolean;
  kind?: string;
  model?: string;
  latency_ms?: number;
  models?: string[];
  truncated?: boolean;
}
export function useLlmProviderCatalogue() {
  return useQuery({
    queryKey: ['llm-providers'],
    queryFn: ({ signal }) =>
      apiJson<{
        active: string | null;
        engine_active: string;
        engine_from_env: boolean;
        providers: Provider[];
      }>(endpoint, { signal }),
  });
}
export function LlmProviders() {
  const { t } = useTranslation();
  const [selected, setSelected] = useState('');
  const [disabling, setDisabling] = useState(false);
  const [disableFailed, setDisableFailed] = useState(false);
  const client = useQueryClient();
  const query = useLlmProviderCatalogue();
  const activeProvider = query.data?.providers.find(
    (provider) => provider.id === query.data.active,
  );
  const enabled = query.data?.engine_active === 'openai-compat' && activeProvider?.configured;
  const disable = async () => {
    setDisabling(true);
    setDisableFailed(false);
    try {
      await apiJson('/engines/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ family: 'llm', backend_id: 'off' }),
      });
      await Promise.all(
        [
          ['llm-providers'],
          ['llm-skills'],
          ['translation-engines'],
          ['dictation-refinement'],
          queryKeys.engines,
        ].map((queryKey) => client.invalidateQueries({ queryKey })),
      );
    } catch {
      setDisableFailed(true);
    } finally {
      setDisabling(false);
    }
  };
  const current =
    query.data?.providers.find((provider) => provider.id === (selected || query.data.active)) ||
    query.data?.providers.find((provider) => provider.local) ||
    query.data?.providers[0];
  const providerItems =
    query.data?.providers.map((provider) => ({
      value: provider.id,
      label:
        provider.display_name +
        (provider.id === query.data.active ? ' · ' + t('settings.llmp_active_badge') : ''),
    })) ?? [];
  return (
    <SettingsSection icon={BrainIcon} title={t('settings.llm_providers')}>
      <p className="px-4 py-3 text-sm leading-5 text-muted-foreground">{t('settings.llmp_desc')}</p>
      <div>
        {query.data && (
          <SettingsRow
            id="llm-connection-status"
            title={t('settings.llmp_status')}
            description={
              enabled
                ? `${activeProvider.display_name}${activeProvider.model ? ' · ' + activeProvider.model : ''}`
                : t('settings.llmp_setup_hint')
            }
          >
            <span
              role="status"
              className={enabled ? 'text-sm text-success' : 'text-sm text-muted-foreground'}
            >
              {t(enabled ? 'settings.llmp_active_badge' : 'settings.llmskills_needs_setup')}
            </span>
            {enabled && (
              <Button
                size="sm"
                variant="outline"
                disabled={disabling || query.data.engine_from_env}
                onClick={() => void disable()}
              >
                {t('settings.llmp_disable')}
              </Button>
            )}
            {disableFailed && <p role="alert">{t('settings.llmp_save_failed')}</p>}
          </SettingsRow>
        )}
        {query.isError && (
          <div className="p-4">
            <Button variant="outline" onClick={() => void query.refetch()}>
              {t('backend.retry')}
            </Button>
          </div>
        )}
        {query.isPending && <SettingsRowsSkeleton label={t('common.loading')} rows={3} />}
        {current && (
          <SettingsRow
            id="llm-provider"
            title={t('settings.llmp_provider')}
            description={t('settings.llmp_provider_hint')}
          >
            <SearchableSelect
              options={providerItems}
              value={current.id}
              onChange={(value: string) => setSelected(value)}
              ariaLabel={t('settings.llmp_provider')}
              menuPortal
              buttonClassName="flex h-9 w-full min-w-64 items-center justify-between rounded-md border border-input px-3 text-sm"
            />
          </SettingsRow>
        )}
        {current && (current.notes || current.signup_url) && (
          <SettingsRow
            id="llm-provider-about"
            title={t('settings.llmp_about')}
            description={current.notes || undefined}
          >
            {current.signup_url && (
              <ExternalLink href={current.signup_url}>
                {t(current.local ? 'common.learn_more' : 'settings.llmp_get_key')}
              </ExternalLink>
            )}
          </SettingsRow>
        )}
        {current && (
          <ProviderForm
            key={current.id}
            provider={current}
            active={query.data?.active === current.id}
          />
        )}
      </div>
    </SettingsSection>
  );
}
function ProviderForm({ provider, active }: { provider: Provider; active: boolean }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [fields, setFields] = useState({
    base_url: provider.base_url,
    model: provider.model,
    account_id: provider.account_id || '',
    api_key: '',
  });
  const [plainHttp, setPlainHttp] = useState(Boolean(provider.allow_plain_http));
  const [busy, setBusy] = useState(false);
  const operation = useRef<AbortController | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [modelListing, setModelListing] = useState<Result | null>(null);
  useEffect(() => () => operation.current?.abort(), []);
  const change = (key: keyof typeof fields, value: string) => {
    setFields((current) => ({ ...current, [key]: value }));
    setResult(null);
    setSaved(false);
  };
  const run = async (action: 'save' | 'activate' | 'test' | 'models') => {
    if (operation.current) return;
    const controller = new AbortController();
    operation.current = controller;
    setBusy(true);
    setFailed(null);
    setResult(null);
    setSaved(false);
    let persisted = false;
    if (action === 'models') {
      setModels([]);
      setModelListing(null);
    }
    try {
      // Never probe stale saved values after a failed save. Blank key preserves it.
      const body = {
        ...(!provider.base_url_from_env ? { base_url: fields.base_url.trim() } : {}),
        ...(!provider.model_from_env ? { model: fields.model.trim() } : {}),
        ...(provider.needs_account && !provider.account_from_env
          ? { account_id: fields.account_id.trim() }
          : {}),
        ...(provider.supports_plain_http ? { allow_plain_http: plainHttp } : {}),
        ...(fields.api_key && !provider.key_from_env ? { api_key: fields.api_key } : {}),
        make_active: false,
        activate_if_unset: false,
      };
      await apiJson(endpoint + '/' + encodeURIComponent(provider.id), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      persisted = true;
      setFields((current) => ({ ...current, api_key: '' }));
      setSaved(true);
      await Promise.all([
        client.invalidateQueries({ queryKey: ['llm-providers'] }),
        client.invalidateQueries({ queryKey: ['llm-skills'] }),
        client.invalidateQueries({ queryKey: ['translation-engines'] }),
        client.invalidateQueries({ queryKey: queryKeys.engines }),
        client.invalidateQueries({ queryKey: ['sidebar-model-status'] }),
        client.invalidateQueries({ queryKey: ['dictation-refinement'] }),
      ]);
      if (action === 'test' || action === 'models' || action === 'activate') {
        const response = await apiJson<Result>(
          endpoint +
            '/' +
            encodeURIComponent(provider.id) +
            '/' +
            (action === 'activate' ? 'connect' : action),
          { method: action === 'models' ? 'GET' : 'POST', signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        if (action === 'models' && response.ok) {
          setModels(response.models || []);
          setModelListing(response);
        } else setResult(response);
        if (action === 'activate' && response.ok) {
          await Promise.all(
            [
              ['llm-providers'],
              ['llm-skills'],
              ['translation-engines'],
              ['dictation-refinement'],
              queryKeys.engines,
              ['sidebar-model-status'],
            ].map((queryKey) => client.invalidateQueries({ queryKey })),
          );
        }
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        setFailed(
          error instanceof ApiError && error.status === 400
            ? 'settings.llmp_err_config'
            : error instanceof ApiError && error.status === 409
              ? 'settings.llmp_active_env_pin'
              : persisted
                ? 'settings.llmp_err_error'
                : 'settings.llmp_save_failed',
        );
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      operation.current = null;
    }
  };
  const field = (key: keyof typeof fields, title: string, pinned: boolean, password = false) => (
    <SettingsRow
      id={'llm-' + key}
      title={t('settings.' + title)}
      description={pinned ? t('settings.llmp_env_override') : undefined}
    >
      <Input
        aria-label={t('settings.' + title)}
        type={password ? 'password' : 'text'}
        autoComplete="off"
        value={fields[key]}
        disabled={busy || pinned}
        placeholder={
          password
            ? t(
                (provider.has_api_key ?? (!provider.local && provider.has_key))
                  ? 'settings.llmp_key_stored'
                  : 'settings.llmp_key_paste',
              )
            : undefined
        }
        onChange={(event) => change(key, event.target.value)}
      />
    </SettingsRow>
  );
  const kind =
    result?.kind && ['config', 'auth', 'not_found', 'rate_limit', 'network'].includes(result.kind)
      ? result.kind
      : 'error';
  return (
    <div className="divide-y divide-border/50">
      {provider.transport === 'cli' && (
        <p className="px-4 py-3 text-sm text-muted-foreground">{t('settings.llmp_cli_help')}</p>
      )}
      {provider.transport === 'sdk' && (
        <p className="px-4 py-3 text-sm text-muted-foreground">{t('settings.llmp_sdk_help')}</p>
      )}
      {provider.needs_account &&
        field('account_id', 'llmp_account_id', Boolean(provider.account_from_env))}
      {provider.transport === 'cli' ? null : provider.local ? (
        <details className="px-4 py-3">
          <summary className="cursor-pointer text-sm text-muted-foreground">
            {t('settings.llmp_optional_key')}
          </summary>
          {field('api_key', 'llmp_api_key', provider.key_from_env, true)}
        </details>
      ) : (
        field('api_key', 'llmp_api_key', provider.key_from_env, true)
      )}
      {provider.transport !== 'cli' &&
        field('base_url', 'llmp_base_url', provider.base_url_from_env)}
      {provider.supports_plain_http && (
        <SettingsRow
          id="llm-allow_plain_http"
          title={t('settings.llmp_plain_http')}
          description={t('settings.llmp_plain_http_desc')}
        >
          <Switch
            aria-label={t('settings.llmp_plain_http')}
            checked={plainHttp}
            disabled={busy}
            onCheckedChange={(value) => {
              setPlainHttp(value);
              setResult(null);
              setSaved(false);
            }}
          />
        </SettingsRow>
      )}
      {field('model', 'llmp_model', provider.model_from_env)}
      {modelListing && (
        <p role="status" className="px-4 py-3 text-xs text-muted-foreground">
          {t(
            modelListing.truncated
              ? 'settings.llmp_models_truncated'
              : 'settings.llmp_models_loaded',
            { count: models.length },
          )}
        </p>
      )}
      {models.length > 0 && (
        <div className="max-h-48 overflow-y-auto px-4 py-3">
          <div className="flex flex-wrap gap-1">
            {models.map((model) => (
              <Button
                key={model}
                size="xs"
                variant="ghost"
                disabled={busy || provider.model_from_env}
                onClick={() => change('model', model)}
              >
                {model}
              </Button>
            ))}
          </div>
        </div>
      )}
      {(provider.active_from_env || provider.activation_blocked) && (
        <p className="px-4 py-3 text-xs text-muted-foreground">
          {t('settings.llmp_active_env_pin')}
        </p>
      )}
      <div className="flex flex-wrap gap-2 px-4 py-3">
        <Button
          size="sm"
          disabled={busy || (provider.activation_blocked ?? (provider.active_from_env && !active))}
          onClick={() => void run('activate')}
        >
          <CheckCircle2Icon />
          {t('settings.llmp_connect')}
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void run('test')}>
          <PlugZapIcon />
          {t('settings.llmp_test')}
        </Button>
        {provider.supports_model_listing !== false && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => void run('models')}>
            <ListFilterIcon />
            {t('settings.llmp_fetch_models')}
          </Button>
        )}
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void run('save')}>
          <SaveIcon />
          {t('settings.llmp_save')}
        </Button>
      </div>
      {busy && (
        <p role="status" className="px-4 py-3 text-xs">
          {t('common.loading')}
        </p>
      )}
      {saved && !active && !provider.active_from_env && (
        <p className="px-4 py-3 text-xs text-muted-foreground">
          {t('settings.llmp_saved_not_active')}
        </p>
      )}
      {failed && (
        <p role="alert" className="px-4 py-3 text-sm text-destructive">
          {t(failed)}
        </p>
      )}
      {result && (
        <p role={result.ok ? 'status' : 'alert'} className="px-4 py-3 text-sm">
          {result.ok
            ? t('settings.llmp_test_ok', { model: result.model, ms: result.latency_ms })
            : t(
                provider.transport === 'cli' && kind === 'auth'
                  ? 'settings.llmp_cli_auth'
                  : 'settings.llmp_err_' + kind,
              )}
        </p>
      )}
    </div>
  );
}
