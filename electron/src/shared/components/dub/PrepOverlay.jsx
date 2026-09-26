import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader, Square } from 'lucide-react';
import { Button } from '../../ui';

const PREP_FULL = ['download', 'extract', 'demucs', 'scene'];
const PREP_CACHED = ['download', 'extract', 'cached'];

function fmtBytesRate(bps) {
  if (!bps || bps <= 0) return null;
  const units = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
  let v = bps,
    i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

function fmtEta(seconds) {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return null;
  const s = Math.round(seconds);
  if (s < 60) return `${s}s left`;
  const m = Math.floor(s / 60),
    rem = s % 60;
  return rem ? `${m}m ${rem}s left` : `${m}m left`;
}

/**
 * PrepOverlay — the prepare-upload stage indicator.
 * `large` makes the surrounding frame bigger (used for the empty-state drop zone).
 */
function PrepOverlay({ stage, progress, onAbort, large = false }) {
  const { t } = useTranslation();
  const LABEL = {
    download: t('dub.prep_download'),
    extract: t('dub.prep_extract'),
    demucs: t('dub.prep_demucs'),
    scene: t('dub.prep_scene'),
    cached: t('dub.prep_cached'),
  };
  const stages = stage === 'cached' ? PREP_CACHED : PREP_FULL;
  // Elapsed-time ticker for the current stage. Reset whenever
  // stageStartedAt changes (i.e. the backend transitions stages).
  const [elapsedS, setElapsedS] = useState(0);
  const startedAt = progress?.stageStartedAt ?? null;
  useEffect(() => {
    if (!startedAt) {
      setElapsedS(0);
      return undefined;
    }
    setElapsedS(Math.floor((Date.now() - startedAt) / 1000));
    const iv = setInterval(() => {
      setElapsedS(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => clearInterval(iv);
  }, [startedAt]);

  const pct = progress?.percent;
  const hasPct = typeof pct === 'number' && pct >= 0 && pct <= 100;
  const speed = stage === 'download' ? fmtBytesRate(progress?.speedBps) : null;
  const eta = fmtEta(progress?.etaS);
  const elapsedLabel = startedAt
    ? elapsedS < 60
      ? `${elapsedS}s`
      : `${Math.floor(elapsedS / 60)}m ${elapsedS % 60}s`
    : null;
  const detailBits = [
    hasPct ? `${pct}%` : null,
    elapsedLabel ? t('dub.prep_elapsed', { time: elapsedLabel }) : null,
    speed,
    eta,
  ].filter(Boolean);
  const note = stage === 'demucs' && !hasPct ? t('dub.prep_demucs_note') : null;

  const body = (
    <>
      <Loader className="spinner" size={large ? 28 : 20} color="#d3869b" />
      <span className="text-fg font-medium" style={{ fontSize: large ? '0.95rem' : '0.85rem' }}>
        {LABEL[stage] || t('dub.prep_preparing')}
      </span>
      {hasPct && (
        <div className="dub-prep-bar" aria-label={`${pct}%`}>
          <div className="dub-prep-bar__fill" style={{ width: `${pct}%` }} />
        </div>
      )}
      {detailBits.length > 0 && (
        <span className="text-[var(--text-xs)] text-fg-subtle [font-variant-numeric:tabular-nums] tracking-[0.02em]">
          {detailBits.join(' · ')}
        </span>
      )}
      <div
        className={`flex gap-[var(--space-2)] text-[var(--text-xs)] text-fg-subtle ${large ? 'dub-prep-chips--lg' : ''}`}
      >
        {stages.map((s) => (
          <span
            key={s}
            className={`dub-prep-chip ${stage === s ? 'is-active' : ''} ${s === 'cached' ? 'is-cached' : ''}`}
          >
            {t(`dub.prep_chip_${s}`, { defaultValue: s })}
          </span>
        ))}
      </div>
      {note && (
        <span className="text-[var(--text-xs)] text-fg-subtle max-w-[min(320px,90vw)] text-center">
          {note}
        </span>
      )}
      <Button variant="danger" size="sm" onClick={onAbort} leading={<Square size={11} />}>
        {t('dub.prep_stop')}
      </Button>
    </>
  );
  return large ? (
    <div className="flex flex-col items-center gap-[var(--space-5)] dub-prep-overlay--large">
      {body}
    </div>
  ) : (
    <div className="flex flex-col items-center gap-[var(--space-5)]">{body}</div>
  );
}

export default PrepOverlay;
