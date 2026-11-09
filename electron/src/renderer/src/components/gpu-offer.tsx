import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { XIcon, ZapIcon } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { ApiError, apiJson } from '@/lib/api/client';
import { queryKeys } from '@/lib/query';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';

const PATH = '/engines/omnivoice-gguf/gpu-offer';
export const gpuOfferKey = ['gguf-gpu-offer'] as const;

export interface GpuOfferStatus {
  eligible: boolean;
  blocked_by_environment?: boolean;
  answer: 'accepted' | 'declined' | null;
  active: boolean;
  show: boolean;
  licence_accepted: boolean;
  gpu_name: string | null;
  device: string | null;
  shared_memory: boolean;
  effective_vram_gb: number | null;
  quant: string | null;
  download_mb: number;
}

type DownloadStatus = 'started' | 'already_running' | 'already_installed' | 'failed' | 'busy';
type GpuOfferAnswer = GpuOfferStatus & { download: { status: DownloadStatus } | null };

/** The GPU offer state plus the accept / decline actions shared by the
 * one-time banner and the Settings › Compute device row. */
export function useGpuOffer() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const backend = useBackendStatus();
  const [busy, setBusy] = useState(false);
  const [needsLicence, setNeedsLicence] = useState(false);
  const [failed, setFailed] = useState(false);
  const query = useQuery({
    queryKey: gpuOfferKey,
    queryFn: ({ signal }) => apiJson<GpuOfferStatus>(PATH, { signal }),
    enabled: isBackendReachable(backend.stage),
    retry: false,
  });
  const answer = async (accept: boolean) => {
    if (busy) return false;
    setBusy(true);
    setFailed(false);
    try {
      const next = await apiJson<GpuOfferAnswer>(PATH, {
        method: 'POST',
        body: JSON.stringify({ accept }),
      });
      client.setQueryData(gpuOfferKey, next);
      setNeedsLicence(false);
      if (accept && !next.active) {
        // The weights could not start downloading, so nothing was switched.
        setFailed(true);
        return false;
      }
      if (accept) {
        void client.invalidateQueries({ queryKey: queryKeys.engines });
        void client.invalidateQueries({ queryKey: ['gguf-quants'] });
        void client.invalidateQueries({ queryKey: ['gpu-report'] });
        toast.success(
          t(
            next.download?.status === 'already_installed'
              ? 'gpuOffer.switched'
              : 'gpuOffer.switched_downloading',
            {
              gpu: next.gpu_name ?? '',
              quant: next.quant ?? '',
            },
          ),
        );
      }
      return true;
    } catch (error) {
      // A 403 licence error already opened the app's licence dialog.
      if (error instanceof ApiError && error.status === 403) setNeedsLicence(true);
      else setFailed(true);
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { status: query.data, busy, needsLicence, failed, answer };
}

export function GpuOfferText({ status }: { status: GpuOfferStatus }) {
  const { t } = useTranslation();
  return (
    <>
      <span className="block text-sm leading-6 text-muted-foreground">
        {t(status.shared_memory ? 'gpuOffer.body_shared' : 'gpuOffer.body', {
          gpu: status.gpu_name ?? '',
          quant: status.quant ?? '',
        })}{' '}
        {status.download_mb
          ? t('gpuOffer.download', { size: status.download_mb })
          : t('gpuOffer.no_download')}
      </span>
      {!status.licence_accepted && (
        <span className="block text-xs leading-5 text-muted-foreground">
          {t('gpuOffer.licence')}
        </span>
      )}
    </>
  );
}

/** One-time, non-modal prompt: use the AMD/Intel GPU through GGUF + Vulkan? */
export function GpuOfferBanner() {
  const { t } = useTranslation();
  const [hidden, setHidden] = useState(false);
  const offer = useGpuOffer();
  const status = offer.status;
  if (hidden || !status?.show) return null;
  const decline = async () => {
    // Stay visible when the answer could not be saved, so the error shows
    // and the user can try again.
    if (await offer.answer(false)) setHidden(true);
  };
  return (
    <aside
      aria-label={t('gpuOffer.title')}
      className="glass-panel fixed right-4 bottom-4 z-[70] flex w-[min(460px,calc(100vw-32px))] items-start gap-3 rounded-xl border border-border/60 bg-popover/90 p-4 shadow-xl backdrop-blur-2xl"
    >
      <ZapIcon className="mt-1 size-4 shrink-0 text-primary" aria-hidden="true" />
      <div className="min-w-0 flex-1 space-y-2">
        <h2 className="text-sm font-semibold">{t('gpuOffer.title')}</h2>
        <GpuOfferText status={status} />
        <div className="flex flex-wrap gap-2 pt-1">
          <Button
            type="button"
            size="sm"
            disabled={offer.busy}
            onClick={async () => {
              if (await offer.answer(true)) setHidden(true);
            }}
          >
            <ZapIcon />
            {t('gpuOffer.accept')}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={offer.busy}
            onClick={() => void decline()}
          >
            {t('gpuOffer.decline')}
          </Button>
        </div>
        {offer.needsLicence && (
          <p role="status" className="text-xs text-muted-foreground">
            {t('gpuOffer.licence_then_retry')}
          </p>
        )}
        {offer.failed && (
          <p role="alert" className="text-xs text-destructive">
            {t('common.error')}
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          {t('gpuOffer.later', { section: t('settings.gpu_report_title') })}
        </p>
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        disabled={offer.busy}
        aria-label={t('gpuOffer.decline')}
        title={t('gpuOffer.decline')}
        onClick={() => void decline()}
      >
        <XIcon />
      </Button>
    </aside>
  );
}
