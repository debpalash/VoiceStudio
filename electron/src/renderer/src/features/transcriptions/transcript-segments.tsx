import { useMemo, useState, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { segTimeRange } from '@shared/utils/transcriptionFormat';
import { VirtualList } from '@/components/virtual-list';

type Segment = Parameters<typeof segTimeRange>[0] & { text?: string; speaker?: string | null };

/**
 * The collapsible per-segment transcript. Rows mount only while the section is
 * open, and a long transcript windows its rows instead of mounting thousands.
 */
export function TranscriptSegments({
  segments,
  scrollRef,
}: {
  segments: readonly NonNullable<Segment>[];
  scrollRef: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rows = useMemo(() => segments.map((segment, index) => ({ segment, index })), [segments]);
  return (
    <details
      className="text-sm"
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer text-muted-foreground">
        {t('transcriptions.segments_title')}
      </summary>
      {open && (
        <div className="mt-3">
          <VirtualList
            items={rows}
            scrollRef={scrollRef}
            getKey={(row) => row.index}
            estimateSize={28}
            rowClassName="pb-3"
            renderRow={({ segment }) => (
              <p>
                <span className="mr-3 font-mono text-xs text-muted-foreground">
                  {segTimeRange(segment)}
                </span>
                {segment.speaker ? (
                  <span className="mr-2 font-medium text-foreground">{segment.speaker}</span>
                ) : null}
                {segment.text}
              </p>
            )}
          />
        </div>
      )}
    </details>
  );
}
