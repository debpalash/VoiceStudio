import { AlertCircleIcon, CopyIcon, XIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ReactNode } from 'react';
import { toast } from 'sonner';
import { urlFor } from '@shared/utils/errorDocsMap';
import { FAILURE_CATALOG, failureTopicOf } from '@shared/utils/failureCatalog';
import type { PublicFailure } from '@/lib/api/failure';
import { classifyFailure, looksLikeHtml } from '@/lib/api/failure-cause';
import { cn } from '@/lib/utils';
import { ExternalLink } from './external-link';
import { FailureAction } from './failure-action';
import { Button } from './ui/button';

const GATEWAY_STATUSES = [502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530];

/** Our own localized proxy advice (any language), recognised by catalogue text, not wording. */
function isProxyAdvice(
  text: string,
  t: (key: string, options?: Record<string, unknown>) => string,
) {
  return GATEWAY_STATUSES.some(
    (status) => t('tts_errors.proxy_gateway_error', { status }) === text,
  );
}

function safeDocsUrl(value: string | undefined): string | null {
  if (!value) return null;
  try {
    const protocol = new URL(value).protocol;
    return protocol === 'https:' || protocol === 'http:' ? value : null;
  } catch {
    return null;
  }
}

export function PipelineFailure({
  failure,
  fallback,
  topic: knownTopic,
  action,
  onDismiss,
  className,
}: {
  failure?: PublicFailure | null;
  fallback: string;
  /** The diagnosed class when the caller holds it apart from `failure`. */
  topic?: string | null;
  action?: ReactNode;
  onDismiss?: () => void;
  className?: string;
}) {
  const { t } = useTranslation();
  const fallbackSummary = fallback
    .split(/\s+(?:See Settings\b|Underlying error:)|\r?\n/, 1)[0]
    ?.trim();
  const compactFallback: PublicFailure | null =
    fallback.length > 180 || fallbackSummary !== fallback.trim()
      ? {
          reason: fallbackSummary || t('common.error'),
          diagnostic: fallback,
        }
      : null;
  const visibleFailure = failure ?? compactFallback;
  // A diagnosed class always reads in the app's language and offers its fix;
  // the backend's English text stays under Details and in the diagnostic.
  const topic =
    failureTopicOf(failure) ??
    failureTopicOf({ docs_topic: knownTopic }) ??
    failureTopicOf(fallback);
  const docs = visibleFailure
    ? safeDocsUrl(visibleFailure.docsUrl) ||
      urlFor(topic || visibleFailure.docsTopic || visibleFailure.errorClass)
    : null;
  const rawText = visibleFailure?.reason || fallback;
  // A failure that already carries curated guidance (hint, docs topic, or our own proxy
  // advice) keeps its message; only raw engine/gateway text is translated into a cause.
  const curated = Boolean(
    topic || visibleFailure?.hint || visibleFailure?.docsTopic || visibleFailure?.docsUrl,
  );
  const cause =
    curated || isProxyAdvice(rawText, t)
      ? null
      : classifyFailure(rawText, visibleFailure?.diagnostic, visibleFailure?.errorClass);
  const htmlBody = looksLikeHtml(rawText);
  // Known cause: plain title + fix chips; the raw message moves under Details.
  const title = topic
    ? t(FAILURE_CATALOG[topic].messageKey)
    : cause
      ? t(`failureCause.${cause}.title`)
      : rawText;
  const fixes = cause ? [1, 2].map((n) => t(`failureCause.${cause}.fix${n}`)) : [];
  const rawDetail = (topic || cause) && rawText !== title ? rawText : '';
  const hint = topic ? undefined : visibleFailure?.hint;
  return (
    <div
      role="alert"
      className={cn(
        'rounded-xl border border-destructive/20 bg-destructive/5 p-3 text-sm',
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <AlertCircleIcon className="mt-0.5 size-4 shrink-0 text-destructive" />
        <div className="min-w-0 flex-1">
          <p className="break-words font-medium text-destructive">{title}</p>
          {fixes.length > 0 && (
            <ul className="mt-1.5 flex flex-wrap gap-1.5" aria-label={t('failureCause.fixes')}>
              {fixes.map((fix) => (
                <li
                  key={fix}
                  className="rounded-full border border-border bg-background px-2.5 py-0.5 text-xs text-foreground"
                >
                  {fix}
                </li>
              ))}
            </ul>
          )}
          {hint && <p className="mt-1 text-xs leading-5 text-muted-foreground">{hint}</p>}
        </div>
        {action}
        <FailureAction topic={topic} />
        {onDismiss && (
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={t('dub.dismiss_error')}
            onClick={onDismiss}
          >
            <XIcon />
          </Button>
        )}
      </div>
      {rawDetail && (
        <details className="mt-2 pl-7 text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none">{t('failureCause.details')}</summary>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-2 font-mono">
            {htmlBody
              ? rawText
                  .replace(/<[^>]*>/g, ' ')
                  .replace(/\s+/g, ' ')
                  .trim()
              : rawText}
          </pre>
        </details>
      )}
      {(docs || visibleFailure?.diagnostic) && (
        <div className="mt-2 flex flex-wrap items-center gap-2 pl-7">
          {docs && <ExternalLink href={docs}>{t('dub.open_docs')}</ExternalLink>}
          {visibleFailure?.diagnostic && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() =>
                void navigator.clipboard
                  .writeText(visibleFailure.diagnostic!)
                  .then(() => toast.success(t('dub.diagnostic_copied')))
                  .catch(() => toast.error(t('dub.copy_failed')))
              }
            >
              <CopyIcon />
              {t('dub.copy_diagnostic')}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
