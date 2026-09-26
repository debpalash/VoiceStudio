import { useTranslation } from 'react-i18next';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { scrubText } from '@shared/utils/scrub';
import { describeExitCode } from '@shared/utils/nativeExit';
import { ReportBug } from './report-bug';
import { getBridge } from './bridge';

export function CrashDetails() {
  const { t } = useTranslation();
  const crash = useBackendStatus().lastCrash;
  if (!crash) return null;
  return (
    <details
      className="w-full rounded-lg border border-border/60 p-3 text-left"
      onToggle={(event) => {
        if (event.currentTarget.open && !crash.acknowledged)
          void getBridge()
            ?.backend.acknowledgeCrash()
            .catch(() => {});
      }}
    >
      <summary className="cursor-pointer text-sm font-medium">{t('crash.view')}</summary>
      <div className="space-y-3 pt-3">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
          <dt className="text-muted-foreground">{t('crash.field_exit')}</dt>
          {/* A bare NTSTATUS says nothing: 3221225477 is an access violation,
              and reading it as one is the difference between "it crashed" and
              a report that can be triaged (#2250). The raw value stays first
              so it still matches the log and anything the user searched for. */}
          <dd>
            {crash.signal ?? (crash.exitCode != null ? describeExitCode(crash.exitCode) : '—')}
          </dd>
          <dt className="text-muted-foreground">{t('crash.field_when')}</dt>
          <dd>{new Date(crash.timestamp).toLocaleString()}</dd>
          <dt className="text-muted-foreground">{t('crash.field_version')}</dt>
          <dd>{crash.version}</dd>
          <dt className="text-muted-foreground">{t('crash.field_uptime')}</dt>
          <dd>{t('crash.uptime_value', { count: Math.round(crash.uptimeMs / 1000) })}</dd>
        </dl>
        <pre
          aria-label={t('backend.log_label')}
          className="max-h-56 overflow-auto rounded-md bg-muted/30 p-3 font-mono text-xs whitespace-pre-wrap break-words"
        >
          {crash.logTail.length ? scrubText(crash.logTail.join('\n')) : t('crash.no_stderr')}
        </pre>
        <ReportBug />
      </div>
    </details>
  );
}
