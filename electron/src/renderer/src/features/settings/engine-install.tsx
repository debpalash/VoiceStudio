import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { DownloadIcon, Trash2Icon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson, describeError } from '@/lib/api/client';
import { queryKeys } from '@/lib/query';
import { ConfirmDialog } from '@/features/clone/confirm-dialog';
import { SettingsActionError } from './settings-action-error';

export function EngineInstall({
  id,
  name,
  repairOnly = false,
  onRemoved,
}: {
  id: string;
  /** Shown in the delete confirmation; defaults to the engine id. */
  name?: string;
  repairOnly?: boolean;
  onRemoved?: () => void;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [starting, setStarting] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [dismissedFailure, setDismissedFailure] = useState(false);
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [removeFailed, setRemoveFailed] = useState<string | null>(null);
  const status = useQuery({
    queryKey: ['engine-install', id],
    queryFn: () =>
      apiJson<{
        installed: boolean;
        repair_available?: boolean;
        /** The app installed it, so the app may delete it (never a user's own clone). */
        managed?: boolean;
        install_allowed?: boolean;
        job: null | {
          state: string;
          steps: { name?: string; state: string }[];
          error?: string | null;
          remediation?: string | null;
        };
      }>(`/engines/sidecar/${encodeURIComponent(id)}/install/status`),
    refetchInterval: (query) =>
      query.state.data?.job?.state === 'running' ? 1500 : repairOnly ? false : 10_000,
  });
  useEffect(() => {
    if (status.data?.installed) void client.invalidateQueries({ queryKey: queryKeys.engines });
  }, [status.data?.installed, status.data?.job?.state, client]);
  const running = starting || status.data?.job?.state === 'running';
  // Every app-installed engine can be deleted again (#4490): its folder holds
  // the venv and often gigabytes of weights that nothing else lists.
  const removable = Boolean(status.data?.installed && status.data?.managed) && !running;
  const installShown =
    !repairOnly ||
    Boolean(status.data?.repair_available) ||
    running ||
    Boolean(failed) ||
    status.data?.job?.state === 'failed';
  if (!installShown && !removable && !removeFailed) return null;
  const remove = async () => {
    setRemoving(true);
    setRemoveFailed(null);
    try {
      await apiJson(`/engines/sidecar/${encodeURIComponent(id)}/install`, { method: 'DELETE' });
      await status.refetch();
      void client.invalidateQueries({ queryKey: queryKeys.engines });
      onRemoved?.();
    } catch (error) {
      setRemoveFailed(describeError(error));
    } finally {
      setRemoving(false);
    }
  };
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex flex-wrap justify-end gap-2">
        {installShown && (
          <Button
            size="sm"
            variant="outline"
            disabled={
              running ||
              status.isPending ||
              status.isError ||
              (status.data?.installed && !status.data?.repair_available) ||
              status.data?.install_allowed === false
            }
            onClick={async () => {
              if (status.data?.install_allowed === false) return;
              setStarting(true);
              setFailed(null);
              setDismissedFailure(false);
              try {
                await apiJson(`/engines/sidecar/${encodeURIComponent(id)}/install`, {
                  method: 'POST',
                });
                await status.refetch();
              } catch (error) {
                setFailed(describeError(error));
              } finally {
                setStarting(false);
              }
            }}
          >
            <DownloadIcon />
            {t(
              running
                ? 'modelMaintenance.installing'
                : status.data?.repair_available
                  ? 'modelMaintenance.repair'
                  : 'modelMaintenance.install',
            )}
          </Button>
        )}
        {removable && (
          <Button
            size="sm"
            variant="ghost"
            disabled={removing}
            onClick={() => setConfirmingRemove(true)}
          >
            <Trash2Icon />
            {t('modelMaintenance.delete')}
          </Button>
        )}
      </div>
      <ConfirmDialog
        open={confirmingRemove}
        onOpenChange={setConfirmingRemove}
        title={t('modelMaintenance.engineDeleteTitle')}
        description={t('modelMaintenance.engineDeleteConfirm', { name: name || id })}
        confirmLabel={t('modelMaintenance.delete')}
        onConfirm={remove}
      />
      {removeFailed && (
        <SettingsActionError
          className="max-w-sm text-left"
          title={t('modelMaintenance.engineDeleteFailed')}
          detail={removeFailed}
          onDismiss={() => setRemoveFailed(null)}
        />
      )}
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
            [status.data?.job?.error, status.data?.job?.remediation].filter(Boolean).join('\n\n')
          }
          onDismiss={() => setDismissedFailure(true)}
        />
      )}
    </div>
  );
}
