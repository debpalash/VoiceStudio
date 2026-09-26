import {
  classifyError,
  urlFor,
  type ErrorClass,
} from '@shared/utils/errorDocsMap';
import { ExternalLink } from './external-link';
import { buildIssueSearchUrl } from '@shared/utils/bugReportDocument';
import { Component, useEffect, type ReactNode, type ErrorInfo } from 'react';
import { useTranslation } from 'react-i18next';
import { BotIcon, CircleAlertIcon } from 'lucide-react';
import { scrubText } from '@shared/utils/scrub';
import { ReportBug } from './report-bug';
import { Button } from './ui/button';
import { openRepairAgent } from '@/lib/repair-agent-events';
import { captureException } from '@shared/utils/analytics';

const MODULE_RETRY_PREFIX = 'voicestudio.moduleRetry:';
const reloadRenderer = () => window.location.reload();

function claimModuleRetry(message: string): string | null {
  if (
    !/(?:failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed)/i.test(
      message,
    )
  ) {
    return null;
  }
  const key = MODULE_RETRY_PREFIX + message.slice(0, 240);
  try {
    if (sessionStorage.getItem(key) === 'attempted') return null;
    sessionStorage.setItem(key, 'pending');
    return key;
  } catch {
    return null;
  }
}

export function ErrorRecovery({
  error,
  reset,
  reload = reloadRenderer,
}: {
  error: unknown;
  reset: () => void;
  reload?: () => void;
}) {
  const { t } = useTranslation();
  const failure = error instanceof Error ? error : new Error(String(error));
  useEffect(() => {
    const retryKey = claimModuleRetry(failure.message);
    if (retryKey) {
      // A failed dynamic-import promise is cached by the module loader. Resetting
      // only the React/TanStack boundary reuses that rejection; one full reload
      // is required to request a stale or briefly unavailable chunk again. The
      // session marker survives the reload so a second failure reaches repair.
      const timer = window.setTimeout(() => {
        sessionStorage.setItem(retryKey, 'attempted');
        reload();
      }, 250);
      return () => window.clearTimeout(timer);
    }
    openRepairAgent(scrubText([failure.message, failure.stack].filter(Boolean).join('\n')), true);
  }, [failure.message, failure.stack, reload]);
  return (
    <div className="flex h-full min-h-64 items-center justify-center p-6">
      <section
        className="w-full max-w-lg space-y-4 rounded-xl border border-border bg-card p-6"
        role="alert"
      >
        <CircleAlertIcon className="size-6 text-destructive" aria-hidden="true" />
        <h1 className="text-base font-semibold">{t('errors.title')}</h1>
        <p className="text-sm text-muted-foreground">{t('errors.desc')}</p>
        <details>
          <summary className="cursor-pointer text-sm text-muted-foreground">
            {t('backend.show_log')}
          </summary>
          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs">
            {scrubText(failure.message)}
          </pre>
        </details>
        <div className="flex flex-wrap items-start gap-2">
          <Button size="sm" onClick={reset}>
            {t('errors.tryAgain')}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              openRepairAgent(
                scrubText([failure.message, failure.stack].filter(Boolean).join('\n')),
                true,
              )
            }
          >
            <BotIcon />
            {t('repairAgent.title')}
          </Button>
          <ExternalLink
            href={urlFor(
              (failure as Error & { errorClass?: ErrorClass }).errorClass ?? classifyError(failure),
            )}
          >
            {t('errors.openDocs')}
          </ExternalLink>
          <ReportBug error={failure} />
          <ExternalLink href={buildIssueSearchUrl(failure)}>
            {t('errors.searchIssues')}
          </ExternalLink>
        </div>
      </section>
    </div>
  );
}

export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    captureException(error, 'renderer:react-boundary');
    console.error('[VoiceStudio UI]', error, info.componentStack);
  }
  render() {
    return this.state.error ? (
      <ErrorRecovery error={this.state.error} reset={() => this.setState({ error: null })} />
    ) : (
      this.props.children
    );
  }
}
