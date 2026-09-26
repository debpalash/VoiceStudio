import { memo, useCallback, useEffect, useMemo, useRef } from 'react';
import { CircleAlertIcon, LoaderCircleIcon, PauseIcon, PlayIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  MediaProvider,
  StudioMediaPlayer,
  audioLoaders,
  audioSource,
  useMediaState,
  type MediaPlayerInstance,
} from '@/components/media-player';
import { Button } from '@/components/ui/button';
import { formatClock } from '@/components/waveform-player';
import { cn } from '@/lib/utils';
import {
  activeWordIndex,
  buildLyricsTimeline,
} from '@shared/utils/audiobookLyrics';
import type { AudiobookRenderChapter } from './longform-session';

interface TimelineWord {
  text: string;
  start: number;
  end: number;
  chapterIndex: number;
}

interface TimelineChapter {
  title: string;
  start: number;
  end: number;
  wordStart: number;
  wordCount: number;
}

interface LyricsTimeline {
  chapters: TimelineChapter[];
  words: TimelineWord[];
}

export function SyncedAudiobookPlayer({
  src,
  script,
  chapters,
}: {
  src: string;
  script: string;
  chapters: AudiobookRenderChapter[];
}) {
  const player = useRef<MediaPlayerInstance>(null);

  return (
    <StudioMediaPlayer
      playerRef={player}
      sourceKey="audiobook-output"
      src={audioSource(src)}
      viewType="audio"
      load="eager"
      className="overflow-hidden rounded-xl border border-border/60 bg-muted/20 shadow-[inset_0_1px_0_rgb(255_255_255/4%)]"
    >
      <MediaProvider loaders={audioLoaders} className="hidden" />
      <SyncedPlayerContent player={player} script={script} chapters={chapters} />
    </StudioMediaPlayer>
  );
}

function SyncedPlayerContent({
  player,
  script,
  chapters,
}: {
  player: React.RefObject<MediaPlayerInstance | null>;
  script: string;
  chapters: AudiobookRenderChapter[];
}) {
  const { t } = useTranslation();
  const pane = useRef<HTMLDivElement>(null);
  const paused = useMediaState('paused');
  const waiting = useMediaState('waiting');
  const canPlay = useMediaState('canPlay');
  const error = useMediaState('error');
  const currentTime = useMediaState('currentTime');
  const duration = useMediaState('duration');
  const timeline = useMemo(
    () => buildLyricsTimeline(script, { chapters, duration }) as LyricsTimeline,
    [chapters, duration, script],
  );
  const active = activeWordIndex(timeline.words, currentTime);

  useEffect(() => {
    if (active < 0) return;
    pane.current
      ?.querySelector<HTMLElement>('[data-active-word="true"]')
      ?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  const seekTo = useCallback(
    (time: number) => {
      if (player.current) player.current.currentTime = time;
    },
    [player],
  );

  return (
    <>
      <div className="flex items-center gap-3 px-3 py-2.5">
        <Button
          type="button"
          size="icon-sm"
          className="shrink-0 rounded-full"
          disabled={Boolean(error) || !canPlay}
          aria-label={t(paused ? 'player.play' : 'player.pause')}
          aria-pressed={!paused}
          aria-busy={waiting}
          onClick={() => {
            if (paused) void player.current?.play().catch(() => {});
            else void player.current?.pause();
          }}
        >
          {error ? (
            <CircleAlertIcon />
          ) : waiting ? (
            <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
          ) : paused ? (
            <PlayIcon />
          ) : (
            <PauseIcon />
          )}
        </Button>
        <input
          type="range"
          min={0}
          max={Number.isFinite(duration) && duration > 0 ? duration : 1}
          step="0.01"
          value={Number.isFinite(currentTime) ? currentTime : 0}
          disabled={!duration || Boolean(error)}
          aria-label={t('player.seek')}
          className="min-w-0 flex-1 accent-primary"
          onInput={(event) => {
            if (player.current) player.current.currentTime = Number(event.currentTarget.value);
          }}
        />
        <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
          {formatClock(currentTime)} / {formatClock(duration)}
        </span>
      </div>
      {error && (
        <p role="alert" className="border-t border-border/50 px-3 py-2 text-xs text-destructive">
          {t('player.unavailable')}
        </p>
      )}
      {timeline.words.length > 0 && (
        <div
          ref={pane}
          role="region"
          aria-label={t('audiobook.lyrics')}
          className="max-h-56 space-y-3 overflow-y-auto border-t border-border/50 px-4 py-3"
        >
          {timeline.chapters.map((chapter, index) => (
            <LyricsChapter
              key={`${index}:${chapter.start}`}
              chapter={chapter}
              words={timeline.words}
              activeIndex={
                active >= chapter.wordStart && active < chapter.wordStart + chapter.wordCount
                  ? active
                  : -1
              }
              past={active >= chapter.wordStart + chapter.wordCount}
              title={chapter.title || t('audiobook.chapter_n', { n: index + 1 })}
              onSeek={seekTo}
            />
          ))}
        </div>
      )}
    </>
  );
}

const LyricsChapter = memo(function LyricsChapter({
  chapter,
  words,
  activeIndex,
  past,
  title,
  onSeek,
}: {
  chapter: TimelineChapter;
  words: TimelineWord[];
  activeIndex: number;
  past: boolean;
  title: string;
  onSeek: (time: number) => void;
}) {
  return (
    <section>
      <button
        type="button"
        className="mb-1 rounded-md px-1 py-0.5 text-left text-xs font-medium text-foreground outline-none transition-colors hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => onSeek(chapter.start)}
      >
        {title}
      </button>
      <p className="text-sm leading-7 text-muted-foreground">
        {words
          .slice(chapter.wordStart, chapter.wordStart + chapter.wordCount)
          .map((word, offset) => {
            const index = chapter.wordStart + offset;
            const active = index === activeIndex;
            const spoken = past || (activeIndex >= 0 && index < activeIndex);
            return (
              <span key={index}>
                <button
                  type="button"
                  tabIndex={-1}
                  data-active-word={active || undefined}
                  aria-current={active ? 'true' : undefined}
                  className={cn(
                    'rounded px-0.5 text-inherit outline-none transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring',
                    spoken && 'text-foreground/70',
                    active && 'bg-primary/15 font-medium text-primary ring-1 ring-primary/20',
                  )}
                  onClick={() => onSeek(word.start)}
                >
                  {word.text}
                </button>{' '}
              </span>
            );
          })}
      </p>
    </section>
  );
});
