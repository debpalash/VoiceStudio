/**
 * GlobalAudioPlayer — persistent bottom mini-player for "invisible" audio.
 *
 * `playBlobAudio` (playback source 'output') plays the generate auto-play,
 * profile & dub-segment previews, story lines, gallery voices and Projects
 * renders through a bare Audio()/AudioContext with no on-screen player. Its
 * only global affordance used to be the stop-only PlaybackStopPill (#1032) —
 * this bar subsumes it: waveform (peaks decoded once from the blob already in
 * hand), click/drag/keyboard seek, play/pause, elapsed/total time, a source
 * label and a stop button, on every page (mounted once in App.jsx).
 *
 * Exclusion semantics are the pill's, unchanged: ONLY source 'output'
 * renders here. Sources with their own visible player UI (WaveformPlayer
 * instances, 'design-preview', 'demo-output') stay in-place.
 *
 * Layout: a real grid row of .app-container (row 3, directly above the
 * LogsFooter — see index.css). Content in row 2 physically ends at the bar's
 * top edge, so the fixed-overlay overlap class the pill had at 1440×900
 * (covering the studio's Production Overrides row) is impossible by
 * construction. While visible it publishes --audio-dock-height so the fixed
 * overlays that anchor above the footer (FloatingPill, VoicePreview,
 * ExportModal, compare drawer) ride above the bar too.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Pause, Play, Square } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  pauseActivePlayback,
  resumeActivePlayback,
  seekActivePlayback,
  stopActivePlayback,
  usePlaybackTrack,
} from '../utils/playback';

const DOCK_H = 44; // collapsed-chrome scale: header/footer bars are 28px, player needs touch room

const fmt = (s) => {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, '0')}`;
};

// Same visual language as WaveformPlayer's wavesurfer config (bar width 2,
// gap 1, wave/progress colors), just hand-drawn on a canvas — the peaks are
// precomputed in utils/media.js, so no wavesurfer instance (and no second
// decode/fetch) is needed here.
const WAVE_COLOR = 'rgba(168,153,132,0.45)';
const PROGRESS_COLOR = 'rgba(211,134,155,0.75)';
const CURSOR_COLOR = '#d3869b';

function WaveCanvas({ peaks, progress }) {
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext?.('2d');
    if (!ctx) return; // jsdom / very old engines — seek + time still work
    const w = width || canvas.clientWidth;
    const h = canvas.clientHeight || 28;
    if (!w || !h) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);
    const playedX = Math.max(0, Math.min(1, progress)) * w;
    if (peaks && peaks.length) {
      const barW = 2;
      const gap = 1;
      const count = Math.max(1, Math.floor(w / (barW + gap)));
      for (let i = 0; i < count; i++) {
        const x = i * (barW + gap);
        const peak = peaks[Math.floor((i / count) * peaks.length)] || 0;
        const barH = Math.max(2, peak * (h - 2));
        ctx.fillStyle = x + barW <= playedX ? PROGRESS_COLOR : WAVE_COLOR;
        ctx.fillRect(x, (h - barH) / 2, barW, barH);
      }
    } else {
      // No peaks (decode unavailable — e.g. the Tauri streamed fallback):
      // a plain progress track, same colors.
      ctx.fillStyle = WAVE_COLOR;
      ctx.fillRect(0, h / 2 - 1.5, w, 3);
      ctx.fillStyle = PROGRESS_COLOR;
      ctx.fillRect(0, h / 2 - 1.5, playedX, 3);
    }
    // Playhead cursor.
    ctx.fillStyle = CURSOR_COLOR;
    ctx.fillRect(Math.min(playedX, w - 1), 0, 1.5, h);
  }, [peaks, progress, width]);

  return (
    <div ref={wrapRef} className="w-full h-full">
      <canvas ref={canvasRef} className="block w-full h-full" aria-hidden="true" />
    </div>
  );
}

function PlayerBar({ track }) {
  const { t } = useTranslation();
  const { label, paused, currentTime, duration, peaks, canSeek, canPause } = track;
  const scrubbingRef = useRef(false);

  const seekable = canSeek && duration > 0;
  const seekToClientX = (target, clientX) => {
    const rect = target.getBoundingClientRect();
    if (!rect.width) return;
    const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    seekActivePlayback(frac * duration);
  };

  const onPointerDown = (e) => {
    if (!seekable) return;
    scrubbingRef.current = true;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    seekToClientX(e.currentTarget, e.clientX);
  };
  const onPointerMove = (e) => {
    if (!seekable || !scrubbingRef.current) return;
    seekToClientX(e.currentTarget, e.clientX);
  };
  const endScrub = () => {
    scrubbingRef.current = false;
  };
  const onKeyDown = (e) => {
    if (!seekable) return;
    if (e.key === 'ArrowRight') seekActivePlayback(Math.min(duration, currentTime + 5));
    else if (e.key === 'ArrowLeft') seekActivePlayback(Math.max(0, currentTime - 5));
    else if (e.key === 'Home') seekActivePlayback(0);
    else if (e.key === 'End') seekActivePlayback(duration);
    else return;
    e.preventDefault();
  };

  return (
    <div
      className="global-audio-dock flex items-center gap-[10px] px-[10px] [background:var(--chrome-bg)] [border-top:1px_solid_var(--chrome-border)] [color:var(--chrome-fg)] select-none"
      style={{ height: DOCK_H }}
      role="region"
      aria-label={t('player.now_playing')}
      data-testid="global-audio-player"
    >
      {canPause && (
        <button
          type="button"
          className="wf-player__btn shrink-0 inline-flex items-center justify-center w-[28px] h-[28px] border-none rounded-full cursor-pointer text-[color:var(--color-fg-inverse)] bg-[var(--color-brand)] [transition:background_0.15s_ease,transform_0.1s_ease] hover:bg-[var(--color-brand-hover)] active:scale-[0.94]"
          onClick={paused ? resumeActivePlayback : pauseActivePlayback}
          aria-label={paused ? t('player.play') : t('player.pause')}
        >
          {paused ? <Play size={14} /> : <Pause size={14} />}
        </button>
      )}
      <span
        className="shrink-0 max-w-[220px] truncate text-[11.5px] [color:var(--chrome-fg-muted)]"
        title={label || t('player.untitled')}
      >
        {label || t('player.untitled')}
      </span>
      <div
        className={`flex-1 min-w-0 h-[28px] ${seekable ? 'cursor-pointer' : 'cursor-default'}`}
        role="slider"
        tabIndex={seekable ? 0 : -1}
        aria-label={t('player.seek')}
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={Math.round(currentTime)}
        aria-valuetext={`${fmt(currentTime)} / ${fmt(duration)}`}
        aria-disabled={!seekable}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endScrub}
        onPointerCancel={endScrub}
        onKeyDown={onKeyDown}
      >
        <WaveCanvas peaks={peaks} progress={duration > 0 ? currentTime / duration : 0} />
      </div>
      <span className="shrink-0 [font-variant-numeric:tabular-nums] text-[11px] [color:var(--chrome-fg-muted)] whitespace-nowrap">
        {fmt(currentTime)} / {fmt(duration)}
      </span>
      <button
        type="button"
        className="shrink-0 flex items-center justify-center w-[var(--chrome-icon-btn)] h-[var(--chrome-icon-btn)] rounded-[3px] bg-transparent border-0 cursor-pointer [color:var(--chrome-fg-muted)] hover:[color:var(--chrome-fg)] hover:[background:var(--chrome-hover-bg)] focus-visible:[outline:2px_solid_var(--chrome-accent)] focus-visible:[outline-offset:1px]"
        onClick={stopActivePlayback}
        title={t('player.stop')}
        aria-label={t('player.stop')}
      >
        <Square size={12} />
      </button>
    </div>
  );
}

export default function GlobalAudioPlayer() {
  const track = usePlaybackTrack();
  // Exact PlaybackStopPill routing: only bare 'output' playback docks here.
  const visible = track?.source === 'output';

  // Publish the dock height so fixed overlays anchored above the LogsFooter
  // (--logs-footer-height consumers) stack above the bar instead of over it.
  useEffect(() => {
    document.documentElement.style.setProperty(
      '--audio-dock-height',
      visible ? `${DOCK_H}px` : '0px',
    );
    return () => {
      document.documentElement.style.setProperty('--audio-dock-height', '0px');
    };
  }, [visible]);

  if (!visible) return null;
  return <PlayerBar track={track} />;
}
