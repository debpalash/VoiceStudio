import { ShieldCheckIcon } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Link } from '@tanstack/react-router';
import { Button, buttonVariants } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { apiJson } from '@/lib/api/client';
import { SettingsSection, SettingsRow } from './settings-layout';
import { useSettingsAction } from './use-settings-action';
import { translatorPrivacy } from '@shared/utils/translatorPrivacy';
export function PrivacySettings({ showAnalytics = true }: { showAnalytics?: boolean } = {}) {
  const { t } = useTranslation();
  const info = useQuery({
    queryKey: ['system-info'],
    queryFn: ({ signal }) => apiJson<{ translate_provider?: string }>('/system/info', { signal }),
  });
  return (
    <>
      <SettingsSection icon={ShieldCheckIcon} title={t('settings.privacy')}>
        <SettingsRow id="privacy-translator" title={t('about.translator')}>
          <span className="text-sm text-muted-foreground">
            {t(
              'privacy.translator_' +
                translatorPrivacy(info.isError ? undefined : info.data?.translate_provider),
              { provider: info.data?.translate_provider },
            )}
          </span>
          <Link
            to="/settings/models/$family"
            params={{ family: 'translation' }}
            className={buttonVariants({ variant: 'ghost', size: 'sm' })}
          >
            {t('settings.translation')}
          </Link>
        </SettingsRow>
        <PrivacyToggle kind="watermark" />
        {showAnalytics && <PrivacyToggle kind="analytics" />}
      </SettingsSection>
      <HistoryRetention />
    </>
  );
}
interface PrivacyState {
  audioseal_available?: boolean;
  invisible_enabled?: boolean;
  available?: boolean;
  opted_in?: boolean;
  enabled?: boolean;
}
function PrivacyToggle({ kind }: { kind: 'watermark' | 'analytics' }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const action = useSettingsAction();
  const watermark = kind === 'watermark';
  const key = ['privacy', kind];
  const path = watermark ? '/watermark/status' : '/api/settings/analytics';
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => apiJson<PrivacyState>(path, { signal }),
  });
  const state = query.data;
  if (state && !(watermark ? state.audioseal_available : state.available)) return null;
  return (
    <div>
      <SettingsRow
        id={'privacy-' + kind}
        title={t('privacy.' + kind + '_title')}
        description={t('privacy.' + kind + '_subtitle')}
      >
        <Switch
          aria-label={t('privacy.' + kind + '_title')}
          checked={!!(watermark ? state?.invisible_enabled : state?.opted_in)}
          disabled={!state || action.busy}
          onCheckedChange={(enabled) =>
            void action.run(async () => {
              const next = await apiJson<PrivacyState>(
                watermark ? '/watermark/settings?invisible=' + enabled : path,
                watermark
                  ? { method: 'POST' }
                  : { method: 'PUT', body: JSON.stringify({ enabled }) },
              );
              client.setQueryData(key, (prior: PrivacyState | undefined) => ({
                ...prior,
                ...next,
              }));
            })
          }
        />
      </SettingsRow>
      {(query.isError || action.error) && (
        <p role="alert" className="px-4 pb-3 text-sm text-destructive">
          {t('common.error')}{' '}
          <Button size="sm" variant="ghost" onClick={() => void query.refetch()}>
            {t('common.retry')}
          </Button>
        </p>
      )}
    </div>
  );
}
export function HistoryRetention() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const action = useSettingsAction();
  const dirty = useRef(false);
  const [value, setValue] = useState('');
  const [confirm, setConfirm] = useState(false);
  const query = useQuery({
    queryKey: ['history-retention'],
    queryFn: ({ signal }) =>
      apiJson<{ cap: number; default: number }>('/api/settings/history-retention', { signal }),
  });
  useEffect(() => {
    if (!dirty.current && query.data) setValue(String(query.data.cap));
  }, [query.data]);
  const cap = Number(value);
  const valid = value.trim() !== '' && Number.isInteger(cap) && cap >= 0 && cap <= 100000;
  const save = () =>
    action.run(async () => {
      const state = await apiJson('/api/settings/history-retention', {
        method: 'PUT',
        body: JSON.stringify({ cap }),
      });
      client.setQueryData(['history-retention'], state);
      setConfirm(false);
      dirty.current = false;
    });
  const submit = () => {
    if (!valid || !query.data || action.busy) return;
    if (cap > 0 && (query.data.cap === 0 || cap < query.data.cap)) setConfirm(true);
    else void save();
  };
  return (
    <SettingsSection icon={ShieldCheckIcon} title={t('settings.history_retention')}>
      <p className="p-4 text-sm text-muted-foreground">{t('settings.history_retention_help')}</p>
      <SettingsRow
        id="retention-cap"
        title={t('settings.history_retention_cap')}
        description={t('settings.history_retention_cap_hint', { count: query.data?.default })}
      >
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <Input
            className="w-28 tabular-nums"
            type="number"
            min="0"
            max="100000"
            step="1"
            aria-label={t('settings.history_retention_cap')}
            value={value}
            disabled={action.busy || !query.data}
            onChange={(event) => {
              dirty.current = true;
              setValue(event.target.value);
              setConfirm(false);
              action.reset();
            }}
          />
          <Button type="submit" size="sm" disabled={!query.data || action.busy || !valid}>
            {t('common.save')}
          </Button>
        </form>
      </SettingsRow>
      {confirm && (
        <div className="space-y-3 p-4">
          <p className="text-sm">{t('settings.history_retention_help')}</p>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="destructive"
              disabled={action.busy || !valid}
              onClick={() => void save()}
            >
              {t('common.confirm')}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={action.busy}
              onClick={() => setConfirm(false)}
            >
              {t('common.cancel')}
            </Button>
          </div>
        </div>
      )}
      {(query.isError || action.error) && (
        <p role="alert" className="p-4 text-sm text-destructive">
          {t('common.error')}{' '}
          <Button size="sm" variant="ghost" onClick={() => void query.refetch()}>
            {t('common.retry')}
          </Button>
        </p>
      )}
      {action.saved && (
        <p role="status" className="p-4 text-sm text-muted-foreground">
          {t('settings.history_retention_saved')}
        </p>
      )}
    </SettingsSection>
  );
}
