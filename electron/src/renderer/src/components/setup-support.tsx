import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DISCORD_URL } from '@shared/utils/contactLinks';
import type { BackendStatus, SetupConnectionCheck } from '../../../preload/index.d';
import { getBridge } from './bridge';
import { Button } from './ui/button';
import { ExternalLink } from './external-link';
import { ReportBug } from './report-bug';

/** Setup support works before Python exists and never sends logs automatically. */
export function SetupSupport({ status }: { status: BackendStatus }) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [failed, setFailed] = useState(false);
  const [result, setResult] = useState<SetupConnectionCheck | null>(null);
  const setupFailed = status.stage === 'setup_required' && Boolean(status.message);
  const stalled =
    status.stage === 'installing' &&
    status.setupProgress?.downloadsComplete === false &&
    status.setupProgress.stalled === true &&
    (status.setupProgress.stalledSeconds ?? 0) >= 300;
  const help = setupFailed || stalled;
  const connectionError =
    setupFailed &&
    /cannot decrypt|error (?:reading a body|decoding response body)|connection (?:reset|timed out)/i.test(
      status.logTail.join('\n'),
    );
  const bridge = getBridge();
  return (
    <section className="w-full space-y-3 text-sm" aria-label={t('setupHelp.download_log')}>
      {stalled && (
        <div
          role="status"
          className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-left"
        >
          <p className="font-medium">{t('setupHelp.title')}</p>
          <p className="mt-1 text-xs text-muted-foreground">{t('setupHelp.stalled_hint')}</p>
        </div>
      )}
      {connectionError && <p role="alert">{t('setupHelp.connection_error')}</p>}
      <div className="flex flex-wrap justify-center gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={saving || !bridge}
          onClick={async () => {
            setSaving(true);
            setFailed(false);
            try {
              const data = await bridge!.backend.getSetupDiagnostics();
              await bridge!.files.saveData({
                data: new TextEncoder().encode(data),
                suggestedName: 'voicestudio-setup.log',
              });
            } catch {
              setFailed(true);
            } finally {
              setSaving(false);
            }
          }}
        >
          {t('setupHelp.download_log')}
        </Button>
        {help && (
          <>
            <ReportBug error={status.message || t('setupHelp.title')} setup />
            <ExternalLink href={DISCORD_URL}>{t('setupHelp.discord')}</ExternalLink>
          </>
        )}
      </div>
      {status.cpuSetupAvailable && (
        <div className="space-y-1">
          <Button
            size="sm"
            disabled={switching || !bridge}
            onClick={async () => {
              setSwitching(true);
              setFailed(false);
              try {
                await bridge!.backend.setupRuntimeOnCpu();
              } catch {
                setFailed(true);
              } finally {
                setSwitching(false);
              }
            }}
          >
            {t('setupHelp.use_cpu')}
          </Button>
          <p className="text-xs text-muted-foreground">{t('setupHelp.use_cpu_hint')}</p>
        </div>
      )}
      {help && (
        <div className="space-y-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={checking || !bridge}
            onClick={async () => {
              setChecking(true);
              setFailed(false);
              setResult(null);
              try {
                setResult(await bridge!.backend.checkSetupConnection());
              } catch {
                setFailed(true);
              } finally {
                setChecking(false);
              }
            }}
          >
            {t(checking ? 'setupHelp.checking' : 'setupHelp.check_connection')}
          </Button>
          <p className="text-xs text-muted-foreground">{t('setupHelp.check_hint')}</p>
          {result && (
            <p role="status" className="text-xs">
              {result.status === 'ok'
                ? t('setupHelp.check_ok', {
                    host: result.host,
                    rate: Math.round((result.bytesPerSecond ?? 0) / 1024),
                  })
                : t(
                    result.status === 'unavailable'
                      ? 'setupHelp.check_unavailable'
                      : 'setupHelp.check_failed',
                    { host: result.host ?? t('common.unknown') },
                  )}
            </p>
          )}
        </div>
      )}
      {failed && (
        <p role="alert" className="text-xs text-destructive">
          {t('common.error')}
        </p>
      )}
    </section>
  );
}
