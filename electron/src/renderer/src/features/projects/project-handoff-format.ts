import { tr } from '@/lib/i18n-text';
import { blankLongformDraft, type Draft } from '../longform/longform-session';
import { restoreBookOptions } from '../longform/book-options';
export type ProjectBundleResult =
  | { kind: 'dub'; title: string; projectId: string }
  | { kind: 'stories' | 'audiobook'; title: string; project: { draft: Partial<Draft> } };
const object = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);
const nullableString = (value: unknown) => value == null || typeof value === 'string';
/** Import into a new library identity; never adopt the sender's local project ID. */
export function importedLongformDraft(result: ProjectBundleResult): Draft {
  const invalid = () => new Error(tr('mobile.handoff.invalid'));
  if (result.kind !== 'stories' && result.kind !== 'audiobook') throw invalid();
  const draft = result.project?.draft;
  if (
    !object(draft) ||
    typeof draft.script !== 'string' ||
    !Array.isArray(draft.lines) ||
    !Array.isArray(draft.cast) ||
    typeof result.title !== 'string' ||
    !result.title.trim()
  )
    throw invalid();
  if (
    !draft.lines.every(
      (line) =>
        object(line) &&
        typeof line.id === 'string' &&
        typeof line.text === 'string' &&
        nullableString(line.profileId) &&
        nullableString(line.character) &&
        (line.speed == null ||
          (typeof line.speed === 'number' && line.speed >= 0.5 && line.speed <= 2)),
    )
  )
    throw invalid();
  if (
    !draft.cast.every(
      (character) =>
        object(character) &&
        typeof character.id === 'string' &&
        typeof character.name === 'string' &&
        nullableString(character.profileId),
    )
  )
    throw invalid();
  if (
    new Set(draft.lines.map((line) => line.id)).size !== draft.lines.length ||
    new Set(draft.cast.map((character) => character.id)).size !== draft.cast.length
  )
    throw invalid();
  for (const key of ['importText', 'title', 'language', 'output', 'outputScript'] as const)
    if (draft[key] !== undefined && typeof draft[key] !== 'string') throw invalid();
  if (
    !nullableString(draft.voice) ||
    (draft.globalSpeed !== undefined &&
      (typeof draft.globalSpeed !== 'number' || draft.globalSpeed < 0.5 || draft.globalSpeed > 2))
  )
    throw invalid();
  if (
    draft.voiceCast !== undefined &&
    (!object(draft.voiceCast) ||
      !Object.values(draft.voiceCast).every((value) => typeof value === 'string'))
  )
    throw invalid();
  if (
    draft.outputChapters !== undefined &&
    (!Array.isArray(draft.outputChapters) ||
      !draft.outputChapters.every(
        (chapter) =>
          object(chapter) &&
          typeof chapter.title === 'string' &&
          typeof chapter.status === 'string',
      ))
  )
    throw invalid();
  if (draft.overrides !== undefined) {
    if (!object(draft.overrides)) throw invalid();
    for (const [key, value] of Object.entries(draft.overrides)) {
      if (value == null) continue;
      const type =
        key === 'emoText'
          ? 'string'
          : ['postprocess', 'varyRepeats', 'trimEdges'].includes(key)
            ? 'boolean'
            : 'number';
      if (typeof value !== type || (typeof value === 'number' && !Number.isFinite(value)))
        throw invalid();
    }
  }
  if (draft.format !== undefined && draft.format !== 'mp3' && draft.format !== 'm4b')
    throw invalid();
  for (const key of ['outputCachedChapters', 'outputFailedChapters'] as const)
    if (draft[key] !== undefined && (!Number.isSafeInteger(draft[key]) || draft[key]! < 0))
      throw invalid();
  const defaults = blankLongformDraft();
  return {
    ...defaults,
    ...draft,
    ...restoreBookOptions(draft),
    overrides: { ...defaults.overrides, ...draft.overrides },
    projectId: null,
  };
}
