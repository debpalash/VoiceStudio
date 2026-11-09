import type { DesignRecipe, HistoryItem, InstructVocabulary, Profile } from '@/lib/api/types';
import { pickDesignSeed } from '@shared/utils/seed';
import {
  applyVdState,
  buildDesignInstruct,
  instructToVdStates,
  mergeDescribedAttrs,
} from '@shared/utils/voiceInstruct';
export const STORAGE = 'voicestudio.design.v1';
export const DESIGN_DRAFT_EVENT = 'voicestudio:design-draft';

/** A detail the user chose explicitly, and the description that was in effect then. */
export interface DesignPick {
  value: string;
  description: string;
}

export interface DesignDraft {
  text: string;
  /** Effective details: what the description maps to, with the picks on top. */
  attrs: Record<string, string>;
  seed: number;
  profileId: string | null;
  /** What the user wrote. */
  description: string;
  /** Explicit choices; each holds until the description says something else about it. */
  picks: Record<string, DesignPick>;
  /** Details inferred from the current description. */
  mapped: Record<string, string>;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function stringEntries(value: unknown): Record<string, string> {
  if (!isPlainObject(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter((entry) => typeof entry[1] === 'string'),
  ) as Record<string, string>;
}

/** Details that are set (not Auto), recorded as picks made under no description. */
function explicitPicks(attrs: Record<string, string>): Record<string, DesignPick> {
  const result: Record<string, DesignPick> = {};
  for (const category in attrs) {
    const value = attrs[category];
    if (value !== 'Auto') result[category] = { value, description: '' };
  }
  return result;
}

/** Strip a picks map down to category -> chosen value. */
function pickValues(picks: Record<string, DesignPick>): Record<string, string> {
  const values: Record<string, string> = {};
  for (const category in picks) values[category] = picks[category].value;
  return values;
}

function blankDraft(): DesignDraft {
  return {
    text: '',
    attrs: mergeDescribedAttrs(),
    seed: pickDesignSeed(false, null),
    profileId: null,
    description: '',
    picks: {},
    mapped: {},
  };
}

export function readDraft(): DesignDraft {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(STORAGE) ?? '{}');
    const stored = isPlainObject(parsed) ? parsed : {};
    const attrs = mergeDescribedAttrs(stringEntries(stored.attrs));
    let picks: Record<string, DesignPick> = {};
    if (!Object.prototype.hasOwnProperty.call(stored, 'picks')) {
      picks = explicitPicks(attrs);
    } else if (isPlainObject(stored.picks)) {
      const validEntries = Object.entries(stored.picks).flatMap(([category, candidate]) => {
        if (!isPlainObject(candidate)) return [];
        const { value, description } = candidate;
        return typeof value === 'string' && typeof description === 'string'
          ? [[category, { value, description }] as const]
          : [];
      });
      picks = Object.fromEntries(validEntries);
    }
    return {
      text: typeof stored.text === 'string' ? stored.text : '',
      attrs,
      seed: Number.isInteger(stored.seed) ? (stored.seed as number) : pickDesignSeed(false, null),
      profileId: typeof stored.profileId === 'string' ? stored.profileId : null,
      description: typeof stored.description === 'string' ? stored.description : '',
      picks,
      mapped: stringEntries(stored.mapped),
    };
  } catch {
    return blankDraft();
  }
}

export function writeDraft(draft: DesignDraft) {
  try {
    localStorage.setItem(STORAGE, JSON.stringify(draft));
  } catch {
    /* The mounted workspace can still receive the in-memory draft. */
  }
  window.dispatchEvent(new CustomEvent<DesignDraft>(DESIGN_DRAFT_EVENT, { detail: draft }));
}

/**
 * Replace the draft from a profile, preset, or new recipe. Source prose and
 * picks are retained when supplied; otherwise both reset so another voice's
 * free-form directions cannot carry across.
 */
export function replaceRecipe(current: DesignDraft, recipe: Partial<DesignDraft>): DesignDraft {
  const overlaid = { ...current, ...recipe };
  return {
    ...overlaid,
    profileId: recipe.profileId ?? null,
    description: recipe.description ?? '',
    mapped: recipe.mapped ?? {},
    picks: recipe.picks ?? explicitPicks(overlaid.attrs),
  };
}

/**
 * Change part of the voice (description, seed, details). The draft no longer
 * is the saved voice it came from, so it stops sending that voice: the
 * backend would otherwise keep cloning the saved sample and the edit would
 * barely be heard. Setting a value it already has changes nothing.
 */
export function editVoice(
  current: DesignDraft,
  patch: Partial<Pick<DesignDraft, 'attrs' | 'seed' | 'description' | 'picks'>>,
): DesignDraft {
  const changed = (Object.keys(patch) as (keyof typeof patch)[]).some(
    (key) => JSON.stringify(patch[key]) !== JSON.stringify(current[key]),
  );
  return changed ? { ...current, ...patch, profileId: null } : current;
}

function sameAttrs(left: Record<string, string>, right: Record<string, string>): boolean {
  return [...new Set([...Object.keys(left), ...Object.keys(right)])].every(
    (category) => (left[category] ?? 'Auto') === (right[category] ?? 'Auto'),
  );
}

/**
 * The saved design voice a take should re-render, or null. A draft links to
 * a design profile only while its details and seed are still the profile's,
 * so an edit made before linking was enforced (or restored from an older
 * draft) designs a new voice instead of cloning the saved sample.
 */
export function linkedDesignProfile(
  draft: Pick<DesignDraft, 'attrs' | 'seed' | 'profileId'>,
  profiles: readonly Profile[] | undefined,
): Profile | null {
  const profile = profiles?.find((item) => item.id === draft.profileId && item.kind === 'design');
  if (!profile) return null;
  const saved = restoreDesignProfile(profile, draft.seed);
  return saved.seed === draft.seed && sameAttrs(saved.attrs, draft.attrs) ? profile : null;
}

/**
 * The seed a take sends. A linked voice saved without a seed keeps its own
 * (none): the draft's placeholder would read as an edit and design a new
 * voice instead of re-rendering the saved one.
 */
export function designRequestSeed(
  draft: Pick<DesignDraft, 'seed'>,
  linked: Profile | null,
): number | undefined {
  return linked && linked.seed == null ? undefined : draft.seed;
}

/**
 * Fold the details inferred from a description into the draft. Explicit picks
 * are replayed over the inferred values; a pick gives way only when it was
 * made under other wording and the new text now says something different
 * about its category (or about the exclusive category the pick would reset).
 */
export function applyDescription(
  current: DesignDraft,
  mapping: Record<string, string>,
  described: string,
): DesignDraft {
  const inferred = mergeDescribedAttrs(mapping);
  const textMoved = (category: string) =>
    (inferred[category] ?? 'Auto') !== (current.mapped[category] ?? 'Auto');

  let attrs: Record<string, string> = { ...inferred };
  const picks: Record<string, DesignPick> = {};
  for (const [category, pick] of Object.entries(current.picks)) {
    const trial = applyVdState(attrs, category, pick.value);
    const overridden =
      textMoved(category) || (trial.clearedCategory !== null && textMoved(trial.clearedCategory));
    if (overridden && pick.description !== described) continue;
    attrs = trial.vdStates;
    picks[category] = pick;
  }

  return { ...current, attrs, picks, mapped: inferred };
}

/**
 * "Reset to description" drops the picks (and a saved-voice link) so the
 * description alone decides again. With no description there is nothing to
 * reset to, and without picks or a link the details already follow it.
 */
export function canResetToDescription(
  draft: Pick<DesignDraft, 'description' | 'picks' | 'profileId'>,
): boolean {
  return (
    draft.description.trim() !== '' &&
    (Object.keys(draft.picks).length > 0 || draft.profileId !== null)
  );
}

/** Record a deliberate detail choice and report any exclusive detail reset by it. */
export function pickDetail(
  current: DesignDraft,
  category: string,
  value: string,
): { draft: DesignDraft; clearedCategory: string | null } {
  const applied = applyVdState(current.attrs, category, value);
  const nextPicks = Object.assign({}, current.picks, {
    [category]: { value, description: current.description.trim() },
  });
  if (applied.clearedCategory) delete nextPicks[applied.clearedCategory];
  const nextProfile = sameAttrs(applied.vdStates, current.attrs) ? current.profileId : null;
  const draft = {
    ...current,
    attrs: applied.vdStates,
    profileId: nextProfile,
    picks: nextPicks,
  };
  return {
    draft,
    clearedCategory: applied.clearedCategory,
  };
}

export function designInstruct(
  draft: Pick<DesignDraft, 'attrs' | 'description' | 'picks'>,
  vocabulary: InstructVocabulary,
): string {
  const freeform = vocabulary === 'freeform';
  const tagSource = freeform ? pickValues(draft.picks) : draft.attrs;
  const tags = buildDesignInstruct(tagSource, '').instruct;
  if (!freeform) return tags;

  const prose = draft.description.trim();
  if (!prose) return tags;
  return tags ? `${prose}, ${tags}` : prose;
}

/** The part of the draft that is stored with the take. */
export function designRecipe(draft: Pick<DesignDraft, 'description' | 'picks'>): DesignRecipe {
  return { description: draft.description.trim(), picks: pickValues(draft.picks) };
}

interface StoredRecipe {
  description: string;
  picks: Record<string, string>;
  mapped: Record<string, string>;
}

function parseStoredRecipe(raw: string | null | undefined): StoredRecipe | null {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isPlainObject(parsed) || typeof parsed.description !== 'string') return null;
    return {
      description: parsed.description,
      picks: stringEntries(parsed.picks),
      mapped: stringEntries(parsed.mapped),
    };
  } catch {
    return null;
  }
}

/**
 * Rebuild Design from a take. Recipes restore prose, picks, and mapped details;
 * legacy takes use their full instruction as prose and recognise its tags.
 */
export function designDraftFromTake(item: HistoryItem): DesignDraft {
  const stored = parseStoredRecipe(item.design_recipe);
  const legacyDescription = item.instruct ?? '';
  const description = stored ? stored.description : legacyDescription;
  const describedAttrs = stored ? stored.mapped : instructToVdStates(description);
  const mapped = mergeDescribedAttrs(describedAttrs);
  const described = description.trim();
  const restoredPicks: Record<string, DesignPick> = {};
  if (stored) {
    Object.entries(stored.picks).forEach(([category, value]) => {
      restoredPicks[category] = { value, description: described };
    });
  }
  const draft: DesignDraft = {
    text: item.text,
    attrs: mapped,
    seed: item.seed ?? pickDesignSeed(false, null),
    profileId: item.profile_id,
    description,
    picks: restoredPicks,
    mapped,
  };
  return applyDescription(draft, mapped, described);
}

/** Restore a saved design recipe without leaking values from the previous voice. */
export function restoreDesignProfile(
  profile: Profile,
  fallbackSeed: number,
): Pick<DesignDraft, 'attrs' | 'seed' | 'profileId' | 'description' | 'picks' | 'mapped'> & {
  language: string;
} {
  const recipe = parseStoredRecipe(profile.design_recipe);
  const editedAttrs = instructToVdStates(profile.instruct ?? '');
  let attrs = editedAttrs;
  if (profile.vd_states) {
    try {
      const parsed = JSON.parse(profile.vd_states);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        // vd_states remains the source of truth for the effective category
        // controls, even if the free-form description covers richer traits.
        attrs = { ...editedAttrs, ...(parsed as Record<string, string>) };
      }
    } catch {
      /* The complete instruct-derived fallback remains usable. */
    }
  }
  const description = recipe?.description ?? '';
  const merged = mergeDescribedAttrs(attrs);
  return {
    attrs: merged,
    seed: profile.seed ?? fallbackSeed,
    profileId: profile.id,
    language: profile.language || 'Auto',
    description,
    mapped: recipe?.mapped ?? {},
    picks: recipe
      ? Object.fromEntries(
          Object.entries(recipe.picks).map(([category, value]) => [
            category,
            { value, description },
          ]),
        )
      : explicitPicks(merged),
  };
}
