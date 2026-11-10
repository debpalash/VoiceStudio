import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { TriangleAlertIcon } from 'lucide-react';
import type {
  BackendStatus,
  SetupConnectionCheck as ConnectionCheckResult,
} from '../../../preload/index.d';
import { getBridge } from './bridge';
import { Button } from './ui/button';

/** Five minutes without download progress before setup offers its support tools. */
const STALL_HELP_SECONDS = 300;

export function setupStalled(status: BackendStatus): boolean {
  return (
    status.stage === 'installing' &&
    status.setupProgress?.downloadsComplete === false &&
    status.setupProgress.stalled === true &&
    (status.setupProgress.stalledSeconds ?? 0) >= STALL_HELP_SECONDS
  );
}

/** A failed or stalled install: the prefilled report, Discord and the connection check apply. */
export function setupNeedsHelp(status: BackendStatus): boolean {
  return (status.stage === 'setup_required' && Boolean(status.message)) || setupStalled(status);
}

/** The setup log names a TLS or connection failure (a proxy or antivirus, usually). */
export function setupConnectionError(status: BackendStatus): boolean {
  return (
    status.stage === 'setup_required' &&
    Boolean(status.message) &&
    /cannot decrypt|error (?:reading a body|decoding response body)|connection (?:reset|timed out)/i.test(
      status.logTail.join('\n'),
    )
  );
}

/** Saves the setup log locally. Works before Python exists and never uploads anything. */
export function SetupLogButton() {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const bridge = getBridge();
  return (
    <>
      <Button
        size="sm"
        variant="ghost"
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
      {failed && (
        <span role="alert" className="self-center px-2 text-xs text-destructive">
          {t('common.error')}
        </span>
      )}
    </>
  );
}

/** A user-clicked, bounded sample download from the affected package host. */
export function SetupConnectionCheck() {
  const { t } = useTranslation();
  const [checking, setChecking] = useState(false);
  const [failed, setFailed] = useState(false);
  const [result, setResult] = useState<ConnectionCheckResult | null>(null);
  const bridge = getBridge();
  return (
    <div className="space-y-1">
      <Button
        size="sm"
        variant="outline"
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
      {failed && (
        <p role="alert" className="text-xs text-destructive">
          {t('common.error')}
        </p>
      )}
    </div>
  );
}

/** After five minutes without download progress. It does not cancel the install. */
export function SetupStallNotice({ status }: { status: BackendStatus }) {
  const { t } = useTranslation();
  if (!setupStalled(status)) return null;
  return (
    <div
      role="status"
      data-testid="setup-stall-help"
      className="flex items-start gap-3 rounded-xl border border-warning/30 bg-warning/5 p-3"
    >
      <TriangleAlertIcon className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
      <div className="min-w-0 flex-1 space-y-2">
        <div>
          <p className="text-sm font-medium">{t('setupHelp.title')}</p>
          <p className="text-xs text-muted-foreground">{t('setupHelp.stalled_hint')}</p>
        </div>
        <SetupConnectionCheck />
      </div>
    </div>
  );
}

/** Main offers the smaller CPU build when the CUDA download failed or stuck. */
export function SetupCpuFallback({ status }: { status: BackendStatus }) {
  const { t } = useTranslation();
  const [switching, setSwitching] = useState(false);
  const [failed, setFailed] = useState(false);
  const bridge = getBridge();
  if (!status.cpuSetupAvailable) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <Button
        size="sm"
        variant="outline"
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
      <p className="min-w-0 flex-1 basis-48 text-xs text-muted-foreground">
        {t('setupHelp.use_cpu_hint')}
      </p>
      {failed && (
        <p role="alert" className="w-full text-xs text-destructive">
          {t('common.error')}
        </p>
      )}
    </div>
  );
}
