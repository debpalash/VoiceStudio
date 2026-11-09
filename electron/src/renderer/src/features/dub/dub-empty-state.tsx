import { useMemo, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { FilmIcon, FolderOpenIcon, LoaderCircleIcon, TriangleAlertIcon } from 'lucide-react';
import { videoSource } from '@/components/media-player';
import { Button } from '@/components/ui/button';
import { VideoPlayer } from '@/components/video-player';
import { WaveformPlayer } from '@/components/waveform-player';
import { cn } from '@/lib/utils';
import { DUB_INPUT_ISSUE_KEYS } from './dub-input-issue';
import type { DubSession } from './dub-session';
import { compactSourceLabel } from './dub-source-row';

const STEPS = ['dub.upload_transcribe', 'dub.translate', 'dub.generate_dub'] as const;

/** One compact line for the three steps; the numbered sidebar holds their controls. */
export function DubProgress({ current, busy }: { current: number; busy?: boolean }) {
  const { t } = useTranslation();
  return (
    <ol className="flex min-w-0 flex-wrap items-center justify-center gap-x-1 gap-y-1 text-xs text-muted-foreground">
      {STEPS.map((label, index) => {
        const active = index === current;
        return (
          <li key={label} className="flex min-w-0 items-center gap-1">
            {index > 0 && (
              <span aria-hidden="true" className="px-1 text-border">
                /
              </span>
            )}
            <span
              aria-current={active ? 'step' : undefined}
              className={cn(
                'flex items-center gap-1.5 rounded-full px-2 py-0.5',
                active && 'bg-primary/10 font-medium text-primary',
                index < current && 'text-foreground/80',
              )}
            >
              {active && busy ? (
                <LoaderCircleIcon
                  aria-hidden="true"
                  className="size-3 animate-spin motion-reduce:animate-none"
                />
              ) : (
                <span aria-hidden="true" className="tabular-nums">
                  {index + 1}
                </span>
              )}
              {t(label)}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The main pane before there are segments: the welcome placeholder when no
 * source is loaded, otherwise the loaded source with its preview and status.
 */
export function DubEmptyState({
  session,
  busy,
  stage,
  localPreview,
  demo,
  onChangeFile,
}: {
  session: DubSession;
  busy: boolean;
  /** Translation key of the running stage. */
  stage: string;
  /** Object URL of the file picked in this window, when the sidebar shows no preview. */
  localPreview?: string;
  /** The sample comparison, shown only while nothing is loaded. */
  demo?: ReactNode;
  onChangeFile: () => void;
}) {
  const { t } = useTranslation();
  const video = useMemo(
    () =>
      localPreview && session.inputType === 'video'
        ? videoSource(localPreview, session.filename, 'video/mp4')
        : null,
    [localPreview, session.filename, session.inputType],
  );
  if (!session.jobId)
    return (
      <div className="mx-auto flex min-h-[24rem] w-full max-w-5xl flex-col items-center justify-center px-6 text-center">
        <div className="mb-3 flex size-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          <FilmIcon className="size-6" />
        </div>
        <h2 className="text-lg font-semibold">{t('dub.video_dubbing_studio')}</h2>
        <p className="mt-2 text-sm text-muted-foreground">{t('dub.supported_formats')}</p>
        {demo && <div className="mt-4 w-full">{demo}</div>}
      </div>
    );
  const issue = session.inputIssue;
  return (
    <div
      data-slot="dub-source-status"
      className="mx-auto flex w-full max-w-3xl flex-col items-center gap-4 py-6 text-center"
    >
      {video && (
        <div className="w-full overflow-hidden rounded-xl border border-border/40 bg-black/80">
          <VideoPlayer src={video} source="dub-source-preview" load="visible" />
        </div>
      )}
      {localPreview && session.inputType === 'audio' && (
        <WaveformPlayer
          src={localPreview}
          source="dub-source-preview"
          durationHint={session.duration || undefined}
          className="w-full"
        />
      )}
      <div className="min-w-0 max-w-full space-y-1">
        <h2 className="truncate text-base font-semibold" title={session.filename}>
          {compactSourceLabel(session.filename)}
        </h2>
        {busy ? (
          <p role="status" className="text-sm text-muted-foreground">
            {t(stage)}
          </p>
        ) : issue ? (
          <p role="status" className="flex items-center justify-center gap-1.5 text-sm">
            <TriangleAlertIcon aria-hidden="true" className="size-4 shrink-0 text-warning" />
            {t(DUB_INPUT_ISSUE_KEYS[issue])}
          </p>
        ) : null}
      </div>
      {issue && !busy && (
        <Button size="sm" variant="outline" onClick={onChangeFile}>
          <FolderOpenIcon />
          {t('dub.change_file')}
        </Button>
      )}
      <DubProgress current={0} busy={busy} />
    </div>
  );
}
