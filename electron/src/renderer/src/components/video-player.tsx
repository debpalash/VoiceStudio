import { memo, useEffect, useRef, useState } from 'react';
import {
  PlayIcon,
  PauseIcon,
  MaximizeIcon,
  MinimizeIcon,
  Volume2Icon,
  VolumeXIcon,
  LoaderCircleIcon,
  RotateCcwIcon,
  RotateCwIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { MediaPlayerProps } from '@vidstack/react';
import {
  StudioMediaPlayer,
  MediaProvider,
  videoLoaders,
  useMediaState,
  type MediaPlayerInstance,
} from './media-player';
import { Button } from './ui/button';
import { formatClock } from './waveform-player';
import {
  publishPlaybackClock,
  resetPlaybackClock,
  usePlaybackSeek,
} from '@/lib/audio/playback-clock';
export const VideoPlayer = memo(function VideoPlayer({
  src,
  source = 'video',
  playbackGroup,
  load = 'play',
  poster,
  playerRef: externalPlayerRef,
  onPlay,
  onPause,
  onSeeked,
  onCanPlay,
  controls = 'full',
}: {
  src: MediaPlayerProps['src'];
  source?: string;
  playbackGroup?: string;
  load?: MediaPlayerProps['load'];
  poster?: string;
  playerRef?: React.RefObject<MediaPlayerInstance | null>;
  onPlay?: MediaPlayerProps['onPlay'];
  onPause?: MediaPlayerProps['onPause'];
  onSeeked?: MediaPlayerProps['onSeeked'];
  onCanPlay?: MediaPlayerProps['onCanPlay'];
  controls?: 'full' | 'compact';
}) {
  const localPlayerRef = useRef<MediaPlayerInstance>(null);
  const player = externalPlayerRef ?? localPlayerRef;
  const sourceIdentity =
    typeof src === 'string'
      ? src
      : src && !Array.isArray(src) && typeof src === 'object' && 'src' in src
        ? String(src.src)
        : JSON.stringify(src);
  return (
    <StudioMediaPlayer
      playerRef={player}
      sourceKey={source}
      playbackGroup={playbackGroup}
      src={src}
      viewType="video"
      load={load}
      poster={poster}
      onPlay={onPlay}
      onPause={onPause}
      onSeeked={onSeeked}
      onCanPlay={onCanPlay}
      className="group relative overflow-hidden rounded-xl border border-white/10 bg-black text-white shadow-[0_16px_40px_-24px_rgb(0_0_0/85%)]"
    >
      <MediaProvider
        loaders={videoLoaders}
        className="relative aspect-video [&_[data-remotion-canvas]]:h-full [&_[data-remotion-canvas]]:w-full [&_[data-remotion-container]]:h-full [&_[data-remotion-container]]:w-full [&_video]:h-full [&_video]:w-full [&_iframe]:h-full [&_iframe]:w-full"
      />
      <VideoControls
        player={player}
        source={source}
        sourceIdentity={sourceIdentity}
        compact={controls === 'compact'}
      />
    </StudioMediaPlayer>
  );
});
function VideoControls({
  player,
  source,
  sourceIdentity,
  compact,
}: {
  player: React.RefObject<MediaPlayerInstance | null>;
  source: string;
  sourceIdentity: string;
  compact: boolean;
}) {
  const { t } = useTranslation();
  const rangeEnd = useRef<number | null>(null);
  const paused = useMediaState('paused');
  const time = useMediaState('currentTime');
  const duration = useMediaState('duration');
  const muted = useMediaState('muted');
  const volume = useMediaState('volume');
  const fullscreen = useMediaState('fullscreen');
  const canFullscreen = useMediaState('canFullscreen');
  const waiting = useMediaState('waiting');
  const error = useMediaState('error');
  const [failed, setFailed] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);
  const seek = usePlaybackSeek(source);
  const progress =
    Number.isFinite(time) && Number.isFinite(duration) && duration > 0
      ? Math.max(0, Math.min(100, (time / duration) * 100))
      : 0;
  useEffect(() => {
    resetPlaybackClock(source);
    return () => resetPlaybackClock(source);
  }, [source, sourceIdentity]);
  useEffect(() => publishPlaybackClock(source, time, duration), [duration, source, time]);
  useEffect(() => {
    if (!seek || !player.current) return;
    player.current.currentTime = seek.time;
    rangeEnd.current = seek.end ?? null;
    if (seek.play) void player.current.play().catch(() => setFailed(true));
  }, [player, seek]);
  useEffect(() => {
    if (rangeEnd.current == null || time < rangeEnd.current) return;
    rangeEnd.current = null;
    void player.current?.pause().catch(() => setFailed(true));
  }, [player, time]);
  return (
    <div
      className={`absolute inset-x-2 bottom-2 z-10 space-y-2 rounded-xl border border-white/10 bg-black/55 px-2.5 pt-8 pb-2 text-white shadow-[0_12px_32px_rgb(0_0_0/38%)] backdrop-blur-xl transition-[opacity,transform] duration-200 group-hover:translate-y-0 group-hover:opacity-100 group-focus-within:translate-y-0 group-focus-within:opacity-100 ${paused || waiting ? 'translate-y-0 opacity-100' : 'translate-y-1 opacity-0'}`}
    >
      {(error || failed) && (
        <p role="alert" className="text-xs text-destructive">
          {t('player.unavailable')}
        </p>
      )}
      <input
        type="range"
        aria-label={t('player.seek')}
        min={0}
        max={Number.isFinite(duration) && duration > 0 ? duration : 1}
        step="0.01"
        value={Number.isFinite(time) ? time : 0}
        disabled={!Number.isFinite(duration) || duration <= 0}
        className="block h-1.5 w-full cursor-pointer appearance-none rounded-full border-0 bg-white/20 accent-primary outline-none [&::-webkit-slider-thumb]:size-3.5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:shadow-[0_1px_8px_rgb(0_0_0/55%)] focus-visible:ring-2 focus-visible:ring-primary/70"
        style={{
          background: `linear-gradient(to right, var(--primary) 0%, var(--primary) ${progress}%, rgb(255 255 255 / 22%) ${progress}%, rgb(255 255 255 / 22%) 100%)`,
        }}
        onInput={(event) => {
          rangeEnd.current = null;
          if (player.current) player.current.currentTime = Number(event.currentTarget.value);
        }}
      />
      <div className="flex items-center gap-2">
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={t(paused ? 'player.play' : 'player.pause')}
          aria-busy={waiting}
          className="hover:bg-white/15 hover:text-white"
          onClick={() => {
            setFailed(false);
            rangeEnd.current = null;
            if (paused) void player.current?.play().catch(() => setFailed(true));
            else void player.current?.pause();
          }}
        >
          {waiting ? (
            <LoaderCircleIcon className="animate-spin" />
          ) : paused ? (
            <PlayIcon />
          ) : (
            <PauseIcon />
          )}
        </Button>
        {!compact && (
          <>
            <Button
              size="icon-sm"
              variant="ghost"
              className="hover:bg-white/15 hover:text-white"
              aria-label={`${t('player.seek')} -10s`}
              onClick={() => {
                if (player.current) player.current.currentTime = Math.max(0, time - 10);
              }}
            >
              <RotateCcwIcon />
            </Button>
            <Button
              size="icon-sm"
              variant="ghost"
              className="hover:bg-white/15 hover:text-white"
              aria-label={`${t('player.seek')} +10s`}
              onClick={() => {
                if (player.current)
                  player.current.currentTime = Math.min(
                    Number.isFinite(duration) ? duration : time + 10,
                    time + 10,
                  );
              }}
            >
              <RotateCwIcon />
            </Button>
          </>
        )}
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={t(muted ? 'player.unmute' : 'player.mute')}
          aria-pressed={muted}
          className="hover:bg-white/15 hover:text-white"
          onClick={() => {
            if (player.current) player.current.muted = !muted;
          }}
        >
          {muted ? <VolumeXIcon /> : <Volume2Icon />}
        </Button>
        {!compact && (
          <>
            <input
              type="range"
              aria-label={t('player.volume')}
              min={0}
              max={1}
              step="0.05"
              value={muted ? 0 : volume}
              className="hidden h-1 w-14 shrink-0 cursor-pointer appearance-none rounded-full bg-white/25 accent-primary [&::-webkit-slider-thumb]:size-3 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white sm:block"
              onInput={(event) => {
                if (player.current) {
                  player.current.muted = false;
                  player.current.volume = Number(event.currentTarget.value);
                }
              }}
            />
            <Button
              size="xs"
              variant="ghost"
              className="min-w-10 px-1.5 text-[10px] tabular-nums hover:bg-white/15 hover:text-white"
              aria-label={t('clone.speed')}
              onClick={() => {
                const rates = [0.75, 1, 1.25, 1.5, 2];
                const next = rates[(rates.indexOf(playbackRate) + 1) % rates.length];
                setPlaybackRate(next);
                if (player.current) player.current.playbackRate = next;
              }}
            >
              {playbackRate}×
            </Button>
          </>
        )}
        <span className="ml-auto whitespace-nowrap text-[10px] tabular-nums">
          {formatClock(time)} / {formatClock(duration)}
        </span>
        {!compact && (
          <Button
            size="icon-sm"
            variant="ghost"
            disabled={!canFullscreen}
            aria-label={t(fullscreen ? 'player.exit_fullscreen' : 'player.fullscreen')}
            className="hover:bg-white/15 hover:text-white"
            onClick={() => {
              const action = fullscreen
                ? player.current?.exitFullscreen()
                : player.current?.enterFullscreen();
              void action?.catch(() => setFailed(true));
            }}
          >
            {fullscreen ? <MinimizeIcon /> : <MaximizeIcon />}
          </Button>
        )}
      </div>
    </div>
  );
}
