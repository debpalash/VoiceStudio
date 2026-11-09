import { Link } from '@tanstack/react-router';
import { AlertCircleIcon, XIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { failureTopicOf } from '@shared/utils/failureCatalog';
import { FailureAction } from '@/components/failure-action';
import { Button } from '@/components/ui/button';
import { describeError } from '@/lib/api/client';
import { apiFailureGuidance } from '@/lib/api/failure';

export interface TranscriptionFailureState {
  /** Raw failure text, kept behind Technical details. */
  detail: string;
  /** VoiceStudio-owned remediation for a classified failure, shown visibly. */
  guidance?: string;
  /** Diagnosed failure class, which picks the in-app fix. */
  topic?: string;
}

export function transcriptionFailureFrom(error: unknown): TranscriptionFailureState {
  return {
    detail: describeError(error),
    guidance: apiFailureGuidance(error),
    topic: failureTopicOf(error),
  };
}

export function TranscriptionFailure({
  failure,
  retryDisabled,
  onRetry,
  onDismiss,
}: {
  failure: TranscriptionFailureState;
  retryDisabled: boolean;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="mx-6 mt-4 rounded-xl border border-border/60 bg-muted/20 px-4 py-3 text-sm">
      <div className="flex items-center gap-3">
        <AlertCircleIcon className="size-4 shrink-0 text-destructive" />
        <span role="alert" className="min-w-0 flex-1 font-medium">
          {t('transcriptions.failed')}
        </span>
        <FailureAction topic={failure.topic} />
        <Button variant="ghost" disabled={retryDisabled} onClick={onRetry}>
          {t('backend.retry')}
        </Button>
        <Button variant="ghost" size="icon-xs" aria-label={t('common.dismiss')} onClick={onDismiss}>
          <XIcon />
        </Button>
      </div>
      {failure.guidance && (
        <p className="mt-1 pl-7 text-xs leading-5 text-foreground">{failure.guidance}</p>
      )}
      <details className="mt-1 pl-7 text-xs text-muted-foreground">
        <summary className="cursor-pointer rounded py-1 focus-visible:outline-ring">
          {t('profileIdentity.details')}
        </summary>
        <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted/40 p-3 font-mono text-xs">
          {failure.detail}
        </pre>
        <Link to="/settings/logs" className="mt-2 inline-block underline">
          {t('settings.logs')}
        </Link>
      </details>
    </div>
  );
}
