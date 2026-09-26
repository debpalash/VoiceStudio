import React, { useEffect, useRef } from 'react';
import { Scale, Fingerprint, Play, X } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { PRESETS } from '../utils/constants';
import { generateSpeech } from '../api/generate';
import { Button, Panel, Field, Textarea, Select } from '../ui';
import WaveformPlayer from './WaveformPlayer';
import { useTranslation } from 'react-i18next';

export default function CompareModal({
  open,
  onClose,
  profiles,
  compareText,
  setCompareText,
  compareVoiceA,
  setCompareVoiceA,
  compareVoiceB,
  setCompareVoiceB,
  compareResultA,
  setCompareResultA,
  compareResultB,
  setCompareResultB,
  compareProgress,
  setCompareProgress,
  isComparing,
  setIsComparing,
  steps,
  cfg,
  speed,
  denoise,
  postprocess,
  fileToMediaUrl,
  loadHistory,
}) {
  const drawerRef = useRef(null);
  const { t } = useTranslation();

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.();
    };
    const onPointer = (e) => {
      if (drawerRef.current && !drawerRef.current.contains(e.target)) onClose?.();
    };
    window.addEventListener('keydown', onKey);
    // Defer click-outside so the opening click doesn't immediately close.
    const t = setTimeout(() => window.addEventListener('mousedown', onPointer), 0);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('mousedown', onPointer);
      clearTimeout(t);
    };
  }, [open, onClose]);

  const runCompare = async () => {
    setIsComparing(true);
    setCompareResultA(null);
    setCompareResultB(null);

    const generateVoice = async (voiceId) => {
      setCompareProgress(t('compare.preparing_voice'));
      const formData = new FormData();
      formData.append('text', compareText);
      let fin_prof = voiceId;
      let fin_inst = '';
      if (fin_prof.startsWith('preset:')) {
        const pr = PRESETS.find((p) => p.id === fin_prof.replace('preset:', ''));
        if (pr) {
          const parts = Object.values(pr.attrs).filter((v) => v !== 'Auto');
          fin_inst = parts.join(', ');
        }
        fin_prof = '';
      } else if (profiles.find((p) => p.id === fin_prof)?.instruct) {
        fin_inst = profiles.find((p) => p.id === fin_prof).instruct;
      }
      if (fin_prof) formData.append('profile_id', fin_prof);
      if (fin_inst) formData.append('instruct', fin_inst);
      formData.append('num_step', steps);
      formData.append('guidance_scale', cfg);
      formData.append('speed', speed);
      formData.append('denoise', denoise);
      formData.append('postprocess_output', postprocess);
      const res = await generateSpeech(formData);
      const blob = await res.blob();
      const urls = await fileToMediaUrl(blob, null);
      return urls.audioUrl;
    };

    try {
      setCompareProgress(t('compare.generating_voice_a'));
      const audioA = await generateVoice(compareVoiceA);
      setCompareResultA(audioA);
      setCompareProgress(t('compare.generating_voice_b'));
      const audioB = await generateVoice(compareVoiceB);
      setCompareResultB(audioB);
      setCompareProgress('');
      toast.success(t('compare.comparison_complete'));
      loadHistory();
    } catch (err) {
      toast.error(
        err?.code === 'tts_generation_busy'
          ? t('tts_errors.generation_in_progress')
          : t('compare.play_failed', { message: err.message }),
      );
      setCompareProgress('');
    } finally {
      setIsComparing(false);
    }
  };

  const canCompare = !isComparing && compareVoiceA && compareVoiceB && compareText.trim();

  if (!open) return null;

  return (
    <div
      className="compare-drawer"
      role="dialog"
      aria-modal="false"
      aria-label={t('compare.title')}
    >
      <div className="compare-drawer__sheet" ref={drawerRef}>
        <header className="relative flex items-center gap-[var(--space-3)] p-[10px_var(--space-4)] [border-bottom:1px_solid_var(--chrome-border)] [background:linear-gradient(180deg,rgba(255,255,255,0.02),transparent)]">
          <span
            className="absolute top-[4px] left-1/2 -translate-x-1/2 w-[36px] h-[3px] rounded-[2px] bg-[var(--chrome-border-strong)]"
            aria-hidden="true"
          />
          <span className="inline-flex items-center gap-[6px] [font-family:var(--font-display,inherit)] text-[length:var(--text-md)] font-semibold text-[var(--chrome-fg)]">
            <Scale size={14} /> {t('compare.title')}
          </span>
          <button
            type="button"
            className="ml-auto inline-flex h-[var(--chrome-icon-btn,22px)] w-[var(--chrome-icon-btn,22px)] cursor-pointer items-center justify-center rounded-[var(--chrome-radius-pill)] bg-transparent text-[var(--chrome-fg-muted)] [border:1px_solid_transparent] transition-[background,color,border-color] duration-[var(--dur-fast)] hover:border-transparent hover:bg-[var(--chrome-hover-bg)] hover:text-[var(--chrome-fg)]"
            onClick={onClose}
            aria-label={t('compare.close')}
          >
            <X size={12} />
          </button>
        </header>

        <div className="overflow-y-auto p-[var(--space-4)]">
          <p className="mx-0 mt-0 mb-[var(--space-5)] text-[length:var(--text-base)] text-fg-muted leading-[1.5]">
            {t('compare.desc')}
          </p>

          <Field label={t('compare.test_phrase')}>
            <Textarea
              value={compareText}
              onChange={(e) => setCompareText(e.target.value)}
              rows={2}
              className="resize-none"
            />
          </Field>

          <div className="ui-compare__grid grid grid-cols-2 gap-[var(--space-6)] mt-[var(--space-5)]">
            <CompareSide
              accent="var(--color-brand)"
              label={t('compare.voice_a')}
              profiles={profiles}
              value={compareVoiceA}
              onChange={setCompareVoiceA}
              audio={compareResultA}
            />
            <CompareSide
              accent="var(--color-success)"
              label={t('compare.voice_b')}
              profiles={profiles}
              value={compareVoiceB}
              onChange={setCompareVoiceB}
              audio={compareResultB}
            />
          </div>
        </div>

        <footer className="flex justify-end gap-[var(--space-2)] bg-[var(--chrome-bg)] p-[var(--space-3)_var(--space-4)] [border-top:1px_solid_var(--chrome-border)]">
          <Button variant="ghost" onClick={onClose}>
            {t('compare.close_btn')}
          </Button>
          <Button
            variant="primary"
            loading={isComparing}
            disabled={!canCompare}
            onClick={runCompare}
            leading={!isComparing && <Play size={12} />}
          >
            {isComparing ? compareProgress || t('compare.comparing') : t('compare.compare_btn')}
          </Button>
        </footer>
      </div>
    </div>
  );
}

function CompareSide({ accent, label, profiles, value, onChange, audio }) {
  const { t } = useTranslation();
  return (
    <Panel variant="flat" padding="sm">
      <h3
        className="mx-0 mt-0 mb-[var(--space-4)] flex items-center justify-center gap-[var(--space-3)] [font-family:var(--font-display)] text-[length:var(--text-lg)] [font-weight:var(--weight-bold)]"
        style={{ color: accent }}
      >
        <Fingerprint size={14} /> {label}
      </h3>
      <Field>
        <Select value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">{t('compare.select_voice')}</option>
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
          {PRESETS.map((p) => (
            <option key={p.id} value={`preset:${p.id}`}>
              {p.name} {t('compare.preset_suffix')}
            </option>
          ))}
        </Select>
      </Field>
      {audio ? (
        <WaveformPlayer src={audio} source="compare" className="mt-[var(--space-3)] w-full" />
      ) : (
        <div className="mt-[var(--space-3)] flex h-[32px] items-center justify-center rounded-sm bg-bg-elev-2 text-[length:var(--text-sm)] italic text-fg-subtle">
          {t('compare.no_audio')}
        </div>
      )}
    </Panel>
  );
}
