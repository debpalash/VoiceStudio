import { useTranslation } from 'react-i18next';
import { generationFailureMessage } from '@shared/utils/generationFailureMessage';
import type { BatchJob } from '@shared/api/batch-types';
import { FailureAction } from '@/components/failure-action';

/** A failed batch job: the localized cause, its in-app fix, and the raw text behind More info. */
export function BatchJobFailure({ job }: { job: BatchJob }) {
  const { t } = useTranslation();
  if (!job.error) return null;
  const message = generationFailureMessage(job, t);
  return (
    <>
      {message && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <p role="alert" className="min-w-0 flex-1 text-xs text-destructive">
            {message}
          </p>
          <FailureAction topic={job.docs_topic} />
        </div>
      )}
      <details className="mt-3 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">
        <summary>{t('common.more_info')}</summary>
        <p className="mt-2 break-words">{job.error}</p>
      </details>
    </>
  );
}
