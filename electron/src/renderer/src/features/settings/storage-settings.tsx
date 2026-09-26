import { HardDriveIcon } from 'lucide-react';
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Link } from '@tanstack/react-router';
import { FolderOpenIcon } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { apiJson } from '@/lib/api/client';
import { getBridge } from '@/components/bridge';
import { SettingsSection } from './settings-layout';
import { HistoryRetention } from './privacy-settings';
import { useSettingsAction } from './use-settings-action';
import { fmtBytes } from '@shared/components/settings/models/format';
import { ModelsDirectorySettings } from './models-directory-settings';
import { ResetSettings } from './reset-settings';
import { DataDirectorySettings } from './data-directory-settings';
import { UninstallSettings } from './uninstall-settings';
import {
  warningText,
  type StorageWarning,
} from '@shared/utils/storageWarnings';
interface Item {
  id: string;
  path?: string;
  bytes: number;
  complete?: boolean;
  exists?: boolean;
  items?: { name: string; bytes: number }[];
  children?: Item[];
}
interface Report {
  categories: Item[];
  volumes: {
    path: string;
    total_bytes: number;
    used_bytes: number;
    free_bytes: number;
    used_percent: number;
    roots: string[];
  }[];
  warnings: StorageWarning[];
}
export function StorageSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const action = useSettingsAction();
  const [confirm, setConfirm] = useState(false);
  const [freed, setFreed] = useState<number | null>(null);
  const query = useQuery({
    queryKey: ['storage-report'],
    queryFn: ({ signal }) => apiJson<Report>('/api/settings/storage', { signal }),
    staleTime: 300000,
  });
  const backup = useQuery({
    queryKey: ['db-backup'],
    queryFn: ({ signal }) =>
      apiJson<{
        available: boolean;
        latest?: { path: string; created_at: number; size_bytes: number } | null;
      }>('/api/settings/db-backup', { signal }),
  });
  const refresh = () =>
    action.run(async () => {
      client.setQueryData(['storage-report'], await apiJson('/api/settings/storage?refresh=1'));
    }, false);
  const open = (path: string) =>
    action.run(async () => {
      const bridge = getBridge();
      if (bridge) await bridge.files.revealPath(path);
      else await apiJson('/export/reveal', { method: 'POST', body: JSON.stringify({ path }) });
    }, false);
  const clear = () =>
    action.run(async () => {
      setFreed(null);
      const result = await apiJson<{ freed_bytes: number; errors?: unknown[] }>(
        '/api/settings/storage/temp/clear',
        { method: 'POST' },
      );
      setConfirm(false);
      client.setQueryData(['storage-report'], await apiJson('/api/settings/storage?refresh=1'));
      if (result.errors?.length) throw new Error('Incomplete cleanup');
      setFreed(result.freed_bytes);
    });
  return (
    <>
      <DataDirectorySettings />
      <ModelsDirectorySettings />
      <SettingsSection icon={HardDriveIcon} title={t('settings.storage_usage')}>
        <div className="flex items-center justify-between gap-3 p-4">
          <p className="text-sm text-muted-foreground">{t('settings.storage_desc')}</p>
          <Button
            size="sm"
            variant="ghost"
            disabled={action.busy || query.isFetching}
            onClick={() => void refresh()}
          >
            {t('settings.storage_refresh')}
          </Button>
        </div>
        {query.isPending && (
          <p role="status" className="p-4 text-sm">
            {t('common.loading')}
          </p>
        )}
        {(query.isError || action.error) && (
          <p role="alert" className="p-4 text-sm text-destructive">
            {t('common.error')}{' '}
            <Button size="sm" variant="ghost" onClick={() => void query.refetch()}>
              {t('common.retry')}
            </Button>
          </p>
        )}
        {query.data?.warnings.map((warning, index) => (
          <p key={index} role="alert" className="p-4 text-sm text-destructive">
            {warningText(t, warning)}
          </p>
        ))}
        {query.data?.volumes.map((volume) => (
          <div key={volume.path} className="space-y-2 p-4">
            <div className="flex flex-wrap justify-between gap-2 text-sm">
              <span>{volume.path}</span>
              <span className="tabular-nums">
                {fmtBytes(volume.used_bytes)} / {fmtBytes(volume.total_bytes)}
              </span>
            </div>
            <div
              role="meter"
              aria-label={volume.path}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={volume.used_percent}
              className="h-1.5 overflow-hidden rounded-full bg-muted"
            >
              <div
                className="h-full bg-primary"
                style={{ width: Math.max(0, Math.min(100, volume.used_percent)) + '%' }}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              {t('settings.storage_volume_free', {
                free: fmtBytes(volume.free_bytes),
                total: fmtBytes(volume.total_bytes),
              })}
            </p>
          </div>
        ))}
        {query.data?.categories.map((category) => (
          <div key={category.id} className="space-y-2 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-medium">{t('settings.storage_cat_' + category.id)}</h3>
              <span className="text-sm tabular-nums">
                {fmtBytes(category.bytes)}{' '}
                {category.complete === false && (
                  <span className="text-xs text-muted-foreground">
                    {t('settings.storage_partial')}
                  </span>
                )}
              </span>
            </div>
            {category.path && (
              <p className="break-all text-xs text-muted-foreground">{category.path}</p>
            )}
            <div className="flex flex-wrap gap-2">
              {category.path && category.exists !== false && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={action.busy}
                  onClick={() => void open(category.path!)}
                >
                  <FolderOpenIcon />
                  {t('settings.storage_open_folder')}
                </Button>
              )}
              {category.id === 'hf_cache' && (
                <Link
                  to="/settings/models"
                  className={buttonVariants({ variant: 'ghost', size: 'sm' })}
                >
                  {t('settings.storage_manage_models')}
                </Link>
              )}
              {category.id === 'temp' && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={action.busy}
                  onClick={() => {
                    setFreed(null);
                    setConfirm(true);
                  }}
                >
                  {t('settings.storage_clear_temp')}
                </Button>
              )}
            </div>
            {category.items?.length || category.children?.length ? (
              <details className="text-sm">
                <summary className="cursor-pointer text-muted-foreground">
                  {t(category.id === 'hf_cache' ? 'settings.storage_top_models' : 'common.details')}
                </summary>
                <ul className="mt-2 space-y-2">
                  {category.items?.map((item) => (
                    <li key={item.name} className="flex justify-between gap-3 text-xs">
                      <span className="break-all">{item.name}</span>
                      <span className="shrink-0 tabular-nums">{fmtBytes(item.bytes)}</span>
                    </li>
                  ))}
                  {category.children?.map((child) => (
                    <li
                      key={child.id}
                      className="flex flex-wrap items-center justify-between gap-3 text-xs"
                    >
                      <span>{t('settings.storage_child_' + child.id)}</span>
                      <span className="tabular-nums">
                        {fmtBytes(child.bytes)}{' '}
                        {child.complete === false && t('settings.storage_partial')}
                      </span>
                      {child.id === 'logs' && (
                        <Link
                          to="/settings/logs"
                          className={buttonVariants({ variant: 'ghost', size: 'xs' })}
                        >
                          {t('settings.logs')}
                        </Link>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
        ))}
        {confirm && (
          <div className="space-y-3 p-4">
            <p className="text-sm">{t('settings.storage_clear_temp_confirm')}</p>
            <div className="flex gap-2">
              <Button
                variant="destructive"
                size="sm"
                disabled={action.busy}
                onClick={() => void clear()}
              >
                {t('common.confirm')}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={action.busy}
                onClick={() => setConfirm(false)}
              >
                {t('common.cancel')}
              </Button>
            </div>
          </div>
        )}
        {freed != null && (
          <p role="status" className="p-4 text-sm text-muted-foreground">
            {t('settings.storage_temp_cleared', { freed: fmtBytes(freed) })}
          </p>
        )}
      </SettingsSection>
      <SettingsSection icon={HardDriveIcon} title={t('updates.backup_line')}>
        <div className="space-y-2 p-4 text-sm text-muted-foreground">
          {backup.isPending ? (
            t('common.loading')
          ) : backup.isError ? (
            <p role="alert">
              {t('common.error')}{' '}
              <Button size="sm" variant="ghost" onClick={() => void backup.refetch()}>
                {t('common.retry')}
              </Button>
            </p>
          ) : backup.data?.available && backup.data.latest ? (
            <>
              <p>
                {t('updates.backup_latest', {
                  when: new Date(backup.data.latest.created_at * 1000).toLocaleString(),
                })}
              </p>
              <p className="break-all text-xs">{backup.data.latest.path}</p>
              <span>{fmtBytes(backup.data.latest.size_bytes)}</span>
            </>
          ) : (
            t('updates.backup_none')
          )}
        </div>
      </SettingsSection>
      <HistoryRetention />
      <ResetSettings />
      <UninstallSettings />
    </>
  );
}
