import React, { useEffect, useRef, useState } from 'react';
import { Check, Circle, Loader, X, Zap } from 'lucide-react';

/**
 * Live audiobook render feedback (#1216): a progress bar (finished/total
 * chapters), elapsed time, an ETA derived from the average completed-chapter
 * time, and a compact per-chapter list marking each chapter
 * pending / rendering / done / cached / failed.
 *
 * `chapters` is `[{ title, status }]` maintained by AudiobookTab from the stream
 * events (`started`, `chapter`, `chapter_error`, …). Timing lives HERE, off a
 * `performance.now()` ref + a 1 s tick — never `Date.now()`, so tests that mock
 * the clock stay deterministic; the interval only runs at runtime while mounted.
 */
function fmtTime(sec) {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, '0')}`;
}

function StatusIcon({ status }) {
  if (status === 'rendering') return <Loader size={12} className="spin" />;
  if (status === 'done') return <Check size={12} />;
  if (status === 'cached') return <Zap size={12} />;
  if (status === 'failed') return <X size={12} />;
  return <Circle size={12} />; // pending
}

export default function GenerationProgress({ t, chapters = [], assembling = false }) {
  const total = chapters.length;
  const completed = chapters.filter(
    (c) => c.status === 'done' || c.status === 'cached' || c.status === 'failed',
  ).length;
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

  // Elapsed off a mount-time ref; a 1 s tick re-renders so the clock ticks —
  // but only while work remains. Once every chapter is terminal and nothing is
  // assembling, freeze the clock at the finish time and stop the interval, so a
  // still-mounted panel doesn't keep counting past the real elapsed. (The parent
  // also unmounts this on cancel/fail, which clears the interval either way.)
  const terminal = total > 0 && completed === total && !assembling;
  const startRef = useRef(performance.now());
  const frozenRef = useRef(null);
  const [, setTick] = useState(0);
  useEffect(() => {
    if (terminal) {
      if (frozenRef.current == null) frozenRef.current = performance.now();
      return undefined;
    }
    frozenRef.current = null; // resumed (e.g. a re-render that adds chapters)
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [terminal]);
  const elapsed =
    ((terminal ? (frozenRef.current ?? performance.now()) : performance.now()) - startRef.current) /
    1000;
  // ETA = average time per completed chapter × chapters remaining. Only once at
  // least one chapter has finished and some remain (else it's meaningless).
  const eta =
    completed > 0 && completed < total ? (elapsed / completed) * (total - completed) : null;

  return (
    <div className="audiobook-progress" role="status" aria-live="polite">
      <div
        className="flex items-center justify-between gap-[8px] mb-[4px]"
        style={{ fontSize: '0.78rem' }}
      >
        <span>
          {assembling
            ? t('audiobook.assembling')
            : t('audiobook.progress_summary', { current: completed, total })}
        </span>
        <span className="muted" style={{ fontVariantNumeric: 'tabular-nums' }}>
          {fmtTime(elapsed)}
          {eta != null ? ` · ${t('audiobook.eta', { time: fmtTime(eta) })}` : ''}
        </span>
      </div>

      <div
        className="audiobook-progress__bar"
        style={{
          height: 6,
          borderRadius: 3,
          background: 'var(--chrome-bg-inset, rgba(127,127,127,0.18))',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${assembling ? 100 : pct}%`,
            height: '100%',
            background: 'var(--accent, #6366f1)',
            transition: 'width .3s ease',
          }}
        />
      </div>

      <ol
        className="audiobook-progress__list"
        style={{
          listStyle: 'none',
          margin: '8px 0 0',
          padding: 0,
          maxHeight: 168,
          overflowY: 'auto',
        }}
      >
        {chapters.map((c, i) => (
          <li
            key={i}
            className={`audiobook-progress__row status-${c.status}`}
            // A failed chapter used to be a red row with no reason anywhere in
            // the UI (#1321). The row is single-line/ellipsised, so the full
            // text lives in the native tooltip and the head of it renders inline.
            title={c.status === 'failed' && c.error ? c.error : undefined}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              fontSize: '0.72rem',
              padding: '1px 0',
              opacity: c.status === 'pending' ? 0.5 : 1,
              fontWeight: c.status === 'rendering' ? 600 : 400,
            }}
          >
            <StatusIcon status={c.status} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {c.title || t('audiobook.chapter_n', { n: i + 1 })}
            </span>
            {c.status === 'cached' && <span className="muted">· {t('audiobook.cached_tag')}</span>}
            {c.status === 'failed' && (
              <span
                className="muted"
                style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              >
                · {t('audiobook.failed_tag')}
                {c.error ? `: ${c.error}` : ''}
              </span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
