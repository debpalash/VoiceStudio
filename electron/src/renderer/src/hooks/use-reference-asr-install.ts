import { useQueryClient } from '@tanstack/react-query';
import { useModelInstall } from '@/hooks/use-model-install';

/**
 * One-click install of the speech-to-text model the backend recommended for
 * this machine, through the shared install state machine. Nothing downloads
 * until `start` is called; `onInstalled` fires once the job finishes.
 */
export function useReferenceAsrInstall(repoId: string | undefined, onInstalled: () => void) {
  const client = useQueryClient();
  const install = useModelInstall(repoId, {
    onReady: () => {
      void client.invalidateQueries({ queryKey: ['model-catalogue'] }).catch(() => undefined);
      onInstalled();
    },
  });
  const failed = install.view?.stage === 'failed';
  return {
    ...install,
    installing: install.active,
    progress: install.view?.pct ?? null,
    error: failed ? (install.view?.reason ?? install.view?.detail ?? '') : null,
  };
}
