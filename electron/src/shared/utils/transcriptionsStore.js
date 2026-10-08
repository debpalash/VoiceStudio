/**
 * Single source of truth for the dictation-history localStorage store (#23).
 * Relocates the key/event consts + the reader that were duplicated across
 * Transcriptions.jsx and Projects.jsx. No version/rename/rewrite — the storage
 * contract (key, entry shape, 200-cap) is unchanged; this only de-dups the read.
 */
export const TRANSCRIPTIONS_KEY = 'omni_transcriptions';
export const TRANSCRIPTION_EVENT = 'omni:transcription-added';

/**
 * Read the transcription history. Never throws; always returns an array
 * (newest-first, as written). [] on absent/empty/malformed/non-array/blocked.
 * @returns {Array<object>}
 */
export function loadTranscriptions() {
  try {
    const parsed = JSON.parse(localStorage.getItem(TRANSCRIPTIONS_KEY) || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/**
 * A numeric id unique within the stored history. `Date.now()` alone repeats
 * when several finals land in the same millisecond or the clock steps back,
 * and deletion is by id, so a repeat would delete unrelated rows.
 */
function nextTranscriptionId(entries) {
  let newest = 0;
  for (const entry of entries) {
    if (typeof entry?.id === 'number' && Number.isFinite(entry.id) && entry.id > newest) {
      newest = entry.id;
    }
  }
  return Math.max(Date.now(), Math.floor(newest) + 1);
}

/** Append a completed transcript using the existing 200-entry storage contract. */
export function addTranscription(entry) {
  const existing = loadTranscriptions();
  const newEntry = {
    id: nextTranscriptionId(existing),
    text: entry.text || '',
    language: entry.language || 'unknown',
    duration_s: entry.duration_s || 0,
    segments: entry.segments || [],
    timestamp: new Date().toISOString(),
    ...(typeof entry.refined_text === 'string' && entry.refined_text
      ? { refined_text: entry.refined_text }
      : {}),
  };
  const list = [newEntry, ...existing].slice(0, 200);
  localStorage.setItem(TRANSCRIPTIONS_KEY, JSON.stringify(list));
  window.dispatchEvent(new CustomEvent(TRANSCRIPTION_EVENT, { detail: newEntry }));
  return newEntry;
}

/** Observe writes from this document and sibling desktop windows. */
export function subscribeTranscriptions(listener) {
  const refresh = () => listener(loadTranscriptions());
  const storage = (event) => {
    if (event.key === TRANSCRIPTIONS_KEY) refresh();
  };
  window.addEventListener(TRANSCRIPTION_EVENT, refresh);
  window.addEventListener('storage', storage);
  return () => {
    window.removeEventListener(TRANSCRIPTION_EVENT, refresh);
    window.removeEventListener('storage', storage);
  };
}

/**
 * Delete one entry from the latest stored list; failed writes leave observers
 * unchanged. Only the first match (the row lists select) is removed, so older
 * histories that already hold repeated ids never lose unrelated transcripts.
 */
export function removeTranscription(id) {
  const entries = JSON.parse(localStorage.getItem(TRANSCRIPTIONS_KEY) || '[]');
  if (!Array.isArray(entries)) throw new Error('Invalid transcription history');
  const index = entries.findIndex((entry) => entry?.id === id);
  if (index >= 0) entries.splice(index, 1);
  localStorage.setItem(TRANSCRIPTIONS_KEY, JSON.stringify(entries));
  window.dispatchEvent(new CustomEvent(TRANSCRIPTION_EVENT));
}
