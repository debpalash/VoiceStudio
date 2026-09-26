import {
  AudioProviderLoader,
  VideoProviderLoader,
  MediaProvider as VidstackMediaProvider,
  MediaPlayer,
  isAudioProvider,
  isDASHProvider,
  isHLSProvider,
  isVideoProvider,
  type MediaPlayerInstance,
  type MediaPlayerProps,
  type MediaProviderProps,
  type AudioSrc,
  type VideoSrc,
} from '@vidstack/react';
import { RemotionProviderLoader } from '@vidstack/react/player/remotion';
import { useEffect, useMemo, useRef, type RefObject } from 'react';
import { claimPlayback } from '@/lib/audio/playback';
import { useAecEnabled } from '@/lib/store/dictation-settings';
import { attachPlaybackTap } from '@shared/utils/aec/playbackTap';

export type { MediaPlayerInstance };
export { useMediaState } from '@vidstack/react';

/**
 * Vidstack includes native audio/video, HLS, DASH, YouTube and Vimeo loaders.
 * Remotion is opt-in, so append it to any workflow-specific loaders here.
 */
export function MediaProvider({ loaders = [], ...props }: MediaProviderProps) {
  const studioLoaders = useMemo(() => [RemotionProviderLoader, ...loaders], [loaders]);
  return <VidstackMediaProvider {...props} loaders={studioLoaders} />;
}

// Audio API paths and blob URLs have no extension. Let the native element
// inspect their actual content type instead of inventing a MIME type.
class StudioAudioLoader extends AudioProviderLoader {
  override canPlay() {
    return true;
  }
}
export const audioLoaders = [StudioAudioLoader];

// Native video API paths have no extension. We already attach the real MIME
// from the uploaded filename, so select the native provider immediately
// instead of issuing a speculative HEAD request before <video> is connected.
class StudioVideoLoader extends VideoProviderLoader {
  override canPlay(src: Parameters<VideoProviderLoader['canPlay']>[0]) {
    return (
      (typeof src === 'object' &&
        src !== null &&
        'type' in src &&
        typeof src.type === 'string' &&
        src.type.startsWith('video/')) ||
      super.canPlay(src)
    );
  }
}
export const videoLoaders = [StudioVideoLoader];
// Vidstack's runtime supports "?" (native MIME sniffing), but its public
// AudioMimeType union omits it. Explicitly suppress blob -> video/object inference.
export function audioSource(src: string): AudioSrc {
  return { src, type: '?' as AudioSrc['type'] };
}

/** Give Vidstack a native MIME when the source route has no file extension. */
export function videoSource(
  src: string,
  filename: string,
  fallbackType?: VideoSrc['type'],
): VideoSrc | string {
  const clean = filename.split(/[?#]/, 1)[0].toLowerCase();
  const type =
    clean.endsWith('.mp4') || clean.endsWith('.m4v')
      ? 'video/mp4'
      : clean.endsWith('.webm')
        ? 'video/webm'
        : clean.endsWith('.ogv') || clean.endsWith('.ogg')
          ? 'video/ogg'
          : clean.endsWith('.mov')
            ? 'video/quicktime'
            : clean.endsWith('.mkv')
              ? 'video/x-matroska'
              : null;
  return type || fallbackType ? { src, type: (type || fallbackType) as VideoSrc['type'] } : src;
}

/** Shared provider, lifecycle and single-playback policy for every workspace. */
export function StudioMediaPlayer({
  sourceKey,
  playbackGroup,
  playerRef,
  children,
  onProviderChange,
  ...props
}: MediaPlayerProps & {
  sourceKey: string;
  playbackGroup?: string;
  playerRef: RefObject<MediaPlayerInstance | null>;
}) {
  const release = useRef<(() => void) | null>(null);
  const aecEnabled = useAecEnabled();
  const mediaElement = useRef<HTMLMediaElement | null>(null);
  const tapDetach = useRef<(() => Promise<void>) | null>(null);
  const tapGeneration = useRef(0);
  const relinquish = () => {
    release.current?.();
    release.current = null;
  };
  const detachTap = () => {
    tapGeneration.current += 1;
    const detach = tapDetach.current;
    tapDetach.current = null;
    void detach?.().catch(() => {});
  };
  const attachTap = () => {
    const element = mediaElement.current;
    if (!aecEnabled || !element || tapDetach.current) return;
    const generation = ++tapGeneration.current;
    void attachPlaybackTap(element)
      .then((detach) => {
        if (generation !== tapGeneration.current) {
          void detach().catch(() => {});
          return;
        }
        tapDetach.current = detach;
      })
      .catch(() => {});
  };
  useEffect(() => {
    if (!aecEnabled) detachTap();
    else if (playerRef.current && !playerRef.current.paused) attachTap();
  }, [aecEnabled]);
  useEffect(
    () => () => {
      detachTap();
      relinquish();
      void playerRef.current?.pause().catch(() => {});
    },
    [sourceKey, playerRef],
  );
  return (
    <MediaPlayer
      {...props}
      ref={playerRef}
      playsInline
      onProviderChange={(provider, event) => {
        detachTap();
        mediaElement.current =
          provider && (isAudioProvider(provider) || isVideoProvider(provider))
            ? provider.media
            : null;
        if (isHLSProvider(provider)) provider.library = () => import('hls.js');
        if (isDASHProvider(provider)) provider.library = () => import('dashjs');
        onProviderChange?.(provider, event);
      }}
      onPlay={(event) => {
        attachTap();
        if (!release.current)
          release.current = claimPlayback(
            () => {
              void playerRef.current?.pause().catch(() => {});
            },
            sourceKey,
            playbackGroup,
          );
        props.onPlay?.(event);
      }}
      onPause={(event) => {
        detachTap();
        relinquish();
        props.onPause?.(event);
      }}
      onEnded={(event) => {
        detachTap();
        relinquish();
        props.onEnded?.(event);
      }}
    >
      {children}
    </MediaPlayer>
  );
}
