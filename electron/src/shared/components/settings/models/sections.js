/**
 * Pure helpers for the grouped Model Store catalog. No React — exported for
 * unit tests (modelStoreGrouping.test.jsx) and shared by ModelStoreTab.
 */

/** Section order the catalog renders in. */
export const MODEL_SECTION_ORDER = ['tts', 'asr', 'dictation', 'diarisation', 'other'];

/**
 * Classify a /models row into a catalog section.
 *
 * Dictation is the live-streaming subset of ASR (`engine: sherpa-onnx` or a
 * `tag` of offline/streaming in models.yaml) — the models the dictation UI
 * consumes — split out so "offline transcription" and "live dictation" read
 * as the two distinct capabilities they are.
 */
/** The documented dictation `tag` contract (models.yaml) — an allowlist, so
 *  unrelated future metadata tags (`multilingual`, `recommended`, …) can't
 *  silently reroute an offline-transcription model into Dictation. */
const DICTATION_TAGS = new Set(['offline', 'streaming']);

export function modelSectionKey(m) {
  const role = (m?.role || '').toLowerCase();
  if (role === 'tts') return 'tts';
  if (role === 'asr') {
    const tag = String(m?.tag || '').toLowerCase();
    return m?.engine === 'sherpa-onnx' || DICTATION_TAGS.has(tag) ? 'dictation' : 'asr';
  }
  if (role === 'diarisation' || role === 'diarization') return 'diarisation';
  return 'other';
}

/**
 * The Model Store search predicate — same fields the old TanStack global
 * filter matched (repo_id, label, note, role) so search behavior is unchanged
 * by the grouped layout.
 */
export function matchesModelQuery(m, query) {
  const q = String(query || '')
    .trim()
    .toLowerCase();
  if (!q) return true;
  return [m.repo_id, m.label, m.note, m.role]
    .filter(Boolean)
    .some((v) => String(v).toLowerCase().includes(q));
}

/**
 * Group models into ordered sections, applying the search query. Each entry:
 * { key, models (query-matched, catalog order), compatible, incompatible }.
 * Sections with no matching rows are omitted; `supported === false` rows land
 * in `incompatible` (rendered behind the per-section "Show incompatible"
 * toggle instead of inline greyed rows).
 */
export function groupModels(models, query) {
  const by = new Map(MODEL_SECTION_ORDER.map((k) => [k, []]));
  for (const m of models || []) {
    if (!matchesModelQuery(m, query)) continue;
    by.get(modelSectionKey(m)).push(m);
  }
  return MODEL_SECTION_ORDER.filter((k) => by.get(k).length > 0).map((key) => {
    const rows = by.get(key);
    return {
      key,
      models: rows,
      compatible: rows.filter((m) => m.supported !== false),
      incompatible: rows.filter((m) => m.supported === false),
    };
  });
}

/** Which catalog sections belong to each engine family (one family's slice of the catalog). */
export const FAMILY_SECTIONS = {
  tts: ['tts'],
  asr: ['asr', 'dictation', 'diarisation'],
  llm: [],
};
