import { AlertCircleIcon, CopyIcon, XIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ReactNode } from 'react';
import { toast } from 'sonner';
import { urlFor } from '@shared/utils/errorDocsMap';
import type { PublicFailure } from '@/lib/api/failure';
import { cn } from '@/lib/utils';
import { ExternalLink } from './external-link';
import { Button } from './ui/button';

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
  action,
  onDismiss,
  className,
}: {
  failure?: PublicFailure | null;
  fallback: string;
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
  const docs = visibleFailure
    ? safeDocsUrl(visibleFailure.docsUrl) ||
      urlFor(visibleFailure.docsTopic || visibleFailure.errorClass)
    : null;
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
          <p className="break-words font-medium text-destructive">
            {visibleFailure?.reason || fallback}
          </p>
          {visibleFailure?.hint && (
            <p className="mt-1 text-xs leading-5 text-muted-foreground">{visibleFailure.hint}</p>
          )}
        </div>
        {action}
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
