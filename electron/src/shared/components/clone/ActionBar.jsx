import {
  SlidersHorizontal,
  Settings2,
  ChevronUp,
  ChevronDown,
  Play,
  Square,
  Focus,
  Gauge,
  Timer,
  Thermometer,
  Shuffle,
  Layers,
  Clock,
  AudioLines,
  Sparkles,
} from 'lucide-react';
import { Button, Progress } from '../../ui';
import MultiLangPicker from '../MultiLangPicker';
import ALL_LANGUAGES from '../../languages.json';
import { LANG_CODES } from '../../utils/languages';
import { stopActivePlayback } from '../../utils/playback';

const CLONE_LANGUAGES = ALL_LANGUAGES.map((label) => ({
  label,
  code: LANG_CODES.find((language) => language.label === label)?.code || label,
}));

export default function ActionBar({
  t,
  showOverrides,
  setShowOverrides,
  cfg,
  setCfg,
  speed,
  setSpeed,
  tShift,
  setTShift,
  posTemp,
  setPosTemp,
  classTemp,
  setClassTemp,
  layerPenalty,
  setLayerPenalty,
  duration,
  setDuration,
  denoise,
  setDenoise,
  postprocess,
  setPostprocess,
  language,
  setLanguage,
  steps,
  setSteps,
  showHearDemo,
  playDemoOutput,
  demoAudioPlaying,
  demoAudioRef,
  demoReleaseRef,
  setDemoAudioPlaying,
  outputPlaying,
  isGenerating,
  handleGenerate,
  generationTime,
  generationProgress,
  wasGeneratingRef,
}) {
  return (
    <div className="studio-action-bar overflow-visible relative z-[10]">
      {showOverrides && (
        <div className="override-content">
          <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,180px),1fr))] gap-3">
            {[
              {
                label: t('clone.steps'),
                Icon: SlidersHorizontal,
                value: steps,
                set: setSteps,
                min: 8,
                max: 64,
                step: 1,
              },
              {
                label: t('clone.cfg'),
                Icon: Focus,
                value: cfg,
                set: setCfg,
                min: 1,
                max: 4,
                step: 0.1,
              },
              {
                label: t('clone.speed'),
                Icon: Gauge,
                value: speed,
                set: setSpeed,
                min: 0.5,
                max: 2,
                step: 0.1,
                suffix: '×',
              },
              {
                label: t('clone.tshift'),
                Icon: Timer,
                value: tShift,
                set: setTShift,
                min: 0,
                max: 1,
                step: 0.05,
              },
              {
                label: t('clone.pos_temp'),
                Icon: Thermometer,
                value: posTemp,
                set: setPosTemp,
                min: 0,
                max: 10,
                step: 0.5,
              },
              {
                label: t('clone.class_temp'),
                Icon: Shuffle,
                value: classTemp,
                set: setClassTemp,
                min: 0,
                max: 2,
                step: 0.1,
              },
              {
                label: t('clone.layer_pen'),
                Icon: Layers,
                value: layerPenalty,
                set: setLayerPenalty,
                min: 0,
                max: 10,
                step: 0.5,
              },
            ].map(({ label, Icon, value, set, min, max, step, suffix = '' }) => (
              <label key={label} className="min-w-0 rounded-lg bg-[var(--chrome-hover-bg)] p-3">
                <span className="flex items-center justify-between gap-2 mb-3 text-sm text-[var(--chrome-fg)]">
                  <span className="inline-flex items-center gap-2">
                    <Icon size={15} aria-hidden="true" className="text-[var(--chrome-fg-muted)]" />
                    {label}
                  </span>
                  <output className="tabular-nums text-[var(--chrome-accent)] font-medium">
                    {value}
                    {suffix}
                  </output>
                </span>
                <input
                  className="w-full"
                  type="range"
                  aria-label={label}
                  min={min}
                  max={max}
                  step={step}
                  value={value}
                  onChange={(event) => set(Number(event.target.value))}
                />
              </label>
            ))}
            <label className="min-w-0 rounded-lg bg-[var(--chrome-hover-bg)] p-3">
              <span className="flex items-center gap-2 mb-2 text-sm text-[var(--chrome-fg)]">
                <Clock size={15} aria-hidden="true" />
                {t('clone.duration')}
              </span>
              <input
                type="text"
                aria-label={t('clone.duration')}
                className="input-base text-sm"
                value={duration}
                onChange={(event) => setDuration(event.target.value)}
                placeholder={t('clone.auto')}
              />
            </label>
          </div>
          <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,200px),1fr))] gap-3 mt-3">
            {[
              { label: t('clone.denoise'), Icon: AudioLines, checked: denoise, set: setDenoise },
              {
                label: t('clone.postprocess'),
                Icon: Sparkles,
                checked: postprocess,
                set: setPostprocess,
              },
            ].map(({ label, Icon, checked, set }) => (
              <button
                key={label}
                type="button"
                role="switch"
                aria-checked={checked}
                aria-label={label}
                onClick={() => set(!checked)}
                className="flex min-h-12 items-center justify-between gap-3 rounded-lg border-0 bg-[var(--chrome-hover-bg)] px-3 py-2 text-sm text-[var(--chrome-fg)] cursor-pointer hover:bg-[var(--chrome-accent-bg)] focus-visible:outline-2 focus-visible:outline-[var(--chrome-accent)]"
              >
                <span className="inline-flex items-center gap-2">
                  <Icon size={16} aria-hidden="true" />
                  {label}
                </span>
                <span
                  aria-hidden="true"
                  className={`relative w-9 h-5 rounded-full transition-colors ${checked ? 'bg-[var(--chrome-accent)]' : 'bg-[var(--chrome-fg-dim)]'}`}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 size-4 rounded-full bg-[var(--color-bg)] transition-transform motion-reduce:transition-none ${checked ? 'translate-x-4' : ''}`}
                  />
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Keep the everyday row focused; sampling controls live in overrides. */}
      <div className="flex items-center gap-3 min-w-0 max-[520px]:flex-wrap">
        <div className="flex items-center gap-[6px] flex-[1_1_220px] min-w-[140px] [&>:last-child]:flex-1 [&>:last-child]:min-w-0">
          <MultiLangPicker
            single
            ariaLabel={t('clone.language')}
            selected={[
              {
                lang: language,
                code: CLONE_LANGUAGES.find((item) => item.label === language)?.code || language,
              },
            ]}
            options={CLONE_LANGUAGES}
            onChange={([item]) => setLanguage(item.lang)}
          />
        </div>
        <button
          type="button"
          className="inline-flex min-h-9 items-center gap-[4px] px-[10px] py-[4px] text-[0.7rem] text-[var(--chrome-fg-muted)] bg-transparent border border-transparent rounded-md cursor-pointer whitespace-nowrap flex-none transition-[color,border-color] duration-[var(--dur-fast)] hover:text-[var(--chrome-fg)] hover:bg-[var(--chrome-hover-bg)] focus-visible:[outline:2px_solid_var(--chrome-accent)] focus-visible:[outline-offset:1px]"
          onClick={() => setShowOverrides(!showOverrides)}
          aria-expanded={showOverrides}
        >
          <Settings2 size={13} /> {t('clone.production_overrides')}
          {showOverrides ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
        </button>
      </div>

      {showHearDemo ? (
        <>
          <Button
            variant="primary"
            block
            onClick={playDemoOutput}
            leading={<Play size={14} />}
            className="mt-[6px]"
          >
            {demoAudioPlaying ? t('demo.stop_demo') : t('demo.hear_demo')}
          </Button>
          <div className="mt-[6px] px-[8px] py-[4px] text-[10px] text-center text-fg-muted bg-white/[0.03] rounded-md [border:1px_dashed_rgba(255,255,255,0.08)]">
            {t('demo.prerendered_chip')}
          </div>
          <audio
            ref={demoAudioRef}
            onEnded={() => {
              setDemoAudioPlaying(false);
              demoReleaseRef.current?.();
              demoReleaseRef.current = null;
            }}
            preload="none"
          />
        </>
      ) : outputPlaying && !isGenerating ? (
        /* Synthesized output is playing — the CTA becomes a Stop button
             (#316) so playback can be halted immediately. */
        <Button
          variant="primary"
          block
          onClick={stopActivePlayback}
          leading={<Square size={14} />}
          className="mt-[6px]"
        >
          {t('clone.stop_playback')}
        </Button>
      ) : (
        <Button
          variant="primary"
          block
          loading={isGenerating}
          onClick={handleGenerate}
          leading={!isGenerating && <Play size={14} />}
          className="mt-[6px]"
        >
          {isGenerating
            ? t('clone.synthesizing', { seconds: generationTime })
            : t('clone.synthesize')}
        </Button>
      )}
      {isGenerating && (
        <Progress value={generationProgress} tone="brand" size="sm" className="mt-[6px]" />
      )}
      {/* 10x P4 a11y (spec §3): persistent polite live region — screen
            readers hear generation start AND finish in-workspace, without
            relying on the FloatingPill. sr-only keeps it out of the
            action-bar flex flow; static text avoids per-second re-announces
            from the ticking "Synthesizing… (Ns)" button label. */}
      <div className="sr-only" role="status" aria-live="polite">
        {isGenerating
          ? t('clone.generating_status', { defaultValue: 'Generating audio…' })
          : wasGeneratingRef.current
            ? t('clone.generating_done_status', { defaultValue: 'Generation finished' })
            : null}
      </div>
    </div>
  );
}
