import { ChevronDownIcon, ChevronUpIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import type { DubSegment } from './dub-session';

// Match the warnings shown on transcript rows. A segment with several warnings
// is one stop, and resolved warnings disappear with the current session state.
function hasTranscriptIssue(segment: DubSegment) {
  return Boolean(
    segment.translate_error ||
    segment.translate_degraded ||
    segment.plan?.status === 'tight' ||
    segment.plan?.status === 'impossible' ||
    segment.fit_status?.status === 'overflows' ||
    segment.qc_flagged,
  );
}

export function TranscriptIssueNavigation({
  segments,
  selectedId,
  disabled,
  onSelect,
}: {
  segments: DubSegment[];
  selectedId: string | null;
  disabled: boolean;
  onSelect: (id: string) => void;
}) {
  const { t } = useTranslation();
  const issues = segments.flatMap((segment, index) => (hasTranscriptIssue(segment) ? [index] : []));
  const selectedIndex = segments.findIndex((segment) => segment.id === selectedId);
  const position = issues.indexOf(selectedIndex);
  // Compare transcript positions rather than a stored issue index: an edit can
  // resolve the selected issue without skipping the next remaining warning.
  const next = issues.find((index) => index > selectedIndex) ?? issues[0];
  const previous = issues.findLast((index) => index < selectedIndex) ?? issues.at(-1);
  const unavailable = disabled || issues.length === 0;

  return (
    <div
      role="group"
      aria-label={t('dubWorkspace.transcriptIssues')}
      className="col-span-full mt-1 flex min-w-0 flex-wrap items-center gap-1 border-t border-border/50 pt-1"
    >
      <span role="status" className="mr-auto px-2 text-xs tabular-nums text-muted-foreground">
        {position < 0
          ? t('dubWorkspace.issueCount', { total: issues.length })
          : t('dubWorkspace.issuePosition', { current: position + 1, total: issues.length })}
      </span>
      <Button
        size="sm"
        variant="ghost"
        disabled={unavailable}
        onClick={() => previous !== undefined && onSelect(segments[previous].id)}
      >
        <ChevronUpIcon />
        {t('dubWorkspace.previousIssue')}
      </Button>
      <Button
        size="sm"
        variant="ghost"
        disabled={unavailable}
        onClick={() => next !== undefined && onSelect(segments[next].id)}
      >
        <ChevronDownIcon />
        {t('dubWorkspace.nextIssue')}
      </Button>
    </div>
  );
}
