import { FolderOpenIcon, HardDriveIcon } from 'lucide-react';
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { getBridge } from '@/components/bridge';
import { Button } from '@/components/ui/button';
import { apiJson, describeError } from '@/lib/api/client';
import { SettingsRow, SettingsSection } from './settings-layout';

interface ModelsDirectoryState {
  configured: string | null;
  effective?: string;
  default: string;
  restart_required: boolean;
}

const queryKey = ['models-directory'] as const;

export function ModelsDirectorySettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) =>
      apiJson<ModelsDirectoryState>('/api/settings/storage/models-dir', { signal }),
  });
  const bridge = getBridge();

  const save = async (reset: boolean) => {
    if (busy || !bridge?.files.authorizeModelsDirectory) return;
    setBusy(true);
    setError('');
    try {
      const selected = await bridge.files.authorizeModelsDirectory(reset);
      if (!selected) return;
      const next = await apiJson<ModelsDirectoryState>('/api/settings/storage/models-dir', {
        method: 'PUT',
        body: JSON.stringify({ authorization: selected.authorization }),
      });
      client.setQueryData(queryKey, next);
      await client.invalidateQueries({ queryKey });
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setBusy(false);
    }
  };

  const state = query.data;
  const path = state?.configured || state?.effective || state?.default;
  return (
    <SettingsSection icon={HardDriveIcon} title={t('settings.storage_cat_hf_cache')}>
      <SettingsRow
        id="models-directory"
        title={t('settings.storage_cat_hf_cache')}
        description={t('settings.models_dir_desc')}
      >
        <code
          title={path}
          className="min-w-0 max-w-full flex-1 truncate rounded-md border border-border/60 bg-muted/35 px-2.5 py-1.5 text-xs text-muted-foreground @2xl:max-w-[32rem]"
        >
          {query.isPending ? t('common.loading') : path}
        </code>
        {bridge?.files.authorizeModelsDirectory && (
          <>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || query.isPending}
              onClick={() => void save(false)}
            >
              <FolderOpenIcon />
              {t('settings.models_dir_choose')}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy || query.isPending || !state?.configured}
              onClick={() => void save(true)}
            >
              {t('preferences.reset')}
            </Button>
          </>
        )}
      </SettingsRow>
      {(query.isError || error) && (
        <div role="alert" className="flex items-center gap-2 px-4 py-3 text-sm text-destructive">
          <span className="min-w-0 flex-1 break-words">{error || t('common.error')}</span>
          {query.isError && (
            <Button size="sm" variant="ghost" onClick={() => void query.refetch()}>
              {t('common.retry')}
            </Button>
          )}
        </div>
      )}
      {state?.restart_required && (
        <p role="status" className="px-4 py-3 text-xs text-muted-foreground">
          {t('settings.compute_device_restart')}
        </p>
      )}
    </SettingsSection>
  );
}
