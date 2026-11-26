import { apiFetch, apiJson } from '@/lib/api/client';
import { saveLocalFile } from '@/lib/local-export';

export type PronunciationType = 'respelling' | 'ipa' | 'cmu';

export interface PronunciationEntry {
  id: string;
  term: string;
  replacement: string;
  type: PronunciationType;
  language: string;
  scope?: string;
  enabled: boolean;
}

export interface TestResult {
  substituted: string;
  changed: boolean;
}

export interface ImportPayload {
  entries: Omit<PronunciationEntry, 'id' | 'scope'>[];
  /** Lexicon rows left out (unsupported phoneme alphabet or invalid). */
  skipped?: number;
}

// An import belongs to the dictionary it was started for (#3873 review).
export interface PendingImport extends ImportPayload {
  profileId: string;
  count: number;
}

export const TYPES: PronunciationType[] = ['respelling', 'ipa', 'cmu'];

/** Read a dictionary export (JSON) or a W3C pronunciation lexicon (PLS) into entries. */
export async function readPronunciationImport(file: File): Promise<ImportPayload> {
  const text = await file.text();
  if (/\.(pls|xml)$/i.test(file.name) || text.trimStart().startsWith('<')) {
    // A W3C pronunciation lexicon (ElevenLabs, Azure, Google): the backend
    // reads it into entries; nothing is saved until the import.
    const lexicon = await apiJson<{ entries: unknown[]; skipped: number }>(
      '/pronunciation/parse-pls',
      { method: 'POST', body: JSON.stringify({ text }) },
    );
    return { entries: lexicon.entries, skipped: lexicon.skipped } as ImportPayload;
  }
  const parsed = JSON.parse(text) as unknown;
  const entries = Array.isArray(parsed)
    ? parsed
    : parsed && typeof parsed === 'object' && 'entries' in parsed
      ? (parsed as { entries?: unknown }).entries
      : null;
  if (!Array.isArray(entries)) throw new Error('Invalid pronunciation export');
  return { entries, skipped: 0 } as ImportPayload;
}

/** Save one dictionary (the language one, or a voice's) as JSON or as a PLS lexicon. */
export async function exportPronunciation(
  profileId: string,
  format: 'json' | 'pls',
): Promise<void> {
  const base = profileId ? 'pronunciation-voice-dictionary' : 'pronunciation-dictionary';
  const params = new URLSearchParams(profileId ? { profile_id: profileId } : {});
  if (format === 'pls') params.set('format', 'pls');
  const query = params.toString() ? '?' + params.toString() : '';
  const blob =
    format === 'pls'
      ? new Blob([await (await apiFetch('/pronunciation/export' + query)).text()], {
          type: 'application/pls+xml',
        })
      : new Blob([JSON.stringify(await apiJson('/pronunciation/export' + query), null, 2)], {
          type: 'application/json',
        });
  await saveLocalFile(blob, `${base}.${format}`);
}
