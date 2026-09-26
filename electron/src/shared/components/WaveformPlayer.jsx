/**
 * WaveformPlayer — the single, shared audio player used for every playback
 * surface in the app (generated TTS output, voice-design output, history items,
 * voice-profile reference + test audio, voice-preview popover, A/B compare).
 *
 * One component so every "play some audio" spot looks and behaves identically:
 * a play/pause button, a click-to-seek wavesurfer waveform, and a time readout.
 * It cooperates with the global single-playback manager (utils/playback.js), so
 * starting one player stops whatever else was playing across the app.
 *
 * `src` may be a URL string or a Blob/File (we object-URL it and clean up).
 *
 * WebKit (Tauri on macOS) can refuse to decode some media in WebAudio; if
 * WaveSurfer fails to init or load we transparently fall back to a native
 * <audio controls> element so playback still works — mirrors WaveformTimeline.
 */
import React, { useEffect, useRef, useState } from 'react';
import WaveSurfer from 'wavesurfer.js';
import { Play, Pause } from 'lucide-react';
import { claimPlayback } from '../utils/playback';
import { isTauri, fileToMediaUrl } from '../utils/media';
import { unlockAudio } from '../utils/audioUnlock';
import { useAppStore } from '../store';

const fmt = (s) => {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, '0')}`;
};

export default function WaveformPlayer({
  src,
  source = 'output', // global-playback-manager label
  autoPlay = false,
  height = 44,
  compact = false,
  onEnded,
  className = '',
}) {
  const containerRef = useRef(null);
  const nativeRef = useRef(null);
  const mediaRef = useRef(null); // in-DOM <audio> driven by WaveSurfer
  const wsRef = useRef(null);
  const releaseRef = useRef(null);
  const autoPlayRef = useRef(autoPlay);
  useEffect(() => {
    autoPlayRef.current = autoPlay;
  }, [autoPlay]);

  const [resolvedUrl, setResolvedUrl] = useState(null);
  const [, setReady] = useState(false);
  const [failed, setFailed] = useState(false); // WaveSurfer unavailable → native fallback
  const [missing, setMissing] = useState(false); // source 404s (stale history) → inert notice
  const [isPlaying, setIsPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);

  // Opt-in dictate-over-playback AEC (parity Action 8): while this player is
  // actually playing AND the pref is on, tap its decoded output as the echo
  // reference for dictation (published to the far-end bus). Gated on isPlaying
  // so an AudioContext exists only for the one active player (the global
  // playback manager stops the others), staying well under the browser's
  // per-page context cap. When the pref is off the default playback path never
  // constructs an AudioContext.
  const aecEnabled = useAppStore((s) => s.aecEnabled);
  const tapDetachRef = useRef(null);
  useEffect(() => {
    let cancelled = false;
    const el = mediaRef.current || nativeRef.current;
    if (aecEnabled && isPlaying && el) {
      import('../utils/aec/playbackTap')
        .then(({ attachPlaybackTap }) => attachPlaybackTap(el))
        .then((detach) => {
          if (cancelled) {
            try {
              detach?.();
            } catch {
              /* ignore */
            }
          } else {
            tapDetachRef.current = detach;
          }
        })
        .catch(() => {
          /* Web Audio unavailable — dictation still works sans AEC */
        });
    }
    return () => {
      cancelled = true;
      const d = tapDetachRef.current;
      tapDetachRef.current = null;
      try {
        d?.();
      } catch {
        /* ignore */
      }
    };
  }, [aecEnabled, isPlaying]);

  // Resolve Blob/File → playable URL. Strings pass through. In Tauri, blob:
  // URLs don't play in WebKit media elements, so blobs are routed through the
  // backend preview endpoint (same path the rest of the app uses).
  useEffect(() => {
    if (!src) {
      setResolvedUrl(null);
      return;
    }
    if (typeof src === 'string') {
      setResolvedUrl(src);
      return;
    }
    if (isTauri) {
      let cancelled = false;
      fileToMediaUrl(src, null)
        .then((urls) => {
          if (!cancelled) setResolvedUrl(urls.audioUrl);
        })
        .catch(() => {
          if (!cancelled) setResolvedUrl(URL.createObjectURL(src));
        });
      return () => {
        cancelled = true;
      };
    }
    const u = URL.createObjectURL(src);
    setResolvedUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [src]);

  // Build / tear down the WaveSurfer instance for the current url.
  useEffect(() => {
    setMissing(false); // a new url gets a fresh chance
    if (!resolvedUrl || failed || !containerRef.current || !mediaRef.current) return;
    setReady(false);
    setIsPlaying(false);
    setDuration(0);
    setCurrentTime(0);

    let ws;
    try {
      ws = WaveSurfer.create({
        container: containerRef.current,
        waveColor: 'rgba(168,153,132,0.45)',
        progressColor: 'rgba(211,134,155,0.75)',
        cursorColor: '#d3869b',
        cursorWidth: 2,
        height,
        barWidth: 2,
        barGap: 1,
        barRadius: 2,
        normalize: true,
        // Drive a REAL in-DOM <audio> element instead of letting WaveSurfer
        // create a detached one: Tauri's WebKit decodes (peaks render) but
        // won't actually output sound for detached/blob-backed media — the
        // same reason WaveformTimeline passes its <video> element.
        // NOTE: the element's `src` is set in JSX, NOT via the `url` option —
        // with an external `media`, wavesurfer only fetches `url` for peaks
        // and never assigns it to the element, leaving play() with nothing
        // to play (waveform drew, click did nothing).
        media: mediaRef.current,
      });
    } catch (initErr) {
      console.warn('WaveformPlayer: WaveSurfer init failed, native fallback:', initErr);
      setFailed(true);
      return;
    }
    wsRef.current = ws;
    // Stale-instance guard: a destroy() that throws mid-teardown (observed
    // under StrictMode double-mount) can leave this instance's media
    // listeners alive — its handlers must become inert, or its duplicate
    // 'play' event re-claims the playback slot and stops... ourselves
    // (the self-pause bug: waveform drew, click silently un-played).
    let stale = false;

    ws.on('ready', () => {
      if (stale) return;
      setDuration(ws.getDuration());
      setReady(true);
      if (autoPlayRef.current) ws.play().catch(() => {});
    });
    ws.on('timeupdate', (t) => {
      if (!stale) setCurrentTime(t);
    });
    ws.on('play', () => {
      if (stale) return;
      setIsPlaying(true);
      // Idempotent: duplicate 'play' events must not re-claim — claiming
      // stops the current owner, which would be this very element.
      if (!releaseRef.current) {
        releaseRef.current = claimPlayback(() => {
          try {
            ws.pause();
          } catch {
            /* noop */
          }
        }, source);
      }
    });
    ws.on('pause', () => {
      if (stale) return;
      setIsPlaying(false);
      if (releaseRef.current) {
        releaseRef.current();
        releaseRef.current = null;
      }
    });
    ws.on('finish', () => {
      setIsPlaying(false);
      if (releaseRef.current) {
        releaseRef.current();
        releaseRef.current = null;
      }
      if (onEnded) onEnded();
    });
    ws.on('error', (err) => {
      const msg = (typeof err === 'string' ? err : err?.message || '').toLowerCase();
      if (err?.name === 'AbortError' || msg.includes('abort')) return; // React cleanup aborts
      if (/\b40[34]\b|not found/.test(msg)) {
        // The audio file is gone (stale history row, cleared outputs dir).
        // A native fallback would just re-request and 404 again — render an
        // inert "missing" notice instead and stop retrying.
        setMissing(true);
        return;
      }
      console.warn('WaveformPlayer: WaveSurfer error, native fallback:', err);
      setFailed(true);
    });

    // (No explicit ws.load — `url` in the create options loads via the media el.)

    return () => {
      stale = true;
      if (releaseRef.current) {
        releaseRef.current();
        releaseRef.current = null;
      }
      // Detach our handlers BEFORE destroy — if destroy throws mid-teardown
      // (the swallowed catch below) the listeners must already be gone.
      try {
        ws.unAll();
      } catch {
        /* noop */
      }
      try {
        ws.destroy();
      } catch {
        /* already gone */
      }
      wsRef.current = null;
    };
  }, [resolvedUrl, failed, height, source, onEnded]);

  const togglePlay = async () => {
    // Browser autoplay policy (Linux FF/Chrome, Android Chrome): WaveSurfer's
    // AudioContext starts suspended until a user gesture. This click IS the
    // gesture — explicitly resume so the subsequent play() succeeds. No-op
    // on macOS where the context was never blocked.
    try {
      await unlockAudio();
    } catch {
      /* ignore — play() will surface errors */
    }
    // playPause is async — a swallowed rejection here is exactly how the
    // "click does nothing" bug hid; log it so playback failures are visible.
    try {
      await wsRef.current?.playPause();
    } catch (e) {
      console.warn('WaveformPlayer: play failed:', e);
    }
  };

  if (!resolvedUrl) return null;

  // Source file no longer exists (stale history row) — inert notice, no retries.
  if (missing) {
    return (
      <div
        className={`flex items-center justify-center w-full min-w-0 box-border opacity-55 border border-solid border-transparent bg-[rgba(168,153,132,0.08)] ${
          compact
            ? 'gap-[8px] py-[4px] px-[8px] rounded-[8px]'
            : 'gap-[10px] py-[6px] px-[10px] rounded-[10px]'
        } ${className}`}
      >
        <span className="text-[10.5px] italic text-[color:var(--chrome-fg-dim,#665c54)]">
          audio file missing
        </span>
      </div>
    );
  }

  // Native fallback — still wires the global playback manager so cross-app
  // "only one thing plays at once" holds even on the degraded path.
  if (failed) {
    return (
      <audio
        ref={nativeRef}
        className={`w-full h-[34px] ${className}`}
        controls
        src={resolvedUrl}
        autoPlay={autoPlay}
        onPlay={() => {
          releaseRef.current = claimPlayback(() => {
            try {
              nativeRef.current?.pause();
            } catch {
              /* noop */
            }
          }, source);
        }}
        onPause={() => {
          if (releaseRef.current) {
            releaseRef.current();
            releaseRef.current = null;
          }
        }}
        onEnded={() => {
          if (releaseRef.current) {
            releaseRef.current();
            releaseRef.current = null;
          }
          if (onEnded) onEnded();
        }}
        onError={() => setMissing(true)}
      />
    );
  }

  return (
    <div
      className={`flex items-center w-full min-w-0 box-border border border-solid border-transparent bg-[rgba(168,153,132,0.08)] ${
        compact
          ? 'gap-[8px] py-[4px] px-[8px] rounded-[8px]'
          : 'gap-[10px] py-[6px] px-[10px] rounded-[10px]'
      } ${className}`}
    >
      {/* Hidden but DOM-attached playback element (see WaveSurfer `media`). */}
      <audio ref={mediaRef} src={resolvedUrl} preload="metadata" style={{ display: 'none' }} />
      {/* `wf-player__btn` class kept as the focus-visible hook (shared a11y ring
          in index.css); all other button visuals are Tailwind utilities. */}
      <button
        type="button"
        className={`wf-player__btn flex-[0_0_auto] inline-flex items-center justify-center border-none rounded-full cursor-pointer text-[color:var(--color-fg-inverse)] bg-[var(--color-brand)] [transition:background_0.15s_ease,transform_0.1s_ease] enabled:hover:bg-[var(--color-brand-hover)] enabled:active:scale-[0.94] disabled:opacity-60 disabled:cursor-default ${
          compact ? 'w-[26px] h-[26px]' : 'w-[30px] h-[30px]'
        }`}
        onClick={togglePlay}
        disabled={!resolvedUrl}
        aria-label={isPlaying ? 'Pause' : 'Play'}
      >
        {isPlaying ? <Pause size={compact ? 13 : 15} /> : <Play size={compact ? 13 : 15} />}
      </button>
      <div
        className="flex-[1_1_auto] min-w-0 cursor-pointer"
        ref={containerRef}
        style={{ height }}
      />
      <span
        className={`flex-[0_0_auto] [font-variant-numeric:tabular-nums] text-[color:rgba(168,153,132,0.85)] whitespace-nowrap ${
          compact ? 'text-[10px]' : 'text-[11px]'
        }`}
      >
        {fmt(currentTime)} / {fmt(duration)}
      </span>
    </div>
  );
}
