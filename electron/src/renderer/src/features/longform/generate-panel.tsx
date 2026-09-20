import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { GenerationProgress } from './generation-progress';
import type { GenerateBlocker } from './generate-blocker';
import type { AudiobookRenderChapter } from './longform-session';

/** The slice of the render session this panel reads. */
export interface GenerateSession {
  active: 'stories' | 'audiobook' | null;
  stage: string;
  completed: number;
  total: number;
  failed: number;
  stopped: boolean;
  chapters: AudiobookRenderChapter[];
}

/**
 * Generate / Stop, the chapter tracker and the render status in one block, so
 * the primary action and its progress are always in the same, visible place
 * (pinned in the setup column) instead of below the last line of a long script.
 */
export function GeneratePanel({
  mode,
  session,
  blocker,
  onGenerate,
  onStop,
}: {
  mode: 'stories' | 'audiobook';
  session: GenerateSession;
  blocker: GenerateBlocker | null;
  onGenerate: () => void;
  onStop: () => void;
}) {
  const { t } = useTranslation();
  const active = session.active === mode;
  const status = active
    ? session.stage === 'assembling'
      ? t('audiobook.assembling')
      : session.stage === 'starting'
        ? t('common.loading')
        : t('audiobook.progress_summary', {
            current: Math.min(session.completed + 1, session.total),
            total: session.total,
          })
    : blocker
      ? t('audiobook.blocked.' + blocker)
      : '';
  return (
    <div data-slot="generate-panel" className="flex min-h-0 flex-col gap-3">
      {session.failed > 0 && (
        <p role="status" className="shrink-0 text-xs text-muted-foreground">
          {t('audiobook.failed_note', { count: session.failed })}
        </p>
      )}
      {session.stopped && !session.active && (
        <p role="status" className="shrink-0 text-xs text-muted-foreground">
          {t('audiobook.stopped_note')}
        </p>
      )}
      {active && session.stage !== 'starting' && (
        <div className="min-h-0 max-h-[38vh] overflow-y-auto">
          <GenerationProgress
            chapters={session.chapters}
            assembling={session.stage === 'assembling'}
          />
        </div>
      )}
      {status && (
        <p id="generate-status" role="status" className="shrink-0 text-xs text-muted-foreground">
          {status}
        </p>
      )}
      {active ? (
        <Button variant="outline" className="w-full shrink-0" onClick={onStop}>
          {t('common.stop')}
        </Button>
      ) : (
        <Button
          className="w-full shrink-0"
          disabled={blocker !== null}
          aria-describedby={status ? 'generate-status' : undefined}
          onClick={onGenerate}
        >
          {t(mode === 'stories' ? 'stories.generateAll' : 'audiobook.create')}
        </Button>
      )}
    </div>
  );
}
