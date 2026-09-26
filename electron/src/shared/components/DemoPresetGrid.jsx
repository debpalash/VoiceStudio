/**
 * DemoPresetGrid — the empty-state of the Voice Design tab.
 *
 * Renders the curated 7-card grid of demo voice designs (see
 * backend/core/personalities.py entries with `is_demo: true`). Each card:
 *   • Title + icon + 1-line description
 *   • ▶ Preview button (plays pre-rendered WAV from /demo_audio, no model
 *     load required — works offline before any engine is installed)
 *   • Use this design → calls `onUse(preset)` which pre-fills text + sliders
 *
 * Only one preview plays at a time. Mounting/unmounting the audio element
 * cancels any in-flight playback so navigating away mid-preview is silent.
 */
import { useEffect, useRef, useState } from 'react';
import { Play, Pause } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { DEMO_ICONS, FALLBACK_VOICE_ICON, stripVoiceEmoji } from '../utils/voiceIcons';
import { API } from '../api/client';
import { claimPlayback, stopActivePlayback } from '../utils/playback';

export default function DemoPresetGrid({ presets, onUse }) {
  const { t } = useTranslation();
  const [playingId, setPlayingId] = useState(null);
  const audioRef = useRef(null);
  const releaseRef = useRef(null);

  // Stop playback on unmount so leaving the Design tab mid-preview goes
  // silent immediately.
  useEffect(() => {
    const audio = audioRef.current;
    return () => {
      if (audio) {
        audio.pause();
        audio.currentTime = 0;
      }
      releaseRef.current?.();
      releaseRef.current = null;
    };
  }, []);

  const handlePreview = (preset) => {
    const audio = audioRef.current;
    if (!audio) return;
    if (playingId === preset.id) {
      // Our claim's stop pauses the element and clears playingId.
      stopActivePlayback();
      return;
    }
    // Claim the global playback slot (#316): stops any other preview/output
    // that is currently playing before this one starts.
    releaseRef.current = claimPlayback(() => {
      audio.pause();
      setPlayingId(null);
    }, 'design-preview');
    audio.src = `${API}${preset.preview_url}`;
    audio.currentTime = 0;
    audio
      .play()
      .then(() => setPlayingId(preset.id))
      .catch((e) => {
        // Most common failure: WAV missing on disk (someone deleted it or
        // build_demos.sh hasn't been run). Fall back gracefully — the card
        // still works for "Use this design".
        console.warn('Preview playback failed:', e);
        releaseRef.current?.();
        releaseRef.current = null;
        setPlayingId(null);
      });
  };

  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-[10px] mb-[12px]">
      {/* Single audio element shared across cards — keeps the "only one
          plays at a time" invariant without per-card state coordination. */}
      <audio
        ref={audioRef}
        onEnded={() => {
          setPlayingId(null);
          releaseRef.current?.();
          releaseRef.current = null;
        }}
        preload="none"
      />
      {presets.map((p) => {
        const isPlaying = playingId === p.id;
        const Icon = DEMO_ICONS[p.id] || FALLBACK_VOICE_ICON;
        return (
          <div
            key={p.id}
            className="flex flex-col gap-[6px] p-[12px] rounded-xl border border-border bg-[rgba(255,255,255,0.02)] [transition:border-color_120ms_ease,background_120ms_ease] hover:border-transparent hover:bg-[rgba(255,255,255,0.04)]"
          >
            <div className="inline-flex items-center gap-[6px]">
              <span className="text-[16px] leading-none" aria-hidden>
                <Icon size={18} />
              </span>
              <span className="text-[13px] font-bold text-fg">{stripVoiceEmoji(p.name)}</span>
            </div>
            <p className="m-0 text-[11px] leading-[1.35] text-fg-muted">{p.description}</p>
            <code className="font-mono text-[10px] text-fg-subtle bg-[rgba(0,0,0,0.22)] px-[6px] py-[2px] rounded-md self-start max-w-full overflow-hidden text-ellipsis whitespace-nowrap">
              {p.instruct}
            </code>
            <div className="flex gap-[6px] mt-auto pt-[4px]">
              <button
                type="button"
                className="demo-preset-card__preview flex-1 inline-flex items-center justify-center gap-[4px] px-[8px] py-[5px] text-[11px] font-semibold rounded-lg border border-border bg-transparent text-fg cursor-pointer [transition:background_100ms_ease,border-color_100ms_ease] hover:bg-[rgba(255,255,255,0.05)] hover:border-transparent"
                onClick={() => handlePreview(p)}
                aria-label={isPlaying ? `Pause ${p.name}` : `Preview ${p.name}`}
                aria-pressed={isPlaying}
              >
                {isPlaying ? <Pause size={12} /> : <Play size={12} />}
                {isPlaying ? t('demo.preset_stop') : t('demo.preset_preview')}
              </button>
              <button
                type="button"
                className="flex-1 inline-flex items-center justify-center gap-[4px] px-[8px] py-[5px] text-[11px] font-semibold rounded-lg border border-transparent bg-[rgba(243,165,182,0.12)] text-fg cursor-pointer [transition:background_100ms_ease,border-color_100ms_ease] hover:border-transparent hover:bg-[rgba(243,165,182,0.22)]"
                onClick={() => onUse(p)}
                aria-label={`Use ${p.name} design`}
              >
                {t('demo.preset_use')}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
