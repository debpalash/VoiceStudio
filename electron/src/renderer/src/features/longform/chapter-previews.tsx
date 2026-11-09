import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { PlayIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { WaveformPlayer } from '@/components/waveform-player';
import { PipelineFailure } from '@/components/pipeline-failure';
import { apiJson, apiPath, describeError } from '@/lib/api/client';
import { chapterPreviewBody, type Draft } from './longform-session';
import { beginAppActivity } from '@/lib/app-activity';
interface Chapter {
  title: string;
  char_count: number;
}
export function ChapterPreviews({
  draft,
  disabled,
  canPreview,
  onBusy,
}: {
  draft: Draft;
  disabled: boolean;
  canPreview: boolean;
  onBusy: (busy: boolean) => void;
}) {
  const { t } = useTranslation();
  const [chapters, setChapters] = useState<Chapter[] | null>(null);
  const [output, setOutput] = useState<{
    output: string;
    title: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const fingerprint = JSON.stringify([
    draft.script,
    draft.voice,
    draft.language,
    draft.voiceCast,
    draft.lexicon,
    draft.overrides,
  ]);
  useEffect(() => {
    setChapters(null);
    setOutput(null);
    setError(null);
    return () => {
      controller.current?.abort();
    };
  }, [fingerprint]);
  const act = async (chapter?: number) => {
    if (disabled || controller.current || (chapter !== undefined && !canPreview)) return;
    const current = new AbortController();
    const finishActivity = chapter === undefined ? null : beginAppActivity('synthesis');
    controller.current = current;
    onBusy(true);
    setPending(true);
    setError(null);
    try {
      if (chapter === undefined) {
        const plan = await apiJson<{ chapters: Chapter[] }>('/audiobook/plan', {
          method: 'POST',
          body: JSON.stringify({
            text: draft.script,
            default_voice: draft.voice,
          }),
          signal: current.signal,
        });
        if (!current.signal.aborted) setChapters(plan.chapters);
      } else {
        const preview = await apiJson<{ output: string; title: string }>('/audiobook/preview', {
          method: 'POST',
          body: JSON.stringify(chapterPreviewBody(draft, chapter)),
          signal: current.signal,
        });
        if (!current.signal.aborted) setOutput(preview);
      }
    } catch (cause) {
      if (!current.signal.aborted) setError(describeError(cause));
    } finally {
      finishActivity?.();
      if (controller.current === current) {
        controller.current = null;
        setPending(false);
        onBusy(false);
      }
    }
  };
  return (
    <section className="space-y-3 rounded-xl border border-border/60 bg-muted/20 p-3">
      <Button
        variant="ghost"
        size="sm"
        disabled={disabled || !draft.script.trim()}
        onClick={() => void act()}
      >
        {t('audiobook.preview_plan')}
      </Button>
      {pending && (
        <p role="status" className="text-xs text-muted-foreground">
          {t('common.loading')}
        </p>
      )}
      {error && <PipelineFailure fallback={error} onDismiss={() => setError(null)} />}
      {chapters && (
        <div className="max-h-56 space-y-1 overflow-y-auto">
          {chapters.map((chapter, index) => {
            const title = chapter.title || t('stories.chapterN', { n: index + 1 });
            return (
              <div key={index} className="flex items-center gap-3 text-sm">
                <span className="min-w-0 flex-1 truncate">{title}</span>
                <span className="text-xs text-muted-foreground">
                  {t('stories.chars', { count: chapter.char_count })}
                </span>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  disabled={disabled || !canPreview}
                  aria-label={t('audiobook.preview_chapter', { title })}
                  onClick={() => void act(index)}
                >
                  <PlayIcon />
                </Button>
              </div>
            );
          })}
        </div>
      )}
      {output && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">{output.title}</p>
          <WaveformPlayer
            showWaveform={false}
            src={apiPath('/audio/' + encodeURIComponent(output.output))}
            source="chapter-preview"
          />
        </div>
      )}
    </section>
  );
}
