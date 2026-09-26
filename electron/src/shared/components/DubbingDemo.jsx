/**
 * DubbingDemo — side-by-side player for the synthetic dubbing demo.
 *
 * Reads /demo_audio/demo/dubbing/manifest.json (mounted via FastAPI's
 * /demo_audio static route), shows the English source video on the left,
 * a language-pickable dubbed variant on the right, and a "Try it with
 * your own video" CTA below.
 *
 * Renders on the DubTab idle state when no project / file is loaded.
 * Dismissable via `onDismiss` — the parent passes a setter that hides
 * the demo and falls back to the existing drop-zone UI.
 */
import { useEffect, useRef, useState } from 'react';
import { Play, Film, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { API, apiFetch } from '../api/client';

// Shared container surface for the demo + its loading placeholder.
const SHELL = 'rounded-[10px] border border-border bg-[rgba(255,255,255,0.02)]';

export default function DubbingDemo({ onDismiss }) {
  const { t } = useTranslation();
  const [manifest, setManifest] = useState(null);
  const [error, setError] = useState(null);
  const [pickedCode, setPickedCode] = useState('es');
  const [syncPlay, setSyncPlay] = useState(true);
  const sourceRef = useRef(null);
  const dubbedRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch(`${API}/demo_audio/demo/dubbing/manifest.json`)
      .then((r) => r.json())
      .then((j) => {
        if (!cancelled) setManifest(j);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message || String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Sync the two players when syncPlay is on — play/pause/seek the
  // English source drives the dubbed clone (and vice versa).
  useEffect(() => {
    if (!syncPlay) return;
    const a = sourceRef.current;
    const b = dubbedRef.current;
    if (!a || !b) return;

    const onPlay = (src, dst) => () => {
      // Avoid feedback loop — only play target if it's currently paused.
      if (dst.paused) {
        dst.currentTime = src.currentTime;
        dst.play().catch(() => {});
      }
    };
    const onPause = (dst) => () => {
      if (!dst.paused) dst.pause();
    };
    const onSeek = (src, dst) => () => {
      dst.currentTime = src.currentTime;
    };

    const aPlay = onPlay(a, b);
    const bPlay = onPlay(b, a);
    const aPause = onPause(b);
    const bPause = onPause(a);
    const aSeek = onSeek(a, b);
    const bSeek = onSeek(b, a);

    a.addEventListener('play', aPlay);
    b.addEventListener('play', bPlay);
    a.addEventListener('pause', aPause);
    b.addEventListener('pause', bPause);
    a.addEventListener('seeked', aSeek);
    b.addEventListener('seeked', bSeek);
    return () => {
      a.removeEventListener('play', aPlay);
      b.removeEventListener('play', bPlay);
      a.removeEventListener('pause', aPause);
      b.removeEventListener('pause', bPause);
      a.removeEventListener('seeked', aSeek);
      b.removeEventListener('seeked', bSeek);
    };
  }, [syncPlay, pickedCode]);

  if (error) {
    return null; // No demo manifest yet — silently fall through to drop zone.
  }
  if (!manifest) {
    return (
      <div className={`${SHELL} p-[18px] text-center text-[11px] text-fg-muted`}>
        {t('demo.dubbing_loading')}
      </div>
    );
  }

  const source = manifest.source;
  const dubbed = manifest.dubbed?.find((d) => d.code === pickedCode) || manifest.dubbed?.[0];
  if (!dubbed) return null;

  const base = `${API}/demo_audio/demo/dubbing`;

  return (
    <div className={`${SHELL} flex flex-col gap-[10px] p-[14px]`}>
      <header className="flex items-center justify-between gap-[12px]">
        <div className="inline-flex items-center gap-[6px] text-[12px] font-bold text-fg">
          <Film size={13} /> {t('demo.dubbing_title')}
        </div>
        <div className="inline-flex items-center gap-[8px]">
          <label className="inline-flex items-center gap-[5px] text-[11px] text-fg-muted cursor-pointer select-none">
            <input
              type="checkbox"
              checked={syncPlay}
              onChange={(e) => setSyncPlay(e.target.checked)}
              className="accent-[var(--color-brand)]"
            />
            {t('demo.dubbing_sync')}
          </label>
          {onDismiss && (
            <button
              type="button"
              className="border-0 bg-transparent text-fg-muted cursor-pointer px-[4px] py-[2px] rounded-md inline-flex hover:bg-[rgba(255,255,255,0.05)] hover:text-fg"
              onClick={onDismiss}
              aria-label={t('demo.dubbing_dismiss')}
            >
              <X size={13} />
            </button>
          )}
        </div>
      </header>

      <div className="grid grid-cols-2 max-[720px]:grid-cols-1 gap-[12px]">
        <div className="flex flex-col gap-[6px]">
          <div className="text-[11px] font-semibold text-fg">
            {source.label}{' '}
            <span className="font-normal text-fg-muted text-[10px] ml-[4px] uppercase tracking-[0.05em]">
              · {t('demo.original_tag')}
            </span>
          </div>
          <video
            ref={sourceRef}
            src={`${base}/${source.video}`}
            controls
            playsInline
            preload="metadata"
            className="w-full rounded-[6px] bg-black [outline:1px_solid_rgba(255,255,255,0.06)] [outline-offset:-1px]"
          />
          <p className="m-0 text-[10.5px] leading-[1.4] text-fg-muted px-[6px] py-[4px] bg-bg-elev-3 rounded-md">
            {source.script}
          </p>
        </div>
        <div className="flex flex-col gap-[6px]">
          <div className="text-[11px] font-semibold text-fg">
            {dubbed.label}{' '}
            <span className="font-normal text-fg-muted text-[10px] ml-[4px] uppercase tracking-[0.05em]">
              · {t('demo.dubbed_tag')}
            </span>
          </div>
          <video
            ref={dubbedRef}
            src={`${base}/${dubbed.video}`}
            controls
            playsInline
            preload="metadata"
            dir={dubbed.dir}
            className="w-full rounded-[6px] bg-black [outline:1px_solid_rgba(255,255,255,0.06)] [outline-offset:-1px]"
          />
          <p
            className="m-0 text-[10.5px] leading-[1.4] text-fg-muted px-[6px] py-[4px] bg-bg-elev-3 rounded-md"
            dir={dubbed.dir}
          >
            {dubbed.script}
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-[6px] pt-[4px]">
        <span className="text-[10px] text-fg-muted uppercase tracking-[0.05em] mr-[4px]">
          {t('demo.dubbing_picker')}
        </span>
        {manifest.dubbed.map((d) => (
          <button
            key={d.code}
            type="button"
            className={`text-[11px] px-[10px] py-[3px] rounded-[999px] border border-border bg-transparent text-fg-muted cursor-pointer [transition:background_100ms_ease,border-color_100ms_ease,color_100ms_ease] hover:bg-[rgba(255,255,255,0.04)] hover:text-fg ${pickedCode === d.code ? 'bg-[rgba(243,165,182,0.18)] border-transparent text-[#fff9ef]' : ''}`}
            onClick={() => setPickedCode(d.code)}
          >
            {d.label}
          </button>
        ))}
      </div>

      {onDismiss && (
        <button
          type="button"
          className="self-end inline-flex items-center gap-[6px] px-[12px] py-[6px] text-[11px] font-semibold rounded-lg border border-transparent bg-[rgba(243,165,182,0.12)] text-fg cursor-pointer hover:bg-[rgba(243,165,182,0.22)]"
          onClick={onDismiss}
        >
          <Play size={12} /> {t('demo.dubbing_cta')}
        </button>
      )}
    </div>
  );
}
