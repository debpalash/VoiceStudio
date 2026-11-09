import { useEffect, useRef, useState } from 'react';
import { PlayIcon, PauseIcon, LoaderCircleIcon, CircleAlertIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  StudioMediaPlayer,
  audioSource,
  audioLoaders,
  MediaProvider,
  type MediaPlayerInstance,
} from './media-player';
import { Button } from './ui/button';
import { beginAppActivity, type AppActivityKind } from '@/lib/app-activity';
interface PreviewProps {
  src: string;
  source: string;
  fallbackSrc?: string;
  activity?: AppActivityKind;
  /** Called once playback of a successfully loaded clip stops (pause or end). */
  onReady?: () => void;
  disabled?: boolean;
  disabledLabel?: string;
}
type Status = 'idle' | 'loading' | 'playing' | 'paused' | 'error';
export function AudioPreviewButton(props: PreviewProps) {
  return <PreviewButton key={props.source + ':' + props.src} {...props} />;
}
/**
 * One-shot preview control. The player is mounted only after the first click:
 * preview routes may render on demand (tens of seconds on a cold engine), so
 * every state must stay clickable — loading cancels, an error retries with a
 * fresh request, and nothing waits on a play() promise that may never settle.
 */
function PreviewButton({
  src,
  source,
  fallbackSrc,
  activity,
  onReady,
  disabled,
  disabledLabel,
}: PreviewProps) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<Status>('idle');
  const [attempt, setAttempt] = useState(0);
  const [fallback, setFallback] = useState(false);
  const player = useRef<MediaPlayerInstance>(null);
  const played = useRef(false);
  const readyFired = useRef(false);
  const finishActivity = useRef<(() => void) | null>(null);
  const settle = () => {
    finishActivity.current?.();
    finishActivity.current = null;
  };
  useEffect(() => settle, []);
  const stopped = () => {
    setStatus('paused');
    // Deferred to the end of playback: onReady typically refetches the item,
    // which changes src and remounts this button mid-clip.
    if (played.current && !readyFired.current) {
      readyFired.current = true;
      onReady?.();
    }
  };
  const start = () => {
    if (activity && !finishActivity.current) finishActivity.current = beginAppActivity(activity);
    setAttempt((n) => n + 1);
    setStatus('loading');
  };
  const click = () => {
    if (status === 'idle' || status === 'error') start();
    else if (status === 'loading') {
      // Unmounting drops the request; the backend keeps rendering into its
      // cache, so the next click picks the finished clip up.
      settle();
      setStatus('idle');
    } else if (status === 'playing') void player.current?.pause().catch(() => {});
    else void player.current?.play().catch(() => setStatus('error'));
  };
  const resolved = fallback && fallbackSrc ? fallbackSrc : src;
  const label =
    disabled && disabledLabel
      ? disabledLabel
      : status === 'error'
        ? t('player.unavailable') + ' · ' + t('common.retry')
        : status === 'loading'
          ? t('common.loading') + ' · ' + t('common.cancel')
          : status === 'playing'
            ? t('player.pause')
            : t('clone.preview_voice');
  return (
    <span className="inline-flex">
      {status !== 'idle' && (
        <StudioMediaPlayer
          key={resolved + ':' + attempt}
          playerRef={player}
          sourceKey={source}
          src={audioSource(resolved)}
          viewType="audio"
          load="eager"
          autoPlay
          onPlaying={() => {
            played.current = true;
            settle();
            setStatus('playing');
          }}
          onPause={stopped}
          onEnded={stopped}
          onAutoPlayFail={() => {
            settle();
            setStatus('paused');
          }}
          onError={() => {
            if (!fallback && fallbackSrc && fallbackSrc !== src) {
              setFallback(true);
              return;
            }
            settle();
            setStatus('error');
          }}
          className="hidden"
        >
          <MediaProvider loaders={audioLoaders} className="hidden" />
        </StudioMediaPlayer>
      )}
      <Button
        variant="ghost"
        size="icon-xs"
        title={label}
        aria-label={label}
        aria-pressed={status === 'playing'}
        aria-busy={status === 'loading'}
        disabled={disabled}
        onClick={click}
      >
        {status === 'error' ? (
          <CircleAlertIcon className="text-destructive" />
        ) : status === 'loading' ? (
          <LoaderCircleIcon className="animate-spin" />
        ) : status === 'playing' ? (
          <PauseIcon />
        ) : (
          <PlayIcon />
        )}
      </Button>
    </span>
  );
}
