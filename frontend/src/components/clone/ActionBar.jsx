import {
  Globe,
  SlidersHorizontal,
  Settings2,
  ChevronUp,
  ChevronDown,
  Play,
  Square,
} from 'lucide-react';
import { Button, Progress } from '../../ui';
import SearchableSelect from '../SearchableSelect';
import ALL_LANGUAGES from '../../languages.json';
import { POPULAR_LANGS } from '../../utils/constants';
import { stopActivePlayback } from '../../utils/playback';

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
  wasGeneratingRef,
}) {
  return (
    <div className="studio-action-bar overflow-visible relative z-[10]">
      {showOverrides && (
        <div className="override-content">
          <div className="grid [grid-template-columns:repeat(auto-fit,minmax(120px,1fr))] gap-[6px] max-[500px]:grid-cols-2">
            <div>
              <div className="label-row justify-between">
                <span>CFG</span>
                <span className="text-[0.65rem] bg-black/35 px-[5px] py-px rounded-[3px] [border:1px_solid_rgba(255,255,255,0.04)] [font-variant-numeric:tabular-nums]">
                  {cfg}
                </span>
              </div>
              <input
                type="range"
                min="1.0"
                max="4.0"
                step="0.1"
                value={cfg}
                onChange={(e) => setCfg(Number(e.target.value))}
              />
            </div>
            <div>
              <div className="label-row justify-between">
                <span>{t('clone.speed')}</span>
                <span className="text-[0.65rem] bg-black/35 px-[5px] py-px rounded-[3px] [border:1px_solid_rgba(255,255,255,0.04)] [font-variant-numeric:tabular-nums]">
                  {speed}x
                </span>
              </div>
              <input
                type="range"
                min="0.5"
                max="2.0"
                step="0.1"
                value={speed}
                onChange={(e) => setSpeed(Number(e.target.value))}
              />
            </div>
            <div>
              <div className="label-row justify-between">
                <span>{t('clone.tshift')}</span>
                <span className="text-[0.65rem] bg-black/35 px-[5px] py-px rounded-[3px] [border:1px_solid_rgba(255,255,255,0.04)] [font-variant-numeric:tabular-nums]">
                  {tShift}
                </span>
              </div>
              <input
                type="range"
                min="0"
                max="1.0"
                step="0.05"
                value={tShift}
                onChange={(e) => setTShift(Number(e.target.value))}
              />
            </div>
            <div>
              <div className="label-row justify-between">
                <span>{t('clone.pos_temp')}</span>
                <span className="text-[0.65rem] bg-black/35 px-[5px] py-px rounded-[3px] [border:1px_solid_rgba(255,255,255,0.04)] [font-variant-numeric:tabular-nums]">
                  {posTemp}
                </span>
              </div>
              <input
                type="range"
                min="0"
                max="10"
                step="0.5"
                value={posTemp}
                onChange={(e) => setPosTemp(Number(e.target.value))}
              />
            </div>
            <div>
              <div className="label-row justify-between">
                <span>{t('clone.class_temp')}</span>
                <span className="text-[0.65rem] bg-black/35 px-[5px] py-px rounded-[3px] [border:1px_solid_rgba(255,255,255,0.04)] [font-variant-numeric:tabular-nums]">
                  {classTemp}
                </span>
              </div>
              <input
                type="range"
                min="0"
                max="2"
                step="0.1"
                value={classTemp}
                onChange={(e) => setClassTemp(Number(e.target.value))}
              />
            </div>
            <div>
              <div className="label-row justify-between">
                <span>{t('clone.layer_pen')}</span>
                <span className="text-[0.65rem] bg-black/35 px-[5px] py-px rounded-[3px] [border:1px_solid_rgba(255,255,255,0.04)] [font-variant-numeric:tabular-nums]">
                  {layerPenalty}
                </span>
              </div>
              <input
                type="range"
                min="0"
                max="10"
                step="0.5"
                value={layerPenalty}
                onChange={(e) => setLayerPenalty(Number(e.target.value))}
              />
            </div>
            <div>
              <div className="label-row">
                <span>{t('clone.duration')}</span>
              </div>
              <input
                type="text"
                className="input-base text-[0.8rem]"
                value={duration}
                onChange={(e) => setDuration(e.target.value)}
                placeholder={t('clone.auto')}
              />
            </div>
            <div className="flex flex-col gap-[6px]">
              <label className="text-[0.75rem] flex items-center gap-[6px] cursor-pointer">
                <input
                  type="checkbox"
                  checked={denoise}
                  onChange={(e) => setDenoise(e.target.checked)}
                />{' '}
                {t('clone.denoise')}
              </label>
              <label className="text-[0.75rem] flex items-center gap-[6px] cursor-pointer">
                <input
                  type="checkbox"
                  checked={postprocess}
                  onChange={(e) => setPostprocess(e.target.checked)}
                />{' '}
                {t('clone.postprocess')}
              </label>
            </div>
          </div>
        </div>
      )}

      {/* Controls row: language · steps · overrides disclosure */}
      <div className="flex items-center gap-[16px] min-w-0">
        <div className="flex items-center gap-[6px] flex-[1_1_220px] min-w-[140px] [&>:last-child]:flex-1 [&>:last-child]:min-w-0">
          <Globe size={12} className="label-icon" />
          <SearchableSelect
            value={language}
            options={ALL_LANGUAGES}
            popular={POPULAR_LANGS}
            recentsKey="omnivoice.recents.genLang"
            onChange={setLanguage}
          />
        </div>
        {/* Inference/sampling steps (#1771 follow-up): this slider used to
            carry only a hover title, so its value ("16") read as an
            unlabelled control sitting between the language picker and
            Production Overrides — give it the same visible label treatment
            as everything else in the bar. */}
        <label
          className="flex items-center gap-[6px] flex-[1_1_160px] min-w-[120px] [&_input]:flex-1 [&_input]:min-w-[60px]"
          title={t('clone.steps')}
        >
          <SlidersHorizontal size={12} className="label-icon" />
          <span className="text-[0.7rem] text-[var(--chrome-fg-muted)] whitespace-nowrap">
            {t('clone.steps')}
          </span>
          <input
            type="range"
            min="8"
            max="64"
            value={steps}
            onChange={(e) => setSteps(Number(e.target.value))}
          />
          <span className="text-[0.65rem] bg-black/35 px-[5px] py-px rounded-[3px] [border:1px_solid_rgba(255,255,255,0.04)] [font-variant-numeric:tabular-nums]">
            {steps}
          </span>
        </label>
        <button
          type="button"
          className="inline-flex items-center gap-[4px] px-[10px] py-[4px] text-[0.7rem] text-[var(--chrome-fg-muted)] bg-transparent border border-transparent rounded-[var(--chrome-radius-pill)] cursor-pointer whitespace-nowrap flex-none transition-[color,border-color] duration-[var(--dur-fast)] hover:text-[var(--chrome-fg)] hover:border-transparent focus-visible:[outline:2px_solid_var(--chrome-accent)] focus-visible:[outline-offset:1px]"
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
        <Progress
          value={Math.min((generationTime / 8) * 100, 95)}
          tone="brand"
          size="sm"
          className="mt-[6px]"
        />
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
