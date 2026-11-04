import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { DownloadIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson, describeError } from '@/lib/api/client';
import { queryKeys } from '@/lib/query';
import { SettingsActionError } from './settings-action-error';

export function EngineInstall({ id }: { id: string }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [starting, setStarting] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [dismissedFailure, setDismissedFailure] = useState(false);
  const status = useQuery({
    queryKey: ['engine-install', id],
    queryFn: () =>
      apiJson<{
        installed: boolean;
        install_allowed?: boolean;
        job: null | {
          state: string;
          steps: { name?: string; state: string }[];
          error?: string | null;
          remediation?: string | null;
        };
      }>(`/engines/sidecar/${encodeURIComponent(id)}/install/status`),
    refetchInterval: (query) => (query.state.data?.job?.state === 'running' ? 1500 : 10_000),
  });
  useEffect(() => {
    if (status.data?.installed) void client.invalidateQueries({ queryKey: queryKeys.engines });
  }, [status.data?.installed, client]);
  const running = starting || status.data?.job?.state === 'running';
  const jobError = status.data?.job?.error;
  const jobErrorMessage =
    id === 'voxcpm2' && jobError === 'windows_rocm_voxcpm2_layout'
      ? t('engines.voxcpm2LayoutError')
      : jobError;
  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        size="sm"
        variant="outline"
        disabled={
          running ||
          status.isPending ||
          status.isError ||
          status.data?.installed ||
          status.data?.install_allowed === false
        }
        onClick={async () => {
          if (status.data?.install_allowed === false) return;
          setStarting(true);
          setFailed(null);
          setDismissedFailure(false);
          try {
            await apiJson(`/engines/sidecar/${encodeURIComponent(id)}/install`, { method: 'POST' });
            await status.refetch();
          } catch (error) {
            setFailed(describeError(error));
          } finally {
            setStarting(false);
          }
        }}
      >
        <DownloadIcon />
        {t(running ? 'modelMaintenance.installing' : 'modelMaintenance.install')}
      </Button>
      {status.data?.install_allowed === false && (
        <p className="max-w-sm text-xs text-muted-foreground">
          {t('engines.localInstallRequired')}
        </p>
      )}
      {!dismissedFailure && (failed || status.isError || status.data?.job?.state === 'failed') && (
        <SettingsActionError
          className="max-w-sm text-left"
          title={t('modelMaintenance.failed')}
          detail={
            failed ||
            (status.isError ? describeError(status.error) : '') ||
            [jobErrorMessage, status.data?.job?.remediation].filter(Boolean).join('\n\n')
          }
          onDismiss={() => setDismissedFailure(true)}
        />
      )}
    </div>
  );
}
