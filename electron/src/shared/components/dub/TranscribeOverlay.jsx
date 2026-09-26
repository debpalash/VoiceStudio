import { useTranslation } from 'react-i18next';
import { Loader, Square } from 'lucide-react';
import { Button, Progress } from '../../ui';

/** Seconds → "45s" / "3m 20s". */
function fmtRemaining(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `${m}m ${rem}s` : `${m}m`;
}

/**
 * TranscribeOverlay — Whisper progress + ETA while transcribing.
 *
 * The ETA used to be invented from the video's duration alone:
 *
 *     const est = Math.max(10, Math.ceil(duration / 60) * 3 + 8);
 *
 * i.e. "3 seconds per minute of video" — an assumption of ~20x-realtime
 * transcription. That is roughly true on a CUDA GPU and wildly false on a CPU
 * (#1127: WhisperX on Apple Silicon runs at ~0.33x realtime). For a 16-minute
 * video it predicted 56 seconds; the real answer was ~48 minutes. Past the 56 s
 * mark `est - elapsed` clamped to zero, so it displayed "~0s remaining" and a
 * progress bar frozen at 95% for the next three quarters of an hour.
 *
 * So we no longer guess. `progress` is the real fraction of chunks the backend
 * has actually finished, and the ETA is extrapolated from the rate we are
 * *observing* — which is self-correcting and hardware-agnostic. Until the first
 * chunk lands there is no rate to extrapolate from, and we say nothing rather
 * than inventing a number.
 */
function TranscribeOverlay({ elapsed, duration, progress = 0, onAbort }) {
  const { t } = useTranslation();
  const mm = Math.floor(elapsed / 60);
  const ss = String(elapsed % 60).padStart(2, '0');
  const pct = progress > 0 ? Math.min(99, Math.round(progress * 100)) : null;
  // rate = progress / elapsed  =>  remaining = (1 - progress) / rate
  const remaining = progress > 0.01 && elapsed > 0 ? (elapsed / progress) * (1 - progress) : null;
  return (
    <div className="flex flex-col items-center gap-[var(--space-5)] w-full">
      <div className="flex items-center gap-[var(--space-4)]">
        <Loader className="spinner" size={18} color="#d3869b" />
        <span className="text-fg font-medium text-[var(--text-lg)]">{t('dub.transcribing')}</span>
      </div>
      <div className="flex gap-[var(--space-6)] text-[length:var(--text-md)] text-fg-muted [font-variant-numeric:tabular-nums_slashed-zero]">
        <span>
          ⏱ {mm}:{ss} {t('dub.elapsed')}
        </span>
        {pct != null && <span>{pct}%</span>}
        {remaining != null && (
          <span>
            ~{fmtRemaining(remaining)}
            {t('dub.remaining')}
          </span>
        )}
      </div>
      {duration > 0 && (
        <div className="w-[80%] max-w-[340px]">
          {/* value=null => Progress renders indeterminate; that is exactly right
              before the first chunk lands, when we genuinely don't know yet. */}
          <Progress value={pct} tone="brand" size="sm" />
        </div>
      )}
      <Button variant="danger" size="sm" onClick={onAbort} leading={<Square size={11} />}>
        {t('dub.prep_stop')}
      </Button>
    </div>
  );
}

export default TranscribeOverlay;
