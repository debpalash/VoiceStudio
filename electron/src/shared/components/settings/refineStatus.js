/**
 * Honesty layer for the dictation-refinement `llm_ready` flag.
 *
 * `llm_ready` only means "an endpoint is CONFIGURED" — a placeholder key or a
 * dead local endpoint still reads ready. The backend also reports
 * `last_refine_status` ({ok, reason, at}) from the most recent final, so the
 * panel can tell the user when a configured LLM is actually failing/timing out.
 * The dictation final is never blocked (the hard refine timeout inserts the raw
 * text regardless), so this message is purely informational.
 *
 * Returns the i18n key of the user-facing message (resolve with `t(key)`), or
 * null when the last refinement succeeded / hasn't run. Returning a key —
 * rather than a hardcoded English sentence — keeps the note translatable like
 * every other UI string.
 */
export function refineFailureNoteKey(status) {
  if (!status || status.ok !== false) return null;
  return status.reason === 'timeout'
    ? 'dictation.refine_timeout_note'
    : 'dictation.refine_failed_note';
}
