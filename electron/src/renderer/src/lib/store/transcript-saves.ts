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

/*
 * Requests that may change how a saved voice renders (edit, replace clip,
 * lock, unlock). The backend resolves the profile when a take renders, so a
 * waiting take must not start while one is in flight: it waits until the
 * request settles and the profile list is refreshed, then compares the voice.
 */
const voiceChanges = new Map<number, string>();
const settledListeners = new Set<() => void>();
let nextVoiceChange = 0;

/** Start a voice change; call the returned function once it has settled and the profiles are refreshed. */
export function beginVoiceChange(profileId: string): () => void {
  nextVoiceChange += 1;
  const token = nextVoiceChange;
  voiceChanges.set(token, profileId);
  return () => {
    if (!voiceChanges.delete(token)) return;
    for (const listener of [...settledListeners]) listener();
  };
}

/** True while a change to this voice is in flight. */
export function voiceChangePending(profileId: string): boolean {
  return [...voiceChanges.values()].includes(profileId);
}

/**
 * Resolves once no change to this voice is in flight. Rejects with an
 * AbortError when `signal` aborts first, so a cancelled take stops waiting.
 */
export function voiceChangesSettled(profileId: string, signal?: AbortSignal): Promise<void> {
  const busy = () => voiceChangePending(profileId);
  if (!busy()) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const done = () => {
      settledListeners.delete(check);
      signal?.removeEventListener('abort', onAbort);
    };
    const check = () => {
      if (busy()) return;
      done();
      resolve();
    };
    const onAbort = () => {
      done();
      reject(new DOMException('Stopped waiting for the voice change', 'AbortError'));
    };
    if (signal?.aborted) return onAbort();
    settledListeners.add(check);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}
