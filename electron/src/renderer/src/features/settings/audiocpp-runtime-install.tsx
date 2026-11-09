import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DownloadIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson, describeError } from '@/lib/api/client';
import { queryKeys } from '@/lib/query';
import { SettingsActionError } from './settings-action-error';

/** TTS engines served by the managed audio.cpp runtime. */
export const AUDIOCPP_TTS_ENGINES: ReadonlySet<string> = new Set(['audiocpp', 'audiocpp-dots']);

interface RuntimeStatus {
  installed: boolean;
  supported: boolean;
  install_allowed?: boolean;
  job?: { state: string; progress: number; error?: string | null };
}

/**
 * Install button for the shared audio.cpp runtime on an audio.cpp TTS engine
 * row. Renders nothing once the runtime is installed or when this host has no
 * prebuilt runtime. Shares its query with the diarisation list.
 */
export function AudioCppRuntimeInstall() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [starting, setStarting] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const status = useQuery({
    queryKey: ['audiocpp-runtime-install'],
    queryFn: () => apiJson<RuntimeStatus>('/engines/audiocpp/runtime/install/status'),
    refetchInterval: (state) => (state.state.data?.job?.state === 'running' ? 1_500 : 10_000),
  });
  useEffect(() => {
    if (status.data?.installed) void client.invalidateQueries({ queryKey: queryKeys.engines });
  }, [client, status.data?.installed]);
  if (!status.data || status.data.installed || !status.data.supported) return null;
  const running = starting || status.data.job?.state === 'running';
  const blocked = status.data.install_allowed === false;
  const jobError = status.data.job?.state === 'error' ? status.data.job.error : null;
  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        size="sm"
        variant="outline"
        disabled={running || blocked}
        onClick={async () => {
          if (blocked) return;
          setStarting(true);
          setFailed(null);
          try {
            await apiJson('/engines/audiocpp/runtime/install', { method: 'POST' });
            await status.refetch();
          } catch (error) {
            setFailed(describeError(error));
          } finally {
            setStarting(false);
          }
        }}
      >
        <DownloadIcon />
        {running
          ? `${t('modelMaintenance.installing')} ${Math.round((status.data.job?.progress || 0) * 100)}%`
          : t('modelMaintenance.install')}
      </Button>
      {blocked && (
        <p className="max-w-sm text-xs text-muted-foreground">
          {t('engines.localInstallRequired')}
        </p>
      )}
      {(failed || jobError) && (
        <SettingsActionError
          className="max-w-sm text-left"
          title={t('modelMaintenance.failed')}
          detail={failed || jobError || ''}
          onDismiss={() => setFailed(null)}
        />
      )}
    </div>
  );
}
