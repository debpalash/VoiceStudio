import { useEffect, useRef, useState, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { claimPlayback } from '@/lib/audio/playback';
import {
  publishPlaybackClock,
  resetPlaybackClock,
  usePlaybackSeek,
} from '@/lib/audio/playback-clock';

/** Attach local media directly: a preview must load even after its pane was hidden. */
export function DubMediaPreview({
  src,
  kind,
  poster,
  playerRef,
  onCanPlay,
  sourceKey = 'dub-preview',
}: {
  sourceKey?: string;
  src: string;
  kind: 'audio' | 'video';
  poster?: string;
  playerRef?: RefObject<HTMLMediaElement | null>;
  onCanPlay?: () => void;
}) {
  const { t } = useTranslation();
  const local = useRef<HTMLMediaElement>(null);
  const player = playerRef || local;
  const [failed, setFailed] = useState(false);
  const release = useRef<(() => void) | null>(null);
  const seek = usePlaybackSeek(sourceKey);
  const rangeEnd = useRef<number | undefined>(undefined);
  const pending = useRef(seek);
  pending.current = seek;
  const applied = useRef<number | undefined>(undefined);
  const applySeek = () => {
    const element = player.current,
      request = pending.current;
    if (!element || !request || element.readyState === 0 || applied.current === request.id) return;
    applied.current = request.id;
    element.currentTime = Math.min(
      request.time,
      Number.isFinite(element.duration) ? element.duration : request.time,
    );
    rangeEnd.current = request.end;
    if (request.play) void element.play().catch(() => setFailed(true));
  };
  useEffect(() => {
    applySeek();
  }, [seek]);
  useEffect(() => {
    setFailed(false);
    applied.current = undefined;
    return () => {
      release.current?.();
      release.current = null;
      resetPlaybackClock(sourceKey);
    };
  }, [src, kind, sourceKey]);
  const props = {
    src,
    controls: true,
    preload: 'metadata' as const,
    ref: (element: HTMLMediaElement | null) => {
      player.current = element;
    },
    onLoadedMetadata: applySeek,
    onCanPlay: () => {
      setFailed(false);
      applySeek();
      onCanPlay?.();
    },
    onError: () => setFailed(true),
    onPlay: () => {
      release.current?.();
      release.current = claimPlayback(() => {
        player.current?.pause();
      }, sourceKey);
    },
    onPause: () => {
      release.current?.();
      release.current = null;
    },
    onEnded: () => {
      release.current?.();
      release.current = null;
    },
    onTimeUpdate: () => {
      const element = player.current;
      if (!element) return;
      publishPlaybackClock(sourceKey, element.currentTime, element.duration);
      if (rangeEnd.current !== undefined && element.currentTime >= rangeEnd.current) {
        rangeEnd.current = undefined;
        element.pause();
      }
    },
  };
  return (
    <div className="space-y-2">
      {kind === 'video' ? (
        <video
          {...props}
          poster={poster}
          className="aspect-video w-full rounded-xl bg-black"
          playsInline
        />
      ) : (
        <audio {...props} className="w-full" />
      )}
      {failed && (
        <div role="alert" className="space-y-2 text-xs text-destructive">
          <p>{t('player.unavailable')}</p>
          <Button
            size="xs"
            variant="outline"
            onClick={() => {
              setFailed(false);
              player.current?.load();
            }}
          >
            {t('common.retry')}
          </Button>
        </div>
      )}
    </div>
  );
}
