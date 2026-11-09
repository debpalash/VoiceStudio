import { AudioLinesIcon, PauseIcon, PlayIcon, XIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { WaveformPlayer } from '@/components/waveform-player';
import { stopActivePlayback, usePlaybackSource } from '@/lib/audio/playback';
import { clipTitle, playbackKindLabelKey } from '@/lib/audio/playback-kind';
import {
  closeDock,
  dockSource,
  isClipPlaying,
  playInDock,
  useNowPlaying,
  type NowPlaying,
} from '@/lib/store/now-playing';
import { cn } from '@/lib/utils';

/**
 * App-wide bottom player. Takes and renders opened from lists play here with
 * the same waveform player as Latest take, so playback keeps going while the
 * user moves between pages.
 */
/** What fits the 15rem title column, so the word-boundary cut wins over the CSS one. */
const DOCK_TITLE_CHARS = 32;

export function NowPlayingDock() {
  const { t } = useTranslation();
  const clip = useNowPlaying();
  if (!clip) return null;
  const label = t(playbackKindLabelKey(clip.kind));
  return (
    <section
      aria-label={t('player.now_playing')}
      data-slot="now-playing-dock"
      className="relative z-10 flex h-14 shrink-0 items-center gap-3 border-t border-border/60 bg-background/95 px-4 backdrop-blur-xl"
    >
      <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
        <AudioLinesIcon className="size-4" aria-hidden="true" />
      </span>
      <div className="w-60 min-w-0 shrink" title={clip.title + ' · ' + label}>
        <p className="truncate text-sm leading-5 font-medium">{clipTitle(clip.title, DOCK_TITLE_CHARS)}</p>
        <p className="truncate text-xs leading-4 text-muted-foreground">{label}</p>
      </div>
      <WaveformPlayer
        key={clip.nonce}
        src={clip.src}
        source={dockSource(clip.source)}
        durationHint={clip.duration ?? undefined}
        autoPlay
        compact
        height={32}
        className="min-w-0 flex-1 bg-transparent px-0"
      />
      <Button
        variant="ghost"
        size="icon-sm"
        className="text-muted-foreground hover:text-foreground"
        aria-label={t('common.close')}
        title={t('common.close')}
        onClick={() => {
          stopActivePlayback();
          closeDock();
        }}
      >
        <XIcon />
      </Button>
    </section>
  );
}

/** Row play button that sends a clip to the bottom player; pressing it again pauses. */
export function PlayInDockButton({
  className,
  ...clip
}: Omit<NowPlaying, 'nonce'> & { className?: string }) {
  const { t } = useTranslation();
  const playing = isClipPlaying(usePlaybackSource(), clip.source);
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      aria-label={t(playing ? 'player.pause' : 'player.play')}
      title={t(playing ? 'player.pause' : 'player.play')}
      aria-pressed={playing}
      onClick={() => (playing ? stopActivePlayback() : playInDock(clip))}
      className={cn(playing && 'text-primary', className)}
    >
      {playing ? <PauseIcon /> : <PlayIcon />}
    </Button>
  );
}
