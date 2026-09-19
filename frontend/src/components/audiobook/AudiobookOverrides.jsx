import { useState } from 'react';
import { Settings2, ChevronDown, ChevronUp, RotateCcw } from 'lucide-react';
import { DEFAULT_OVERRIDES } from '../../store/longformSlice';

// Longform render defaults the sliders display when a knob is UNSET (null) —
// the documented audiobook/longform preset. A null value means "not overridden"
// (the backend keeps the model default and today's bytes); moving a slider sets
// an explicit override. Mirrors the Voice page's Production Overrides surface.
const DISPLAY = {
  numStep: 32,
  guidanceScale: 2.0,
  posTemp: 5.0,
  classTemp: 0.0,
  lineGapMs: 250,
  paragraphGapMs: 350,
};

const CHIP =
  'text-[0.65rem] bg-black/35 px-[5px] py-px rounded-[3px] [border:1px_solid_rgba(255,255,255,0.04)] [font-variant-numeric:tabular-nums]';

/**
 * Production Overrides for the Audiobook tab (#1208) — the same sampling surface
 * as the Voice page's ActionBar panel, plus a cache opt-out and (engine-gated)
 * IndexTTS2 emotion. Every control is optional: an untouched panel leaves the
 * store defaults (all null/false), which reproduce today's exact render.
 *
 * Controlled: `overrides` holds the values, `onChange(patch)` merges edits back
 * to the store. `emotionSupported` gates the emotion block so there are no dead
 * controls on engines that ignore emotion.
 */
export default function AudiobookOverrides({ t, overrides, onChange, emotionSupported = false }) {
  const [open, setOpen] = useState(false);
  const o = overrides;

  return (
    <div className="audiobook-tab__field flex flex-col gap-[6px]">
      <div className="flex items-center justify-between">
        <button
          type="button"
          className="inline-flex items-center gap-[4px] px-[4px] py-[4px] text-[0.72rem] text-[var(--chrome-fg-muted)] bg-transparent border-none cursor-pointer hover:text-[var(--chrome-fg)]"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          <Settings2 size={13} /> {t('audiobook.expressive')}
          {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        </button>
        {open && (
          <button
            type="button"
            className="inline-flex items-center gap-[4px] px-[6px] py-[2px] text-[0.65rem] text-[var(--chrome-fg-muted)] bg-transparent border-none cursor-pointer hover:text-[var(--chrome-fg)]"
            onClick={() => onChange({ ...DEFAULT_OVERRIDES })}
          >
            <RotateCcw size={11} /> {t('audiobook.reset')}
          </button>
        )}
      </div>

      {open && (
        <div className="flex flex-col gap-[10px] pl-[2px]">
          <div className="grid grid-cols-2 gap-[8px]">
            <div>
              <div className="label-row justify-between text-[0.7rem]">
                <span>{t('clone.steps')}</span>
                <span className={CHIP}>{o.numStep ?? DISPLAY.numStep}</span>
              </div>
              <input
                type="range"
                min="8"
                max="64"
                step="1"
                aria-label={t('clone.steps')}
                value={o.numStep ?? DISPLAY.numStep}
                onChange={(e) => onChange({ numStep: Number(e.target.value) })}
              />
            </div>
            <div>
              <div className="label-row justify-between text-[0.7rem]">
                <span>CFG</span>
                <span className={CHIP}>{o.guidanceScale ?? DISPLAY.guidanceScale}</span>
              </div>
              <input
                type="range"
                min="1.0"
                max="4.0"
                step="0.1"
                aria-label="CFG"
                value={o.guidanceScale ?? DISPLAY.guidanceScale}
                onChange={(e) => onChange({ guidanceScale: Number(e.target.value) })}
              />
            </div>
            <div>
              <div className="label-row justify-between text-[0.7rem]">
                <span>{t('clone.pos_temp')}</span>
                <span className={CHIP}>{o.posTemp ?? DISPLAY.posTemp}</span>
              </div>
              <input
                type="range"
                min="0"
                max="10"
                step="0.5"
                aria-label={t('clone.pos_temp')}
                value={o.posTemp ?? DISPLAY.posTemp}
                onChange={(e) => onChange({ posTemp: Number(e.target.value) })}
              />
            </div>
            <div>
              <div className="label-row justify-between text-[0.7rem]">
                <span>{t('clone.class_temp')}</span>
                <span className={CHIP}>{o.classTemp ?? DISPLAY.classTemp}</span>
              </div>
              <input
                type="range"
                min="0"
                max="2"
                step="0.1"
                aria-label={t('clone.class_temp')}
                value={o.classTemp ?? DISPLAY.classTemp}
                onChange={(e) => onChange({ classTemp: Number(e.target.value) })}
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-x-[16px] gap-y-[6px]">
            <label className="text-[0.72rem] flex items-center gap-[6px] cursor-pointer">
              <input
                type="checkbox"
                checked={o.postprocess ?? true}
                onChange={(e) => onChange({ postprocess: e.target.checked })}
              />{' '}
              {t('clone.postprocess')}
            </label>
            <label
              className="text-[0.72rem] flex items-center gap-[6px] cursor-pointer"
              title={t('audiobook.vary_repeats_help')}
            >
              <input
                type="checkbox"
                checked={!!o.varyRepeats}
                onChange={(e) => onChange({ varyRepeats: e.target.checked })}
              />{' '}
              {t('audiobook.vary_repeats')}
            </label>
            <label className="text-[0.72rem] flex items-center gap-[6px]">
              {t('audiobook.seed')}
              <input
                type="text"
                inputMode="numeric"
                className="input-base text-[0.75rem] w-[86px]"
                placeholder={t('audiobook.seed_ph')}
                aria-label={t('audiobook.seed')}
                value={o.seed ?? ''}
                onChange={(e) => {
                  const v = e.target.value.trim();
                  const n = Number.parseInt(v, 10);
                  // Keep 0 as a valid seed — only empty / non-numeric clears it.
                  onChange({ seed: v === '' || Number.isNaN(n) ? null : n });
                }}
              />
            </label>
          </div>

          <div className="flex flex-col gap-[6px] pt-[6px] [border-top:1px_solid_rgba(255,255,255,0.06)]">
            <div className="text-[0.7rem] text-fg-muted">{t('audiobook.joins_help')}</div>
            <div>
              <div className="label-row justify-between text-[0.7rem]">
                <span>{t('audiobook.line_gap')}</span>
                <span className={CHIP}>{o.lineGapMs ?? DISPLAY.lineGapMs} ms</span>
              </div>
              <input
                type="range"
                min="0"
                max="2000"
                step="50"
                aria-label={t('audiobook.line_gap')}
                value={o.lineGapMs ?? DISPLAY.lineGapMs}
                onChange={(e) => onChange({ lineGapMs: Number(e.target.value) })}
              />
            </div>
            <div>
              <div className="label-row justify-between text-[0.7rem]">
                <span>{t('audiobook.paragraph_gap')}</span>
                <span className={CHIP}>{o.paragraphGapMs ?? DISPLAY.paragraphGapMs} ms</span>
              </div>
              <input
                type="range"
                min="0"
                max="3000"
                step="50"
                aria-label={t('audiobook.paragraph_gap')}
                value={o.paragraphGapMs ?? DISPLAY.paragraphGapMs}
                onChange={(e) => onChange({ paragraphGapMs: Number(e.target.value) })}
              />
            </div>
            <label
              className="text-[0.72rem] flex items-center gap-[6px] cursor-pointer"
              title={t('audiobook.trim_edges_help')}
            >
              <input
                type="checkbox"
                checked={o.trimEdges ?? true}
                onChange={(e) => onChange({ trimEdges: e.target.checked })}
              />{' '}
              {t('audiobook.trim_edges')}
            </label>
          </div>

          {emotionSupported && (
            <div className="flex flex-col gap-[6px] pt-[6px] [border-top:1px_solid_rgba(255,255,255,0.06)]">
              <div className="text-[0.7rem] text-fg-muted">{t('audiobook.emotion_help')}</div>
              <label className="text-[0.72rem] flex flex-col gap-[3px]">
                {t('audiobook.emotion_text')}
                <input
                  type="text"
                  className="input-base text-[0.8rem]"
                  placeholder={t('audiobook.emotion_text_ph')}
                  aria-label={t('audiobook.emotion_text')}
                  value={o.emoText ?? ''}
                  onChange={(e) => onChange({ emoText: e.target.value })}
                />
              </label>
              <div>
                <div className="label-row justify-between text-[0.7rem]">
                  <span>{t('audiobook.emotion_alpha')}</span>
                  <span className={CHIP}>{o.emoAlpha ?? 1.0}</span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  aria-label={t('audiobook.emotion_alpha')}
                  value={o.emoAlpha ?? 1.0}
                  onChange={(e) => onChange({ emoAlpha: Number(e.target.value) })}
                />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Lower the persisted overrides (+ language) into the snake_case request fields
 * the backend expects. Only NON-default values are emitted, so an untouched
 * panel adds nothing to the body → today's exact request. Shared by the full
 * render and the per-chapter preview so both hit the same cache slot.
 */
export function overridesToRequest(overrides, language) {
  const o = overrides || DEFAULT_OVERRIDES;
  const body = {};
  if (language && language !== 'Auto') body.language = language;
  if (o.numStep != null) body.num_step = o.numStep;
  if (o.guidanceScale != null) body.guidance_scale = o.guidanceScale;
  if (o.posTemp != null) body.position_temperature = o.posTemp;
  if (o.classTemp != null) body.class_temperature = o.classTemp;
  if (o.postprocess != null) body.postprocess_output = o.postprocess;
  if (o.seed != null) body.seed = o.seed;
  if (o.varyRepeats) body.vary_repeats = true;
  const emo = (o.emoText || '').trim();
  if (emo) {
    body.emo_text = emo;
    if (o.emoAlpha != null) body.emo_alpha = o.emoAlpha;
  }
  if (o.lineGapMs != null) body.line_gap_ms = o.lineGapMs;
  if (o.paragraphGapMs != null) body.paragraph_gap_ms = o.paragraphGapMs;
  if (o.trimEdges != null) body.trim_edges = o.trimEdges;
  return body;
}
