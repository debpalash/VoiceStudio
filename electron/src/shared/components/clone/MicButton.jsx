import { useTranslation } from 'react-i18next';
import { LoaderCircle, Sparkles, Square, Mic } from 'lucide-react';

// `.mic-btn` family migrated from CloneDesignTab.css → Tailwind utilities (fast
// shadcn). Same flat chrome pill; the recording pulse + cleaning spinner reuse
// the global `pulse` / `spin` keyframes (index.css) via arbitrary `animate-[…]`.
const MIC_BASE =
  'flex flex-col items-center justify-center gap-[var(--space-2)] px-4 py-2 min-w-[70px] rounded-[var(--radius-xl)] text-[length:var(--text-xs)] font-semibold cursor-pointer transition-all duration-[var(--dur-base)] ease-[var(--ease-out)]';
const MIC_IDLE =
  'bg-[var(--chrome-hover-bg)] border border-transparent text-[var(--color-fg-muted)] hover:border-[var(--color-danger)] hover:text-[var(--color-danger)]';
const MIC_RECORDING =
  'bg-[color-mix(in_srgb,var(--color-danger)_15%,transparent)] border-2 border-[var(--color-danger)] text-[var(--color-danger)] animate-[pulse_1s_ease-in-out_infinite]';
const MIC_CLEANING =
  'bg-[rgba(184,187,38,0.10)] border border-transparent text-[#b8bb26] cursor-default';

export default function MicButton({
  isCleaning,
  isStarting,
  isRecording,
  recordingTime,
  onStart,
  onStop,
}) {
  const { t } = useTranslation();
  if (isStarting) {
    return (
      <div className={`${MIC_BASE} ${MIC_CLEANING}`} role="status" aria-live="polite">
        <LoaderCircle size={18} className="animate-[spin_1s_linear_infinite]" />
        <span>{t('clone.starting_recording')}</span>
      </div>
    );
  }
  if (isCleaning) {
    return (
      <div className={`${MIC_BASE} ${MIC_CLEANING}`}>
        <Sparkles size={18} className="animate-[spin_1s_linear_infinite]" />
        <span>{t('clone.cleaning')}</span>
      </div>
    );
  }
  if (isRecording) {
    return (
      <button type="button" onClick={onStop} className={`${MIC_BASE} ${MIC_RECORDING}`}>
        <Square size={18} fill="currentColor" />
        <span>{recordingTime}s</span>
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={onStart}
      className={`${MIC_BASE} ${MIC_IDLE}`}
      title={t('clone.record')}
    >
      <Mic size={18} />
      <span>{t('clone.record')}</span>
    </button>
  );
}
