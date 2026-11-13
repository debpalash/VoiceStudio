/**
 * Transcripts the user saved for a voice in this session, so a queued take can
 * tell a user's edit from the backend's own transcript updates. The backend
 * fills an empty transcript on a voice's first render (#1032) and repairs a
 * legacy cut prefix (#3062); neither goes through here, so neither skips a
 * waiting take. Only the voice editor records a save.
 */
let sequence = 0;
const latest = new Map<string, { sequence: number; transcript: string }>();

/** Record that the user saved `transcript` for a voice. */
export function noteTranscriptSave(profileId: string, transcript: string | null | undefined): void {
  sequence += 1;
  latest.set(profileId, { sequence, transcript: transcript ?? '' });
}

/** A marker for "now": saves after it count as newer. */
export function transcriptSaveMarker(): number {
  return sequence;
}

/**
 * True when the user saved a different transcript for this voice after
 * `marker`, compared with the transcript the voice had then.
 */
export function transcriptSavedSince(
  profileId: string,
  marker: number,
  transcriptThen: string | null | undefined,
): boolean {
  const save = latest.get(profileId);
  return Boolean(save && save.sequence > marker && save.transcript !== (transcriptThen ?? ''));
}
