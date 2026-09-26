export const DEFAULT_OVERRIDES = {
  numStep: null,
  guidanceScale: null,
  posTemp: null,
  classTemp: null,
  postprocess: null,
  seed: null,
  varyRepeats: false,
  emoText: '',
  emoAlpha: null,
  // Seamless joins: null = the server default (zero gaps / trim off, preserving legacy renders).
  lineGapMs: null,
  paragraphGapMs: null,
  trimEdges: null,
};

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
