import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { formatTimestamp } from '@shared/utils/timeFormat';
import { compareNarration } from './dub-narration';
import type { DubSegment } from './dub-session';

const stamp = (value: number) => formatTimestamp(value, { decimals: 3 });
export function DubScriptComparison({
  captions,
  blocks,
  onLocate,
}: {
  captions: DubSegment[];
  blocks: DubSegment[];
  onLocate?: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false),
    [index, setIndex] = useState(0);
  const rows = compareNarration(captions, blocks);
  const current = Math.min(index, Math.max(0, rows.length - 1));
  const row = rows[current];
  const problems = rows.flatMap((r, i) =>
    !r.matches ||
    (r.block.fit_status?.audio_rate || 1) > 1.5 ||
    (r.block.fit_status?.drift_s || 0) > 0.05
      ? [i]
      : [],
  );
  return (
    <>
      <Button size="xs" variant="outline" disabled={!rows.length} onClick={() => setOpen(true)}>
        {t('dub.compare_title')}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-5xl">
          <DialogHeader>
            <DialogTitle>{t('dub.compare_title')}</DialogTitle>
            <DialogDescription>{t('dub.bounded_hint')}</DialogDescription>
          </DialogHeader>
          {row && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="xs"
                  variant="outline"
                  disabled={current === 0}
                  onClick={() => setIndex(current - 1)}
                >
                  {t('common.back')}
                </Button>
                <Input
                  className="w-24"
                  aria-label={t('dub.issue_segment', { number: current + 1 })}
                  type="number"
                  min={1}
                  max={rows.length}
                  value={current + 1}
                  onChange={(e) => {
                    const n = Number(e.target.value);
                    if (Number.isInteger(n) && n >= 1 && n <= rows.length) setIndex(n - 1);
                  }}
                />
                <span className="text-xs text-muted-foreground">/ {rows.length}</span>
                <Button
                  size="xs"
                  variant="outline"
                  disabled={current + 1 === rows.length}
                  onClick={() => setIndex(current + 1)}
                >
                  {t('dub.guide_continue')}
                </Button>
                {problems.length > 0 && (
                  <Button
                    size="xs"
                    variant="secondary"
                    onClick={() => setIndex(problems.find((i) => i > current) ?? problems[0])}
                  >
                    {t('dubWorkspace.nextIssue')} · {problems.length}
                  </Button>
                )}
              </div>
              <p
                role="status"
                className={
                  row.matches
                    ? 'text-sm text-emerald-600 dark:text-emerald-400'
                    : 'text-sm text-destructive'
                }
              >
                {t(row.matches ? 'dub.compare_match' : 'dub.compare_mismatch')}
              </p>
              <div className="grid min-w-0 gap-4 md:grid-cols-2">
                <section className="min-w-0 space-y-2 rounded-lg border p-3">
                  <h3 className="text-sm font-medium">{t('dub.compare_srt')}</h3>
                  {row.sources.map((cue) => (
                    <div key={cue.id} className="border-b py-2 last:border-0">
                      <p className="text-xs tabular-nums text-muted-foreground">
                        {stamp(cue.start)}–{stamp(cue.end)}
                      </p>
                      <p className="break-words text-sm">{cue.text_original || cue.text}</p>
                    </div>
                  ))}
                </section>
                <section className="min-w-0 space-y-3 rounded-lg border p-3">
                  <h3 className="text-sm font-medium">{t('dub.compare_script')}</h3>
                  <p className="text-xs tabular-nums">
                    {stamp(row.block.start)}–{stamp(row.block.end)}
                  </p>
                  <p className="break-words text-sm">{row.block.text}</p>
                  {row.block.fit_status?.placed_start !== undefined && (
                    <p className="text-xs text-muted-foreground">
                      {t('dub.compare_previous_audio', {
                        start: stamp(row.block.fit_status.placed_start),
                        end: stamp(row.block.fit_status.placed_end || row.block.end),
                      })}
                    </p>
                  )}
                  {(row.block.fit_status?.drift_s || 0) > 0.05 && (
                    <p className="text-xs text-warning">
                      {t('dub.narration_drift', {
                        seconds: row.block.fit_status!.drift_s!.toFixed(1),
                      })}
                    </p>
                  )}
                  {(row.block.fit_status?.audio_rate || 1) > 1.5 && (
                    <p className="text-xs text-warning">
                      {t('dub.issue_fast')} · ×{row.block.fit_status!.audio_rate!.toFixed(2)}
                      <br />
                      {t('dub.issue_fast_solution')}
                    </p>
                  )}
                  {onLocate && (
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => {
                        setOpen(false);
                        onLocate(row.block.id);
                      }}
                    >
                      {t('dub.issue_locate')}
                    </Button>
                  )}
                </section>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
