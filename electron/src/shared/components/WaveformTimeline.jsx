import React, {
  useEffect,
  useRef,
  useState,
  useCallback,
  forwardRef,
  useImperativeHandle,
} from 'react';
import WaveSurfer from 'wavesurfer.js';
import MinimapPlugin from 'wavesurfer.js/dist/plugins/minimap.esm.js';
import TimelinePlugin from 'wavesurfer.js/dist/plugins/timeline.esm.js';
import {
  Play,
  Pause,
  ZoomIn,
  ZoomOut,
  SkipBack,
  Loader,
  Keyboard,
  AlertTriangle,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAppStore } from '../store';
import { unlockAudio } from '../utils/audioUnlock';
import { bindWaveformPan } from '../utils/waveformPan';
import SegmentTrack from './SegmentTrack';

// Transport-control button chrome (migrated from the .waveform-btn family in
// index.css). UA <button> padding/font are intentionally left untouched — the
// app ships without a preflight reset, so this matches the pre-migration look.
const WF_BTN_BASE =
  'flex h-[22px] w-[22px] cursor-pointer items-center justify-center rounded-[4px] [transition:all_var(--transition-smooth)] disabled:cursor-not-allowed disabled:opacity-30';
const WF_BTN = `${WF_BTN_BASE} bg-[rgba(255,255,255,0.04)] text-[color:var(--text-secondary)] [border:1px_solid_rgba(255,255,255,0.06)] hover:bg-[rgba(255,255,255,0.1)] hover:text-[color:var(--text-primary)] hover:[border-color:rgba(255,255,255,0.12)]`;
const WF_BTN_PLAY = `${WF_BTN_BASE} bg-[var(--chrome-accent-bg)] text-[color:var(--chrome-accent)] [border:1px_solid_var(--chrome-accent-border)] hover:bg-[color-mix(in_srgb,var(--chrome-accent)_22%,transparent)] hover:shadow-none`;

/**
 * WaveformTimeline
 *
 * Hosts WaveSurfer (waveform / playhead / zoom / scroll) plus the custom
 * SegmentTrack editing lane (#280, item 3 — replaces the Regions plugin).
 * All segment-box positions derive from a single {pxPerSec, scrollLeft}
 * source read off WaveSurfer's wrapper, so the lane stays pixel-aligned
 * with the waveform across zoom/scroll/resize.
 *
 * Props:
 *   audioSrc        – URL / blob URL for audio (WaveSurfer media + waveform source)
 *   videoSrc        – URL / blob URL for the video preview (optional, shown above waveform)
 *   playbackFallbackSrc – audible companion used if the video transport cannot decode
 *   segments        – Array<{ id, start, end, text }>
 *   disabled        – locks drag/resize of segment boxes
 *   overlayContent  – React node rendered as a translucent overlay on the waveform
 *   onsets          – speech-onset times (s) for the snap ticks
 *   selectedSegId   – selected segment id (timeline ↔ table sync)
 *   onSelectSeg     – (id) => void
 *   incrementalPlan – { stale, fresh } cache plan for box tinting
 *   onSegmentCommit – (id, {start,end}, {undo}) => void — one commit per gesture
 *   onSegmentDelete – (id) => void
 *   onPreviewSegment– (seg) => void — synthesize-and-play this segment's dub
 */
function WaveformTimeline(
  {
    audioSrc,
    videoSrc,
    playbackFallbackSrc = audioSrc,
    segments = [],
    disabled = false,
    overlayContent,
    onsets = [],
    selectedSegId = null,
    onSelectSeg,
    incrementalPlan = null,
    onSegmentCommit,
    onSegmentDelete,
    onPreviewSegment,
  },
  ref,
) {
  const { t } = useTranslation();
  const waveContainerRef = useRef(null); // div WaveSurfer draws into
  const videoContainerRef = useRef(null); // div we imperatively append the <video> into
  const wsRef = useRef(null);
  const mediaElRef = useRef(null); // fallback: direct media element if WaveSurfer unavailable
  const playRangeEndRef = useRef(null); // playRange() watcher pauses at this time

  const [ready, setReady] = useState(false);
  // WaveSurfer init threw (WebKit restriction) — media element still works;
  // the SegmentTrack then self-scrolls with a locally fixed pxPerSec.
  const [fallbackMode, setFallbackMode] = useState(false);
  // Single alignment source for the SegmentTrack, read off ws.getWrapper().
  const [metrics, setMetrics] = useState({ pxPerSec: 0, scrollLeft: 0 });
  const [loadError, setLoadError] = useState(false);
  // Specifically: the source returned a non-media response (typically 404
  // HTML). Differentiates from a generic decode failure so the error UI
  // can tell the user the file has moved/been deleted, not just "broken".
  const [sourceMissing, setSourceMissing] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [zoom, setZoom] = useState(50);

  // ── Live "current segment" signal ──────────────────────────────────────────
  // Derive which segment contains the playhead and write the id to the store
  // *only when it changes*. This keeps DubSegmentTable re-renders bounded to
  // segment-crossings instead of the 4-50Hz timeupdate cadence.
  const setDubCurrentSegId = useAppStore((s) => s.setDubCurrentSegId);
  const lastSegIdRef = useRef(null);
  useEffect(() => {
    if (!segments.length) {
      if (lastSegIdRef.current != null) {
        lastSegIdRef.current = null;
        setDubCurrentSegId(null);
      }
      return;
    }
    // Linear scan — fine for ≤200 segments. For longer transcripts we'd
    // switch to a binary search; keep it simple until needed.
    let hit = null;
    for (const s of segments) {
      if (currentTime >= s.start && currentTime < s.end) {
        hit = s.id;
        break;
      }
    }
    if (hit !== lastSegIdRef.current) {
      lastSegIdRef.current = hit;
      setDubCurrentSegId(hit);
    }
  }, [currentTime, segments, setDubCurrentSegId]);
  // Clear on unmount so a stale id can't outlive the editor.
  useEffect(
    () => () => {
      setDubCurrentSegId(null);
    },
    [setDubCurrentSegId],
  );

  // ── Core init — only re-runs when src changes ───────────────────────────────
  useEffect(() => {
    if (!waveContainerRef.current || !audioSrc) return;

    setReady(false);
    setLoadError(false);
    setSourceMissing(false);
    setCurrentTime(0);
    setDuration(0);
    setIsPlaying(false);
    setFallbackMode(false);
    playRangeEndRef.current = null;

    // ── 1. Create the video element imperatively (stable, no React re-renders) ──
    let videoEl = null;
    let videoRetryTimer = null;
    let fallbackAudioEl = null;
    let fallbackSyncCleanup = null;
    let recoveryState = 'idle';
    if (videoSrc && videoContainerRef.current) {
      // Remove prior children + detach listeners explicitly to avoid leaks.
      const c = videoContainerRef.current;
      while (c.firstChild) {
        const child = c.firstChild;
        if (child.tagName === 'VIDEO' || child.tagName === 'AUDIO') {
          try {
            child.pause();
            child.removeAttribute('src');
            child.load?.();
          } catch (_) {}
        }
        c.removeChild(child);
      }
      videoEl = document.createElement('video');
      videoEl.crossOrigin = 'anonymous';
      videoEl.src = videoSrc;
      videoEl.muted = false;
      videoEl.playsInline = true;
      // Load enough data to paint first frame as thumbnail preview.
      videoEl.preload = 'auto';
      videoEl.style.cssText =
        'width:100%;height:100%;object-fit:contain;background:#000;display:block;';
      // Decode and show the first frame as a thumbnail.
      // WebKit won't paint a frame until currentTime is set past 0.
      const showFirstFrame = () => {
        try {
          if (videoEl.currentTime === 0 && isFinite(videoEl.duration) && videoEl.duration > 0) {
            // Seek to the earliest decodable frame.
            videoEl.currentTime = Math.min(0.1, videoEl.duration * 0.01);
          }
        } catch (_) {
          /* ignore */
        }
      };
      videoEl.addEventListener('loadedmetadata', showFirstFrame, { once: true });
      // Fallback if loadedmetadata already fired before listener attached (cached).
      if (videoEl.readyState >= 1) showFirstFrame();
      // Surface media decode failures so future format issues don't
      // present as a silent black box. MediaError codes: 1=aborted,
      // 2=network, 3=decode, 4=src not supported. 3 = real codec/
      // container mismatch (decoder ran but couldn't handle it); 4 =
      // server returned a non-media response (most often a 404 HTML
      // body when the source video moved or was deleted between
      // project save and reload). In either case the rest of the
      // pipeline can't do anything useful, so flip into the
      // user-facing error fallback instead of staring at a black box.
      // Right after a URL ingest the server file may not be finalized yet
      // (yt-dlp still remuxing) — the first load then fails with code 2
      // (network) or 4 (non-media 404 body) and the preview used to stay a
      // black box until the project was reloaded. Retry with backoff before
      // declaring the source dead; code 3 (decode) is terminal immediately.
      let videoRetries = 0;
      videoEl.addEventListener('error', () => {
        const code = videoEl.error?.code;
        if ((code === 2 || code === 4) && videoRetries < 6) {
          videoRetries += 1;
          videoRetryTimer = setTimeout(() => {
            try {
              videoEl.src = videoSrc;
              videoEl.load();
            } catch (_) {
              /* ignore */
            }
          }, 1000 * videoRetries);
          return;
        }
        if (code === 3 || code === 4) {
          console.warn('[WaveformTimeline] video element rejected source', videoSrc, 'code', code);
          // A valid companion WAV can still make this an audio editor. Keep
          // WaveSurfer mounted so its error recovery can swap transports;
          // only the unusable visual surface needs to disappear.
          if (playbackFallbackSrc) {
            videoEl.style.display = 'none';
            return;
          }
          setSourceMissing(code === 4);
          setLoadError(true);
        }
      });
      videoContainerRef.current.appendChild(videoEl);
    }

    // ── 2. Create the media element WaveSurfer will control ──────────────────
    //    Use <video> if we have one (it has audio), otherwise <audio>.
    //    This avoids two media elements fighting each other.
    const mediaEl =
      videoEl ??
      (() => {
        const a = document.createElement('audio');
        a.crossOrigin = 'anonymous';
        a.src = audioSrc;
        a.preload = 'auto';
        return a;
      })();
    // For the audio-only case (server WAV), also set src on the media element
    if (!videoEl) {
      mediaEl.src = audioSrc;
    }
    mediaEl.crossOrigin = 'anonymous'; // helps in sandboxed WebViews
    mediaElRef.current = mediaEl; // keep ref for fallback play/pause

    // ── 3. Init WaveSurfer with that single media element ────────────────────
    let ws;
    try {
      // The compact editor reserves separate space for the ruler/minimap;
      // the waveform itself must not grow to consume those plugin rows.
      const initialHeight = 56;
      const minimap = MinimapPlugin.create({
        height: 20,
        waveColor: 'rgba(168,153,132,0.25)',
        progressColor: 'rgba(211,134,155,0.4)',
        cursorColor: '#d3869b',
      });
      const timeline = TimelinePlugin.create({
        height: 14,
        timeInterval: 1,
        primaryLabelInterval: 5,
        style: {
          fontSize: '9px',
          color: 'rgba(168,153,132,0.5)',
        },
      });
      ws = WaveSurfer.create({
        container: waveContainerRef.current,
        waveColor: 'rgba(168,153,132,0.45)',
        progressColor: 'rgba(211,134,155,0.75)',
        cursorColor: '#d3869b',
        cursorWidth: 2,
        height: initialHeight,
        barWidth: 2,
        barGap: 1,
        barRadius: 2,
        normalize: true,
        media: mediaEl,
        plugins: [minimap, timeline],
      });
    } catch (initErr) {
      console.warn('WaveSurfer init failed (WebKit restriction?):', initErr);
      // Still allow media element to function for video playback
      // Wire native events so play button and time display work
      const waitMeta = () => {
        setDuration(mediaEl.duration || 0);
        setReady(true);
      };
      if (mediaEl.readyState >= 1) waitMeta();
      else mediaEl.addEventListener('loadedmetadata', waitMeta, { once: true });
      mediaEl.addEventListener('timeupdate', () => {
        setCurrentTime(mediaEl.currentTime);
        // playRange watcher — stop at the requested slot end.
        if (
          playRangeEndRef.current != null &&
          mediaEl.currentTime >= playRangeEndRef.current - 0.02
        ) {
          playRangeEndRef.current = null;
          try {
            mediaEl.pause();
          } catch (_) {
            /* ignore */
          }
        }
      });
      mediaEl.addEventListener('play', () => setIsPlaying(true));
      mediaEl.addEventListener('pause', () => {
        setIsPlaying(false);
        playRangeEndRef.current = null;
      });
      mediaEl.addEventListener('ended', () => setIsPlaying(false));
      wsRef.current = null;
      setFallbackMode(true);
      return;
    }

    ws.on('ready', () => {
      setDuration(ws.getDuration());
      setReady(true);
    });
    ws.on('timeupdate', (t) => {
      setCurrentTime(t);
      // playRange watcher — pause when the requested slot finishes.
      if (playRangeEndRef.current != null && t >= playRangeEndRef.current - 0.02) {
        playRangeEndRef.current = null;
        try {
          ws.pause();
        } catch (_) {
          /* ignore */
        }
      }
    });
    ws.on('play', () => setIsPlaying(true));
    ws.on('pause', () => {
      setIsPlaying(false);
      playRangeEndRef.current = null;
    });
    ws.on('finish', () => setIsPlaying(false));

    // WaveSurfer 7's load(undefined, peaks, duration) clears the attached
    // media element's src. The old recovery path therefore rendered fallback
    // peaks but silently disconnected the video transport — exactly the
    // visible-waveform/no-audio failure reported in #1692. Preserve an actual
    // URL on every recovery load. When video decoding caused the failure,
    // move playback to the known-good companion audio and keep the visual
    // video muted + time-synchronised.
    const fallbackSource = playbackFallbackSrc || audioSrc || videoSrc;
    const loadRecoveredPeaks = (peaks, fallbackDuration) => {
      if (videoEl && fallbackSource && fallbackSource !== videoSrc) {
        if (!fallbackAudioEl) {
          fallbackAudioEl = document.createElement('audio');
          fallbackAudioEl.crossOrigin = 'anonymous';
          fallbackAudioEl.preload = 'auto';
          fallbackAudioEl.src = fallbackSource;
          fallbackAudioEl.muted = false;
          fallbackAudioEl.volume = 1;

          const syncVideoTime = (force = false) => {
            if (!videoEl || !fallbackAudioEl) return;
            const next = fallbackAudioEl.currentTime || 0;
            if (force || Math.abs((videoEl.currentTime || 0) - next) > 0.25) {
              try {
                videoEl.currentTime = next;
              } catch (_) {
                /* visual sync is best-effort; audio remains authoritative */
              }
            }
          };
          const playVideo = () => {
            syncVideoTime(true);
            videoEl.muted = true;
            videoEl.play().catch(() => {});
          };
          const pauseVideo = () => videoEl.pause();
          const seekVideo = () => syncVideoTime(true);
          const trackVideo = () => syncVideoTime(false);
          fallbackAudioEl.addEventListener('play', playVideo);
          fallbackAudioEl.addEventListener('pause', pauseVideo);
          fallbackAudioEl.addEventListener('seeking', seekVideo);
          fallbackAudioEl.addEventListener('timeupdate', trackVideo);
          fallbackSyncCleanup = () => {
            fallbackAudioEl?.removeEventListener('play', playVideo);
            fallbackAudioEl?.removeEventListener('pause', pauseVideo);
            fallbackAudioEl?.removeEventListener('seeking', seekVideo);
            fallbackAudioEl?.removeEventListener('timeupdate', trackVideo);
          };

          ws.setMediaElement(fallbackAudioEl);
          mediaElRef.current = fallbackAudioEl;
        }
      }
      recoveryState = 'loading';
      return Promise.resolve(ws.load(fallbackSource, peaks, fallbackDuration));
    };

    const failRecovery = (error, { missing = false } = {}) => {
      recoveryState = 'failed';
      console.warn('Audio fallback failed:', error);
      setReady(false);
      setSourceMissing(missing);
      setLoadError(true);
    };
    const finishRecovery = () => {
      recoveryState = 'ready';
      setReady(true);
    };

    // Handle errors (like Safari refusing to decode .mov in WebAudio)
    ws.on('error', (err) => {
      const errStr =
        typeof err === 'string' ? err.toLowerCase() : (err?.message || '').toLowerCase();
      const errName = err?.name || '';
      if (errName === 'AbortError' || errStr.includes('abort')) {
        return; // Ignore React cleanup aborts
      }
      // Repeated errors from the original video can arrive while its companion
      // is still being fetched/decoded. They do not say anything about that
      // replacement, so let the in-flight attempt settle. Once WaveSurfer has
      // switched to loading the companion, any further error is terminal.
      if (recoveryState === 'decoding' || recoveryState === 'failed') return;
      if (recoveryState === 'loading' || recoveryState === 'ready') {
        failRecovery(err);
        return;
      }
      recoveryState = 'decoding';

      // Decode the exact source recovery will play. Using original-audio
      // peaks with a dubbed companion made its waveform and range controls
      // drift whenever the generated track had different timing.
      if (fallbackSource) {
        fetch(fallbackSource)
          .then((res) => {
            if (!res.ok) throw new Error('HTTP ' + res.status);
            return res.arrayBuffer();
          })
          .then((buffer) => {
            const actx = new (window.AudioContext || window.webkitAudioContext)();
            return actx.decodeAudioData(buffer);
          })
          .then((audioBuffer) => {
            const channelData = audioBuffer.getChannelData(0);
            // Same explicit-readiness guard as the NotSupportedError branch
            // above — don't depend on the 'ready' event re-firing for this
            // manually-decoded recovery load.
            loadRecoveredPeaks([channelData], audioBuffer.duration)
              .then(finishRecovery)
              .catch((loadErr) => failRecovery(loadErr));
          })
          .catch((decodeErr) => {
            // HTTP 404 on the companion audio means the source file is
            // gone (typically: project loaded after the underlying media
            // was moved/deleted). Surface that explicitly — an empty
            // waveform fallback would just confuse the user into thinking
            // the file is silent. Other decode failures still fall back
            // to empty peaks so the media element can still play.
            const isMissing = /\bHTTP 404\b/.test(String(decodeErr?.message || decodeErr));
            if (isMissing) {
              failRecovery(decodeErr, { missing: true });
              return;
            }
            // If this is the same timeline as audioSrc, the media element may
            // still play even though WebAudio cannot decode it. A dubbed
            // companion is a different timeline, so never fake its duration
            // from the original media.
            if (fallbackSource !== audioSrc) {
              failRecovery(decodeErr);
              return;
            }
            console.warn('Audio decode fallback failed, loading with empty peaks:', decodeErr);
            try {
              const emptyPeaks = new Float32Array(1000).fill(0);
              loadRecoveredPeaks([emptyPeaks], mediaEl.duration || 60)
                .then(finishRecovery)
                .catch((loadErr) => failRecovery(loadErr));
            } catch (loadErr) {
              failRecovery(loadErr);
            }
          });
      } else {
        failRecovery(err);
      }
    });

    wsRef.current = ws;

    return () => {
      if (videoRetryTimer) clearTimeout(videoRetryTimer);
      // Gracefully stop before destroy to avoid WaveSurfer's internal
      // fetch progress handler logging "AbortError: Fetch is aborted".
      try {
        ws.pause();
      } catch (_) {}
      try {
        ws.cancelAudioFetch?.();
      } catch (_) {}
      fallbackSyncCleanup?.();
      if (fallbackAudioEl) {
        try {
          fallbackAudioEl.pause();
          fallbackAudioEl.removeAttribute('src');
          fallbackAudioEl.load?.();
        } catch (_) {}
      }
      // Empty the media source before destroy so any in-flight fetch
      // resolves its AbortController without a stale reference.
      if (mediaEl && !videoEl) {
        try {
          mediaEl.pause();
          mediaEl.removeAttribute('src');
          mediaEl.load?.();
        } catch (_) {}
      }
      try {
        ws.destroy();
      } catch (_) {}
      wsRef.current = null;
      mediaElRef.current = null;
      // Clear the imperatively-created video element (release src so browser frees decoder)
      const c = videoContainerRef.current;
      if (c) {
        while (c.firstChild) {
          const child = c.firstChild;
          if (child.tagName === 'VIDEO' || child.tagName === 'AUDIO') {
            try {
              child.pause();
              child.removeAttribute('src');
              child.load?.();
            } catch (_) {}
          }
          c.removeChild(child);
        }
      }
      setReady(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audioSrc, videoSrc, playbackFallbackSrc]);

  // Native metadata/playback is usable before the full waveform is decoded.
  useEffect(() => {
    const media = mediaElRef.current;
    if (!media) return;
    const metadata = () => {
      if (Number.isFinite(media.duration) && media.duration > 0) setDuration(media.duration);
    };
    const time = () => setCurrentTime(media.currentTime || 0);
    const playing = () => setIsPlaying(!media.paused);
    metadata();
    media.addEventListener('loadedmetadata', metadata);
    media.addEventListener('durationchange', metadata);
    media.addEventListener('timeupdate', time);
    media.addEventListener('play', playing);
    media.addEventListener('pause', playing);
    return () => {
      media.removeEventListener('loadedmetadata', metadata);
      media.removeEventListener('durationchange', metadata);
      media.removeEventListener('timeupdate', time);
      media.removeEventListener('play', playing);
      media.removeEventListener('pause', playing);
      setDuration(0);
    };
  }, [audioSrc, videoSrc, playbackFallbackSrc]);

  // ── Alignment metrics — the single {pxPerSec, scrollLeft} source ────────────
  // Everything the SegmentTrack draws derives from these two numbers, read
  // off WaveSurfer's wrapper after every zoom/scroll/redraw/resize. rAF-
  // throttled so scroll events can't render-storm.
  const metricsRafRef = useRef(0);
  const syncMetrics = useCallback(() => {
    if (metricsRafRef.current) return;
    metricsRafRef.current = requestAnimationFrame(() => {
      metricsRafRef.current = 0;
      const ws = wsRef.current;
      if (!ws) return;
      let wrap = null;
      try {
        wrap = ws.getWrapper?.();
      } catch (_) {
        /* destroyed */
      }
      if (!wrap) return;
      const scrollEl = wrap.parentElement || wrap;
      const dur = ws.getDuration?.() || 0;
      const next = {
        pxPerSec: dur > 0 ? wrap.scrollWidth / dur : 0,
        scrollLeft: scrollEl.scrollLeft || 0,
      };
      setMetrics((m) =>
        m.pxPerSec === next.pxPerSec && m.scrollLeft === next.scrollLeft ? m : next,
      );
    });
  }, []);
  useEffect(
    () => () => {
      if (metricsRafRef.current) cancelAnimationFrame(metricsRafRef.current);
    },
    [],
  );

  useEffect(() => {
    if (!ready || !wsRef.current) return undefined;
    const ws = wsRef.current;
    ws.on('redraw', syncMetrics);
    ws.on('zoom', syncMetrics);
    ws.on('scroll', syncMetrics);
    let wrap = null;
    try {
      wrap = ws.getWrapper?.();
    } catch (_) {
      /* ignore */
    }
    const scrollEl = wrap?.parentElement || null;
    const unbindPan = wrap ? bindWaveformPan(wrap) : () => {};
    if (scrollEl) scrollEl.addEventListener('scroll', syncMetrics, { passive: true });
    const ro = scrollEl ? new ResizeObserver(syncMetrics) : null;
    if (ro && scrollEl) ro.observe(scrollEl);
    syncMetrics();
    return () => {
      unbindPan();
      try {
        ws.un('redraw', syncMetrics);
        ws.un('zoom', syncMetrics);
        ws.un('scroll', syncMetrics);
      } catch (_) {
        /* destroyed */
      }
      if (scrollEl) scrollEl.removeEventListener('scroll', syncMetrics);
      if (ro) ro.disconnect();
    };
  }, [ready, syncMetrics]);

  // ── Zoom ────────────────────────────────────────────────────────────────────
  // pendingZoomAnchorRef keeps the time under the cursor fixed across a
  // Ctrl/Cmd-wheel zoom: after ws.zoom() we re-read the real pxPerSec and
  // restore the anchor's pixel position.
  const pendingZoomAnchorRef = useRef(null);
  useEffect(() => {
    if (wsRef.current && ready) {
      try {
        wsRef.current.zoom(zoom);
        const anchor = pendingZoomAnchorRef.current;
        if (anchor) {
          pendingZoomAnchorRef.current = null;
          const wrap = wsRef.current.getWrapper?.();
          const scrollEl = wrap?.parentElement;
          const dur = wsRef.current.getDuration?.() || 0;
          if (wrap && scrollEl && dur > 0) {
            const pps = wrap.scrollWidth / dur;
            scrollEl.scrollLeft = Math.max(0, anchor.time * pps - anchor.cursorX);
          }
        }
      } catch (err) {
        console.warn('WaveSurfer zoom failed:', err);
      }
      syncMetrics();
    }
  }, [zoom, ready, syncMetrics]);

  // Imperative seek + scroll hooks — used by the transcript table to jump the
  // player to a clicked row, and by the mouse-wheel handler below.
  useImperativeHandle(
    ref,
    () => ({
      seekTo(time) {
        const ws = wsRef.current;
        if (ws && ready) {
          try {
            const d = ws.getDuration?.() || duration || 0;
            const t = Math.max(0, Math.min(time, d || time));
            if (typeof ws.setTime === 'function') ws.setTime(t);
            else if (typeof ws.seekTo === 'function' && d > 0) ws.seekTo(t / d);
          } catch (err) {
            console.warn('WaveSurfer seek failed:', err);
          }
          return;
        }
        const el = mediaElRef.current;
        if (el && Number.isFinite(time)) {
          try {
            el.currentTime = Math.max(0, time);
          } catch (err) {
            console.warn('media seek failed:', err);
          }
        }
      },
    }),
    [ready, duration],
  );

  // Horizontal mouse-wheel → scroll the waveform. WaveSurfer doesn't bind this
  // by default; users expect to spin the wheel over a long timeline. We also
  // honour vertical wheel (most mice) as horizontal motion.
  const onWaveWheel = useCallback(
    (e) => {
      const ws = wsRef.current;
      if (!ws || !ready) return;
      const wrap = ws.getWrapper?.();
      if (!wrap) return;
      const scrollEl = wrap.parentElement || wrap;
      // Ctrl/Cmd + wheel = zoom centered on the cursor (#280, item 3).
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const dur = ws.getDuration?.() || 0;
        if (dur <= 0) return;
        const rect = scrollEl.getBoundingClientRect();
        const cursorX = e.clientX - rect.left;
        const curPps = wrap.scrollWidth / dur;
        const timeAt = (scrollEl.scrollLeft + cursorX) / curPps;
        const factor = e.deltaY < 0 ? 1.25 : 0.8;
        setZoom((z) => {
          const nz = Math.min(300, Math.max(10, Math.round(z * factor)));
          if (nz !== z) pendingZoomAnchorRef.current = { time: timeAt, cursorX };
          return nz;
        });
        return;
      }
      const dx = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
      if (!dx) return;
      e.preventDefault();
      scrollEl.scrollLeft += dx;
    },
    [ready],
  );

  // playRange — seek + play, pausing automatically at `end` via the
  // timeupdate watcher wired in the init effect. Used by the SegmentTrack
  // ("play this slot") on whatever media the player currently holds, so it
  // respects the original/dubbed preview toggle for free.
  const playRange = useCallback(async (start, end) => {
    // Same autoplay-policy unlock as togglePlay (#595): resume the suspended
    // AudioContext on this user gesture before kicking off playback.
    try {
      await unlockAudio();
    } catch {
      /* ignore */
    }
    playRangeEndRef.current = end;
    const ws = wsRef.current;
    if (ws) {
      try {
        ws.setTime(start);
        ws.play();
      } catch (_) {
        playRangeEndRef.current = null;
      }
      return;
    }
    const el = mediaElRef.current;
    if (el) {
      try {
        el.currentTime = start;
        el.play().catch(() => {});
      } catch (_) {
        playRangeEndRef.current = null;
      }
    }
  }, []);

  // Scroll a given time into view (keyboard focus moved to an off-screen box).
  const ensureTimeVisible = useCallback((timeS) => {
    const ws = wsRef.current;
    if (!ws) return;
    const wrap = ws.getWrapper?.();
    const scrollEl = wrap?.parentElement;
    const dur = ws.getDuration?.() || 0;
    if (!wrap || !scrollEl || dur <= 0) return;
    const pps = wrap.scrollWidth / dur;
    const x = timeS * pps;
    if (x < scrollEl.scrollLeft || x > scrollEl.scrollLeft + scrollEl.clientWidth) {
      scrollEl.scrollLeft = Math.max(0, x - scrollEl.clientWidth * 0.3);
    }
  }, []);

  const togglePlay = useCallback(async () => {
    // Browser autoplay policy (Linux FF/Chrome, Windows WebView2, Android
    // Chrome): WaveSurfer's AudioContext is constructed at mount — before any
    // user gesture — and stays "suspended", so playPause() resolves without a
    // sound and the dub video preview just sits there (#595, same class as
    // #510 already fixed in WaveformPlayer). This click IS the gesture —
    // explicitly resume before play. No-op on macOS where it never blocked.
    try {
      await unlockAudio();
    } catch {
      /* play() will surface real errors */
    }
    if (wsRef.current && ready) {
      try {
        await wsRef.current.playPause();
      } catch (e) {
        console.warn('WaveformTimeline: play failed:', e);
      }
    } else if (mediaElRef.current) {
      // Fallback: control the native media element directly
      const el = mediaElRef.current;
      if (el.paused) {
        el.play().catch((e) => console.warn('WaveformTimeline: play failed:', e));
      } else {
        el.pause();
      }
    }
  }, [ready]);
  const seekTo = useCallback(
    (t) => {
      if (wsRef.current && ready) {
        wsRef.current.setTime(t);
      } else if (mediaElRef.current) {
        mediaElRef.current.currentTime = t;
      }
    },
    [ready],
  );

  const fmt = (t) => {
    const m = Math.floor(t / 60);
    const s = (t % 60).toFixed(1);
    return `${m}:${s.padStart(4, '0')}`;
  };

  // ── Keyboard shortcuts (J/K/L video-editor style) ──────────────────────────
  useEffect(() => {
    const handler = (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.key === ' ' && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        togglePlay();
      }
      if (e.key === 'j') seekTo(Math.max(0, currentTime - 5));
      if (e.key === 'l') seekTo(Math.min(duration, currentTime + 5));
      if (e.key === 'k') togglePlay();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [currentTime, duration, togglePlay, seekTo]);

  const playbackControls = (
    <div
      className={`wfm-controls ${videoSrc ? 'wfm-controls--overlay' : ''}`}
      role="toolbar"
      aria-label={t('waveform.controls')}
    >
      <div className="wfm-controls__group">
        <button
          className={WF_BTN}
          onClick={() => seekTo(0)}
          title={t('waveform.restart')}
          aria-label={t('waveform.restart')}
        >
          <SkipBack size={11} />
        </button>
        <button
          className={WF_BTN_PLAY}
          onClick={togglePlay}
          disabled={!ready && duration <= 0}
          aria-label={isPlaying ? t('player.pause') : t('player.play')}
        >
          {isPlaying ? <Pause size={11} /> : <Play size={11} />}
        </button>
        <span
          className="rounded-[3px] bg-[var(--chrome-hover-bg)] px-[5px] py-[1px] text-[0.62rem] text-[color:var(--chrome-fg-muted)] [border:1px_solid_var(--chrome-border)] [font-family:var(--font-mono)] [font-variant-numeric:tabular-nums]"
          aria-live="off"
        >
          {fmt(currentTime)} / {fmt(duration)}
        </span>
        <span className="wfm-kbd-hint" title={t('waveform.shortcuts')}>
          <Keyboard size={10} />
        </span>
      </div>
      <div className="wfm-controls__group">
        <button
          className={WF_BTN}
          onClick={() => setZoom((z) => Math.max(10, z - 20))}
          aria-label={t('trimmer.zoom_out')}
        >
          <ZoomOut size={11} />
        </button>
        <input
          type="range"
          min="10"
          max="300"
          value={zoom}
          onChange={(e) => setZoom(Number(e.target.value))}
          className="!mt-0 !h-[2px] !w-[60px]"
          aria-label={t('waveform.zoom')}
        />
        <button
          className={WF_BTN}
          onClick={() => setZoom((z) => Math.min(300, z + 20))}
          aria-label={t('trimmer.zoom_in')}
        >
          <ZoomIn size={11} />
        </button>
      </div>
    </div>
  );

  // ── Error fallback ──────────────────────────────────────────────────────────
  if (loadError) {
    return (
      <div className="mb-[6px]">
        <div className="wfm-error">
          <AlertTriangle size={13} style={{ flexShrink: 0, marginRight: 6 }} />
          {sourceMissing ? t('waveform.source_missing') : t('waveform.load_failed')}
        </div>
      </div>
    );
  }

  return (
    <div className="mb-[6px] wfm-layout" role="region" aria-label={t('waveform.timeline')}>
      {/* Video transport and one compact, aligned waveform/transcript surface. */}
      <div className="wfm-stack">
        {/* Keep the imperative media host separate from React-owned controls. */}
        {videoSrc && (
          <div className="wfm-video-preview">
            <div ref={videoContainerRef} className="wfm-video-media" />
            {playbackControls}
          </div>
        )}

        <div className="wfm-editor" onWheel={onWaveWheel}>
          <div className="wfm-wave-wrap">
            <div ref={waveContainerRef} className="waveform-container wfm-wave-inner" />

            {/* Loading shimmer */}
            {!ready && !loadError && (
              <div className="wfm-loading">
                <Loader className="spinner" size={12} color="#d3869b" />
                <span className="wfm-loading__text">{t('waveform.loading')}</span>
              </div>
            )}

            {/* Overlay slot — transcription / dubbing progress */}
            {overlayContent && <div className="wfm-overlay">{overlayContent}</div>}
          </div>

          {/* Segment editing lane — pixel-aligned with the waveform via the
            shared {pxPerSec, scrollLeft} metrics. In the WebKit fallback
            (no WaveSurfer) it self-scrolls with a fixed px/sec scale. */}
          {ready && segments.length > 0 && (
            <SegmentTrack
              segments={segments}
              pxPerSec={fallbackMode ? zoom : metrics.pxPerSec}
              scrollLeft={metrics.scrollLeft}
              duration={duration}
              currentTime={currentTime}
              onsets={onsets}
              disabled={disabled}
              selectedId={selectedSegId}
              onSelectSeg={onSelectSeg}
              incrementalPlan={incrementalPlan}
              onCommit={onSegmentCommit}
              onDelete={onSegmentDelete}
              onPlayRange={playRange}
              onPreviewSegment={onPreviewSegment}
              onEnsureVisible={ensureTimeVisible}
              selfScroll={fallbackMode}
            />
          )}
        </div>
      </div>

      {!videoSrc && playbackControls}
    </div>
  );
}

export default forwardRef(WaveformTimeline);
