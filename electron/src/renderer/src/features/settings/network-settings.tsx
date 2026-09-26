import { NetworkIcon } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Link } from '@tanstack/react-router';
import { Button, buttonVariants } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { apiJson } from '@/lib/api/client';
import { SettingsSection, SettingsRow } from './settings-layout';
import { useSettingsAction } from './use-settings-action';
import { saveProxyPreference } from '@shared/utils/networkPreferences';
export function NetworkSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ['system-info'],
    queryFn: ({ signal }) => apiJson<{ proxy_url?: string }>('/system/info', { signal }),
  });
  const dirty = useRef(false);
  const [value, setValue] = useState('');
  const action = useSettingsAction();
  useEffect(() => {
    if (!dirty.current) setValue(query.data?.proxy_url || '');
  }, [query.data]);
  const save = (next: string) =>
    action.run(async () => {
      await saveProxyPreference(next, (key, value) =>
        apiJson('/system/set-env', { method: 'POST', body: JSON.stringify({ key, value }) }),
      );
      setValue(next.trim());
      dirty.current = false;
      await client.invalidateQueries({ queryKey: ['system-info'] });
    });
  return (
    <SettingsSection icon={NetworkIcon} title={t('settings.network')}>
      <SettingsRow
        id="network-proxy"
        title={t('settings.proxy')}
        description={t('settings.proxy_desc')}
      >
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (value.trim()) void save(value);
          }}
        >
          <Input
            aria-label={t('settings.proxy')}
            value={value}
            disabled={action.busy || query.isPending}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => {
              dirty.current = true;
              setValue(event.target.value);
              action.reset();
            }}
          />
          <Button type="submit" size="sm" disabled={action.busy || !value.trim()}>
            {t('common.save')}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={action.busy || (!value && !query.data?.proxy_url)}
            onClick={() => void save('')}
          >
            {t('settings.proxy_clear')}
          </Button>
        </form>
      </SettingsRow>
      {(query.isError || action.error) && (
        <p role="alert" className="p-4 text-sm text-destructive">
          {t('common.error')}{' '}
          <Button variant="ghost" size="sm" onClick={() => void query.refetch()}>
            {t('common.retry')}
          </Button>
        </p>
      )}
      {action.saved && (
        <p role="status" className="p-4 text-sm text-muted-foreground">
          {t(value ? 'settings.proxy_saved' : 'settings.proxy_cleared')}
        </p>
      )}
      <SettingsRow id="network-media" title={t('settings.ffmpeg')}>
        <Link to="/settings/media" className={buttonVariants({ variant: 'ghost', size: 'sm' })}>
          {t('settings.audio_tools')}
        </Link>
      </SettingsRow>
    </SettingsSection>
  );
}
