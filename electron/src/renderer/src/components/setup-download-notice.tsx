import { TriangleAlertIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { BackendStatus } from '../../../preload/index.d';

/**
 * Names where the first-run download comes from and says when it has stopped
 * receiving data, so a slow or blocked host is not mistaken for a frozen app (#3411).
 */
export function SetupDownloadNotice({ progress }: { progress: BackendStatus['setupProgress'] }) {
  const { t } = useTranslation();
  if (!progress || progress.downloadsComplete !== false) return null;
  const host = progress.downloadHost;
  if (progress.stalled) {
    const seconds = progress.stalledSeconds ?? 0;
    return (
      <div
        role="status"
        data-testid="setup-download-stalled"
        className="mx-auto flex max-w-sm items-start gap-2 pt-1 text-left text-sm"
      >
        <TriangleAlertIcon className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
        <div className="space-y-0.5">
          <p className="font-medium">
            {host
              ? t('bootstrap.download_stalled', { host, seconds })
              : t('bootstrap.download_stalled_generic', { seconds })}
          </p>
          <p className="text-xs text-muted-foreground">{t('bootstrap.download_stalled_hint')}</p>
        </div>
      </div>
    );
  }
  if (!host) return null;
  return (
    <p data-testid="setup-download-source" className="font-mono text-xs text-muted-foreground">
      {t('bootstrap.download_source', { host })}
    </p>
  );
}
