import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { composeBugReportUrl } from '@shared/utils/bugReportDocument';
import { formatBreadcrumbs } from '@shared/utils/breadcrumbs';
import { contactAge, lastBackendContact } from '@shared/utils/backendContact';
import { clampCrashTail } from '@shared/utils/crashReport';
import { scrubText } from '@shared/utils/scrub';
import { describeExitCode, isNativeFaultExit } from '@shared/utils/nativeExit';
import {
  _adaptLastRunCrash,
  type LastRunCrashRecord,
} from '@shared/utils/runCrashRecord';
import { getBackendStatusSnapshot } from '@/hooks/use-backend-status';
import { apiJson } from '@/lib/api/client';
import { router } from '@/router';
import { getBridge } from './bridge';
import { Button } from './ui/button';
import { ConfirmDialog } from '@/features/clone/confirm-dialog';

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

export function ReportBug({ error }: { error?: Error | string }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [knownUpdate, setKnownUpdate] = useState<KnownUpdate | null>(null);
  const pending = useRef(false);
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
      const [system, engines, previous, nativeStatus, currentLogs] = await Promise.allSettled([
        apiJson<Record<string, unknown>>('/system/info', { signal }),
        apiJson<{ tts?: { active?: string } }>('/engines', { signal }),
        apiJson<{ record: LastRunCrashRecord | null; acknowledged: boolean }>(
          '/system/last-run-crash',
          { signal },
        ),
        bridge?.backend?.getStatus?.() ?? Promise.resolve(null),
        error
          ? apiJson<{ lines?: unknown[] }>('/system/logs?tail=120', { signal })
          : Promise.resolve(null),
      ]);
      const backend =
        nativeStatus.status === 'fulfilled' && nativeStatus.value
          ? nativeStatus.value
          : cachedBackend;
      const context = ['**App:** VoiceStudio ' + __APP_VERSION__, '**Shell:** Electron'];
      if (staleBuild) {
        context.push(
          `**Build status:** OUTDATED — v${staleBuild.latest} was already available when this was filed`,
        );
      }
      if (system.status === 'fulfilled') {
        for (const key of [
          'platform',
          'os_version',
          'python',
          'device',
          'gpu_name',
          'cpu_model',
          'ram_total_gb',
          'vram_total_gb',
        ]) {
          const value = system.value[key];
          if (typeof value === 'string' || typeof value === 'number')
            context.push(key + ': ' + value);
        }
      }
      if (engines.status === 'fulfilled' && engines.value.tts?.active)
        context.push('TTS: ' + engines.value.tts.active);
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
            }) ? [t('reportBug.native_fault_cause')] : []),
            scrubText(backend.message),
            '',
            '```',
            clampCrashTail(scrubText(backend.logTail.join('\n'))),
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
          // A bare NTSTATUS is not triageable: 3221225477 and -1073741819 are
          // the same access violation, reported differently by Node and Rust,
          // and neither reads as a segfault (#2250). Name it, and say outright
          // that a native fault leaves no Python traceback so nobody hunts for
          // one that was never written.
          'Exit code: ' + describeExitCode(crash.exitCode, t('common.unknown')),
          'Signal: ' + (crash.signal ?? '—'),
          ...(isNativeFaultExit({ exitCode: crash.exitCode, signal: crash.signal })
            ? [
                t('reportBug.native_fault_cause'),
              ]
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
      const url = composeBugReportUrl({
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
          onClick={() => void report()}
        >
          {t(busy ? 'common.loading' : 'reportBug.label')}
        </Button>
        {failed && (
          <p role="alert" className="text-sm text-destructive">
            {t('common.error')}
          </p>
        )}
      </div>
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
