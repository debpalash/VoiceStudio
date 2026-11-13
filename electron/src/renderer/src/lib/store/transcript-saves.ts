/**
 * Transcripts the user saved for a voice in this session, so a queued take can
 * tell a user's edit from the backend's own transcript updates. The backend
 * fills an empty transcript on a voice's first render (#1032) and repairs a
 * legacy cut prefix (#3062); neither goes through here, so neither skips a
 * waiting take. Only the voice editor records a save.
 *
 * A save is pending while its request and the profile refresh are in flight:
 * a take that starts then already sees it, and one queued then (holding the
 * old cached transcript) is caught once the save commits, because the marker
 * only advances at commit. A failed save is dropped and skips nothing.
 */
let sequence = 0;
const latest = new Map<string, { sequence: number; transcript: string }>();
const pending = new Map<number, { profileId: string; transcript: string }>();
let nextToken = 0;

/** The user started saving `transcript` for a voice; settle with commit or fail. */
export function beginTranscriptSave(
  profileId: string,
  transcript: string | null | undefined,
): number {
  nextToken += 1;
  pending.set(nextToken, { profileId, transcript: transcript ?? '' });
  return nextToken;
}

/** The save went through: waiting takes that held another transcript are stale. */
export function commitTranscriptSave(token: number): void {
  const save = pending.get(token);
  if (!save) return;
  pending.delete(token);
  sequence += 1;
  latest.set(save.profileId, { sequence, transcript: save.transcript });
}

/** The save was rejected: the stored transcript is unchanged. */
export function failTranscriptSave(token: number): void {
  pending.delete(token);
}

/** A marker for "now": saves committed after it count as newer. */
export function transcriptSaveMarker(): number {
  return sequence;
}

/**
 * True when the user saved (or is saving) a different transcript for this
 * voice after `marker`, compared with the transcript the voice had then.
 */
export function transcriptSavedSince(
  profileId: string,
  marker: number,
  transcriptThen: string | null | undefined,
): boolean {
  const then = transcriptThen ?? '';
  const save = latest.get(profileId);
  if (save && save.sequence > marker && save.transcript !== then) return true;
  for (const inFlight of pending.values())
    if (inFlight.profileId === profileId && inFlight.transcript !== then) return true;
  return false;
}
