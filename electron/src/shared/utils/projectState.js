/**
 * Project payload extras (P1.4) — multi-language picks + export-track prefs
 * ride the saved project state so reopening a project restores the batch
 * setup the user configured.
 *
 * Legacy payloads (projects saved before these fields existed) simply lack
 * the keys — restoring them must default cleanly: multi-lang OFF/empty, and
 * exportTracks left untouched (`null` sentinel) so a legacy load never
 * clobbers the user's current in-session export choices.
 */

/** True for a plain `{ track: boolean }` map (rejects arrays/null). */
function isPlainObject(v) {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

/**
 * Normalize the multi-lang / export-track fields of a loaded project payload.
 *
 * @param {object} [state] - `data.state` from the projects API (may be legacy).
 * @returns {{ multiLangMode: boolean, multiLangs: {lang: string, code: string}[], exportTracks: Record<string, boolean> | null }}
 *   `exportTracks === null` means "absent in payload — keep the current value".
 */
export function restoreProjectExtras(state = {}) {
  const s = isPlainObject(state) ? state : {};
  return {
    multiLangMode: s.multiLangMode === true,
    multiLangs: Array.isArray(s.multiLangs)
      ? s.multiLangs.filter(
          (l) => isPlainObject(l) && typeof l.lang === 'string' && typeof l.code === 'string',
        )
      : [],
    exportTracks: isPlainObject(s.exportTracks) ? s.exportTracks : null,
  };
}
