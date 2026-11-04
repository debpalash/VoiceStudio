import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { composeBugReportUrl } from '@shared/utils/bugReportDocument';
import { formatBreadcrumbs } from '@shared/utils/breadcrumbs';
import { contactAge, lastBackendContact } from '@shared/utils/backendContact';
import { clampCrashTail } from '@shared/utils/crashReport';
import { scrubText } from '@shared/utils/scrub';
import { formatHostSnapshot, withDeadline } from '@shared/utils/hostSnapshot';
import { collapseRepeatedLines, formatFailureFacts } from '@shared/utils/installFailure';
import { describeExitCode, isNativeFaultExit } from '@shared/utils/nativeExit';
import { _adaptLastRunCrash, type LastRunCrashRecord } from '@shared/utils/runCrashRecord';
import { getBackendStatusSnapshot } from '@/hooks/use-backend-status';
import { apiJson } from '@/lib/api/client';
import { router } from '@/router';
import { getBridge } from './bridge';
import { Button } from './ui/button';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Input } from './ui/input';
import { ConfirmDialog } from '@/features/clone/confirm-dialog';

/** An unpaired surrogate makes encodeURIComponent throw. */
const LONE_SURROGATE = /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g;

type KnownUpdate = { current: string; latest: string };

function reachabilitySection(
  error: Error | string | undefined,
  backend: ReturnType<typeof getBackendStatusSnapshot> | null,
  lastContact: number | null,
): string[] {
  const connection = !backend
    ? 'browser'
    : backend.remote
      ? 'remote'
      : backend.managed
        ? 'managed local'
        : 'attached local';
  const lines = [
    '## Backend reachability',
    '',
    `**Connection:** \`${connection}\``,
    lastContact == null
      ? '**Last backend response:** none this session'
      : `**Last backend response:** ${contactAge(lastContact)} before this report`,
  ];
  const apiError =
    error && typeof error === 'object' && 'status' in error
      ? (error as { status?: unknown; payload?: { detail?: unknown } | null })
      : null;
  if (apiError && typeof apiError.status === 'number') {
    lines.push(`**HTTP status:** ${apiError.status || 'transport failure'}`);
    const detail = apiError.payload?.detail;
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const data = detail as Record<string, unknown>;
      if (typeof data.firstFailureTs === 'number' && data.firstFailureTs > 0)
        lines.push(`**First failure:** ${new Date(data.firstFailureTs).toISOString()}`);
      if (typeof data.attempts === 'number')
        lines.push(`**Attempts before giving up:** ${data.attempts}`);
      if (typeof data.mode === 'string' && data.mode)
        lines.push(`**Mode at failure time:** \`${data.mode}\``);
      if (typeof data.transport === 'string' && data.transport)
        lines.push(`**Transport error:** \`${data.transport.slice(0, 200)}\``);
    }
  }
  lines.push('');
  return lines;
}

export function ReportBug({ error, setup = false }: { error?: Error | string; setup?: boolean }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [knownUpdate, setKnownUpdate] = useState<KnownUpdate | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [summary, setSummary] = useState('');
  const [showRequired, setShowRequired] = useState(false);
  const [hasInstallFailure, setHasInstallFailure] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const savingNow = useRef(false);
  const pending = useRef(false);
  const saveDiagnostics = async () => {
    const bridge = getBridge();
    if (!bridge || savingNow.current) return;
    savingNow.current = true;
    setSaving(true);
    setSaveFailed(false);
    try {
      const data = await bridge.backend.getSetupDiagnostics();
      await bridge.files.saveData({
        data: new TextEncoder().encode(data),
        suggestedName: 'voicestudio-setup.log',
      });
    } catch {
      setSaveFailed(true);
    } finally {
      savingNow.current = false;
      setSaving(false);
    }
  };
  const report = async (skipFreshness = false, staleBuild?: KnownUpdate) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setFailed(false);
    const bridge = getBridge();
    const cachedBackend = bridge ? getBackendStatusSnapshot() : null;
    const contactAtReport = lastBackendContact();
    let popup: Window | null = null;
    try {
      if (!skipFreshness && bridge?.updates) {
        const update = await bridge.updates.getState().catch(() => null);
        if (
          update &&
          ['available', 'downloading', 'downloaded'].includes(update.status) &&
          update.availableVersion
        ) {
          setKnownUpdate({
            current: update.currentVersion,
            latest: update.availableVersion,
          });
          return;
        }
      }
      if (!bridge) {
        popup = window.open('about:blank', '_blank');
        if (!popup) throw new Error('Popup blocked');
        popup.opener = null;
      }
      const signal = AbortSignal.timeout(2500);
      const [system, engines, previous, nativeStatus, currentLogs, host] = await Promise.allSettled(
        [
          apiJson<Record<string, unknown>>('/system/info', { signal }),
          apiJson<{ tts?: { active?: string }; asr?: { active?: string } }>('/engines', { signal }),
          apiJson<{ record: LastRunCrashRecord | null; acknowledged: boolean }>(
            '/system/last-run-crash',
            { signal },
          ),
          bridge?.backend?.getStatus?.() ?? Promise.resolve(null),
          error
            ? apiJson<{ lines?: unknown[] }>('/system/logs?tail=120', { signal })
            : Promise.resolve(null),
          // A stalled network drive must not hold the report hostage.
          withDeadline(bridge?.backend?.getHostSnapshot?.() ?? Promise.resolve(null), 3000),
        ],
      );
      const backend =
        nativeStatus.status === 'fulfilled' && nativeStatus.value
          ? nativeStatus.value
          : cachedBackend;
      const summaryText = scrubText(summary)
        .replace(LONE_SURROGATE, '\ufffd')
        .replace(/\s+/g, ' ')
        .trim();
      const context = [
        `**Summary:** ${summaryText}`,
        `**App:** VoiceStudio ${__APP_VERSION__} (build ${__BUILD_COMMIT__})`,
        '**Shell:** Electron',
      ];
      if (setup) {
        const diagnostics = await withDeadline(
          bridge?.backend?.getSetupDiagnostics?.().catch(() => null) ?? Promise.resolve(null),
          3000,
        );
        context.push(
          '## Runtime setup',
          `Stage: ${backend?.stage ?? 'unknown'}`,
          `Progress: ${JSON.stringify(backend?.setupProgress ?? {})}`,
          'Attach the downloaded voicestudio-setup.log for the full setup history.',
          '```',
          clampCrashTail(scrubText(diagnostics ?? backend?.logTail.join('\n') ?? '')),
          '```',
        );
      }
      if (staleBuild) {
        context.push(
          `**Build status:** OUTDATED — v${staleBuild.latest} was already available when this was filed`,
        );
      }
      if (system.status === 'fulfilled') {
        for (const key of [
          'platform',
          'build_commit',
          'arch',
          'os_version',
          'python',
          'device',
          'gpu_name',
          'cpu_model',
          'ram_total_gb',
          'vram_total_gb',
          'disk_free_gb',
        ]) {
          const value = system.value[key];
          if (typeof value === 'string' || typeof value === 'number')
            context.push(key + ': ' + value);
        }
      }
      // Read by the app itself, so it survives a backend that never started.
      context.push(
        ...formatHostSnapshot(host.status === 'fulfilled' ? host.value : null, {
          machine: system.status !== 'fulfilled',
        }).map((line) => scrubText(line)),
      );
      if (engines.status === 'fulfilled' && engines.value.tts?.active)
        context.push('TTS: ' + engines.value.tts.active);
      if (engines.status === 'fulfilled' && engines.value.asr?.active)
        context.push('ASR: ' + scrubText(String(engines.value.asr.active)));
      const failure = backend && ['failed', 'crashed', 'port_in_use'].includes(backend.stage);
      const crashSection = failure
        ? [
            '## Current backend failure',
            '',
            'Stage: ' + backend.stage,
            'Managed: ' + backend.managed,
            ...(backend.exitCode != null
              ? ['Exit code: ' + describeExitCode(backend.exitCode)]
              : []),
            ...(backend.exitSignal ? ['Signal: ' + backend.exitSignal] : []),
            ...(isNativeFaultExit({
              exitCode: backend.exitCode,
              signal: backend.exitSignal,
            })
              ? [t('reportBug.native_fault_cause')]
              : []),
            scrubText(backend.message),
            '',
            '```',
            clampCrashTail(scrubText(collapseRepeatedLines(backend.logTail).join('\n'))),
            '```',
            '',
          ]
        : [];
      if (backend?.lastCrash) {
        const crash = backend.lastCrash;
        crashSection.push(
          '## Last native backend exit',
          '',
          'Detected: ' + new Date(crash.timestamp).toISOString(),
          'Version: ' + crash.version,
          'Exit code: ' + describeExitCode(crash.exitCode, t('common.unknown')),
          'Signal: ' + (crash.signal ?? '—'),
          ...(isNativeFaultExit({ exitCode: crash.exitCode, signal: crash.signal })
            ? [t('reportBug.native_fault_cause')]
            : []),
          'Uptime (seconds): ' + Math.round(crash.uptimeMs / 1000),
          '```',
          clampCrashTail(scrubText(crash.logTail.join('\n'))),
          '```',
          '',
        );
      }
      if (previous.status === 'fulfilled' && previous.value.record) {
        const marker = _adaptLastRunCrash(previous.value.record, previous.value.acknowledged);
        crashSection.push(
          '## Previous unclean shutdown',
          '',
          'Detected: ' + new Date(marker.ts * 1000).toISOString(),
          'Version: ' + marker.backend_version,
          'Uptime (seconds): ' + marker.uptime_s,
          '```',
          clampCrashTail(scrubText(marker.last_stderr)),
          '```',
          '',
        );
      }
      const diagnosticSection: string[] = [];
      if (currentLogs.status === 'fulfilled' && currentLogs.value) {
        const lines = Array.isArray(currentLogs.value.lines)
          ? currentLogs.value.lines.filter((line): line is string => typeof line === 'string')
          : [];
        const tail = clampCrashTail(scrubText(lines.join('\n')).trim(), 1000);
        if (tail) diagnosticSection.push('## Current backend log', '', '```', tail, '```', '');
      }
      const installFailure = backend?.failure;
      // By code point: a UTF-16 slice can split an emoji and make encodeURIComponent throw.
      const shortSummary = Array.from(summaryText).slice(0, 100).join('');
      const url = composeBugReportUrl({
        title: installFailure
          ? `[Install] ${installFailure.code}: ${shortSummary}`
          : `[Bug] ${shortSummary}`,
        labels: installFailure ? 'bug,install' : 'bug',
        factsSection: installFailure
          ? formatFailureFacts(installFailure).map((line) => scrubText(line))
          : [],
        error,
        ctx: context.join('\n'),
        crashSection,
        diagnosticSection,
        reachabilitySection: reachabilitySection(error, backend, contactAtReport),
        breadcrumbs: formatBreadcrumbs(),
      });
      if (bridge) await bridge.files.openExternal(url);
      else if (popup && !popup.closed) popup.location.replace(url);
    } catch {
      popup?.close();
      setFailed(true);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };
  return (
    <>
      <div className="space-y-2">
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          title={t('reportBug.title')}
          onClick={() => {
            // A failure's own message seeds the one-liner; the user can edit it.
            if (!summary && error)
              setSummary(
                Array.from(
                  scrubText(typeof error === 'string' ? error : error.message || String(error))
                    .replace(LONE_SURROGATE, '\ufffd')
                    .replace(/\s+/g, ' ')
                    .trim(),
                )
                  .slice(0, 80)
                  .join(''),
              );
            setShowRequired(false);
            setHasInstallFailure(Boolean(getBridge() && getBackendStatusSnapshot()?.failure));
            setDialogOpen(true);
          }}
        >
          {t(busy ? 'common.loading' : 'reportBug.label')}
        </Button>
        {failed && (
          <p role="alert" className="text-sm text-destructive">
            {t('common.error')}
          </p>
        )}
      </div>
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>{t('reportBug.label')}</DialogTitle>
            <DialogDescription>{t('reportBug.title')}</DialogDescription>
          </DialogHeader>
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (!summary.trim()) {
                setShowRequired(true);
                return;
              }
              setDialogOpen(false);
              void report();
            }}
          >
            <div className="space-y-1.5">
              <label htmlFor="report-bug-summary" className="text-sm font-medium">
                {t('reportBug.summary_label')}
              </label>
              <Input
                id="report-bug-summary"
                value={summary}
                maxLength={120}
                autoFocus
                aria-required="true"
                aria-invalid={showRequired && !summary.trim()}
                aria-describedby={showRequired ? 'report-bug-summary-error' : undefined}
                placeholder={t('reportBug.summary_placeholder')}
                onChange={(event) => {
                  setSummary(event.target.value);
                  setShowRequired(false);
                }}
              />
              {showRequired ? (
                <p id="report-bug-summary-error" role="alert" className="text-xs text-destructive">
                  {t('reportBug.summary_required')}
                </p>
              ) : null}
            </div>
            <div className="rounded-lg border border-border/60 bg-muted/20 p-3 text-xs">
              <p className="font-medium">{t('reportBug.sent_title')}</p>
              <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-muted-foreground">
                {(['app', 'system', 'engines', 'logs', 'pages'] as const).map((item) => (
                  <li key={item}>{t(`reportBug.sent_${item}`)}</li>
                ))}
                {hasInstallFailure ? <li>{t('reportBug.sent_install_failure')}</li> : null}
              </ul>
              <p className="mt-2 text-muted-foreground">{t('reportBug.not_sent')}</p>
            </div>
            {hasInstallFailure ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={saving}
                onClick={() => void saveDiagnostics()}
              >
                {t('reportBug.save_diagnostics')}
              </Button>
            ) : null}
            {saveFailed ? (
              <p role="alert" className="text-xs text-destructive">
                {t('reportBug.save_diagnostics_failed')}
              </p>
            ) : null}
            <DialogFooter>
              <DialogClose render={<Button type="button" variant="outline" />}>
                {t('common.cancel')}
              </DialogClose>
              <Button type="submit">{t('reportBug.submit')}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={knownUpdate !== null}
        onOpenChange={(open) => {
          if (!open) setKnownUpdate(null);
        }}
        title={t('reportBug.staleTitle')}
        description={t('reportBug.staleMessage', {
          current: knownUpdate ? `v${knownUpdate.current}` : '',
          latest: knownUpdate ? `v${knownUpdate.latest}` : '',
        })}
        confirmLabel={t('reportBug.staleView')}
        cancelLabel={t('reportBug.staleFileAnyway')}
        destructive={false}
        onConfirm={() => router.navigate({ to: '/settings/updates' })}
        onCancel={() => {
          const update = knownUpdate;
          setKnownUpdate(null);
          if (update) void report(true, update);
        }}
      />
    </>
  );
}
