import type { CatalogueModel } from '../model-catalogue-query';
import type { ModelFamily } from '../model-family';

/**
 * Engines whose weights the catalogue does not list under `engines:`. The
 * translation and diarisation option ids live in their own registries, so the
 * link is declared here rather than changing what the backend reports.
 */
const ENGINE_MODEL_ALIASES: Record<string, readonly string[]> = {
  nllb: ['facebook/nllb-200-distilled-600M'],
  pyannote: ['pyannote/speaker-diarization-3.1'],
  'audiocpp-sortformer': ['audio-cpp/audio.cpp-gguf'],
};

/** Catalogue models shown on one family page (same rule the model store used). */
export function familyModels(models: CatalogueModel[] | undefined, family: ModelFamily) {
  return (models ?? []).filter((model) => {
    if (family === 'dictation') return Boolean(model.dictation_id);
    if (family === 'diarisation')
      return (
        ['diarisation', 'diarization'].includes(model.role.toLowerCase()) ||
        Boolean(model.families?.includes('diarisation'))
      );
    return model.role.toLowerCase() === family && !model.dictation_id;
  });
}

/** The catalogue models an engine runs, its curated picks first. */
export function modelsForEngine(
  engineId: string,
  models: CatalogueModel[],
  curated?: readonly { repo_id: string }[],
) {
  const aliases = ENGINE_MODEL_ALIASES[engineId] ?? [];
  const linked = models.filter(
    (model) =>
      model.engines?.includes(engineId) ||
      aliases.includes(model.repo_id) ||
      curated?.some((choice) => choice.repo_id === model.repo_id),
  );
  const order = (model: CatalogueModel) => {
    const index = curated?.findIndex((choice) => choice.repo_id === model.repo_id) ?? -1;
    return index < 0 ? Number.MAX_SAFE_INTEGER : index;
  };
  return linked
    .map((model, index) => ({ model, index }))
    .sort((a, b) => order(a.model) - order(b.model) || a.index - b.index)
    .map(({ model }) => model);
}

/** Catalogue models no listed engine claims: they become rows of their own. */
export function unclaimedModels(models: CatalogueModel[], claimed: Iterable<CatalogueModel>) {
  const taken = new Set([...claimed].map((model) => model.repo_id));
  return models.filter((model) => !taken.has(model.repo_id));
}

export interface ListRank {
  active: boolean;
  ready: boolean;
  recommended: boolean;
}

/** Active first, then installed/ready, then recommended; ties keep backend order. */
export function sortByRank<T extends { rank: ListRank }>(items: readonly T[]) {
  const score = ({ rank }: T) =>
    (rank.active ? 4 : 0) + (rank.ready ? 2 : 0) + (rank.recommended ? 1 : 0);
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => score(b.item) - score(a.item) || a.index - b.index)
    .map(({ item }) => item);
}

export function normalizeQuery(query: string) {
  return query.trim().toLocaleLowerCase();
}

export function matchesQuery(values: readonly (string | null | undefined)[], query: string) {
  return !query || values.some((value) => value?.toLocaleLowerCase().includes(query));
}

/** Text a model contributes to search: label, repo id, note and role. */
export function modelSearchText(model: CatalogueModel) {
  return [model.label, model.repo_id, model.note, model.role];
}

/** Faster-Whisper accepts these CTranslate2 checkpoints as a model pick. */
export function selectableAsrModel(repoId: string) {
  return (
    repoId.startsWith('Systran/faster-') || repoId === 'deepdml/faster-whisper-large-v3-turbo-ct2'
  );
}

export const FASTER_WHISPER_ENGINES = new Set(['faster-whisper', 'faster-whisper-isolated']);
