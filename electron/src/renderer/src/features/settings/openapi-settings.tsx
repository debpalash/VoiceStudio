import {
  CheckCircle2Icon,
  CopyIcon,
  ExternalLinkIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
} from 'lucide-react';
import { lazy, Suspense } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { getBridge } from '@/components/bridge';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';
import { apiJson, apiPath, describeError } from '@/lib/api/client';
import { SettingsActionError } from './settings-action-error';

const ScalarApiReference = lazy(() => import('./scalar-api-reference'));

function LoadingReference() {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-72 items-center justify-center gap-2 text-sm text-muted-foreground">
      <LoaderCircleIcon className="size-4 animate-spin" />
      {t('common.loading')}
    </div>
  );
}

export function OpenApiSettings() {
  const { t } = useTranslation();
  const backend = useBackendStatus();
  const baseUrl = backend.baseUrl.replace(/\/+$/, '');
  const displayedBaseUrl = baseUrl || window.location.origin;
  const rawUrl = `${displayedBaseUrl}/openapi.json`;
  const spec = useQuery({
    queryKey: ['openapi-spec', rawUrl],
    queryFn: async ({ signal }) => {
      const data = await apiJson<Record<string, unknown>>('/openapi.json', { signal });
      return {
        ...data,
        servers: [{ url: `${window.location.origin}${apiPath('')}` }],
      };
    },
    staleTime: 60_000,
  });

  const copyUrl = async () => {
    try {
      await navigator.clipboard.writeText(rawUrl);
      toast.success(t('openapi.copied'));
    } catch {
      toast.error(t('openapi.copy_failed'));
    }
  };

  const openRaw = () => {
    const bridge = getBridge();
    if (bridge)
      void bridge.files.openExternal(rawUrl).catch((error) => toast.error(describeError(error)));
    else window.open(rawUrl, '_blank', 'noopener,noreferrer');
  };

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col">
      <div className="flex min-h-12 shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-border/50 bg-muted/15 px-4 py-2">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <CheckCircle2Icon
            aria-hidden="true"
            className={
              isBackendReachable(backend.stage)
                ? 'size-4 shrink-0 text-emerald-500'
                : 'size-4 shrink-0 text-muted-foreground'
            }
          />
          <span className="shrink-0 text-xs font-medium">{t(`backend.${backend.stage}`)}</span>
          <code
            title={rawUrl}
            className="min-w-0 truncate rounded-md border border-border/50 bg-background/50 px-2 py-1 font-mono text-[11px] text-muted-foreground"
          >
            {displayedBaseUrl}
          </code>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button
            variant="ghost"
            size="sm"
            aria-label={t('common.refresh')}
            title={t('common.refresh')}
            disabled={spec.isFetching}
            onClick={() => void spec.refetch()}
          >
            <RefreshCwIcon className={spec.isFetching ? 'animate-spin' : undefined} />
            <span className="hidden @2xl:inline">{t('common.refresh')}</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label={t('openapi.copy_url')}
            title={t('openapi.copy_url')}
            onClick={() => void copyUrl()}
          >
            <CopyIcon />
            <span className="hidden @2xl:inline">{t('openapi.copy_url')}</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label={t('openapi.open_raw')}
            title={t('openapi.open_raw')}
            onClick={openRaw}
          >
            <ExternalLinkIcon />
            <span className="hidden @2xl:inline">{t('openapi.open_raw')}</span>
          </Button>
        </div>
      </div>
      {spec.isPending ? (
        <main className="min-h-0 flex-1">
          <LoadingReference />
        </main>
      ) : spec.isError ? (
        <main className="min-h-0 flex-1 p-4">
          <SettingsActionError
            title={t('openapi.unreachable_title')}
            detail={`${t('openapi.unreachable_body', { url: rawUrl })} ${describeError(spec.error)}`}
            action={
              <Button variant="ghost" size="xs" onClick={() => void spec.refetch()}>
                <RefreshCwIcon />
                {t('common.retry')}
              </Button>
            }
          />
        </main>
      ) : (
        <div className="isolate min-h-0 min-w-0 flex-1 overflow-auto bg-background [&_.scalar-app]:min-h-full">
          <Suspense fallback={<LoadingReference />}>
            <ScalarApiReference spec={spec.data} />
          </Suspense>
        </div>
      )}
    </div>
  );
}
