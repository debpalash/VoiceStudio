import type { DesignRecipe, HistoryItem, InstructVocabulary, Profile } from '@/lib/api/types';
import {
  applyVdState,
  buildDesignInstruct,
  instructToVdStates,
  mergeDescribedAttrs,
} from '@shared/utils/voiceInstruct';
import { pickDesignSeed } from '@shared/utils/seed';
export const STORAGE = 'voicestudio.design.v1';
export const DESIGN_DRAFT_EVENT = 'voicestudio:design-draft';

export interface DesignDraft {
  text: string;
  /** Effective details: the description mapping with the user's picks on top. */
  attrs: Record<string, string>;
  seed: number;
  profileId: string | null;
  /** What the user wrote; free-form engines receive it as written (#2389). */
  description: string;
  /** Details chosen explicitly; each holds until the description says otherwise. */
  picks: Record<string, DesignPick>;
  /** The details the current description maps to. */
  mapped: Record<string, string>;
}

export interface DesignPick {
  value: string;
  /** The description in effect when the detail was picked. */
  description: string;
}

function stringRecord(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter(
      (entry): entry is [string, string] => typeof entry[1] === 'string',
    ),
  );
}

function pickRecord(value: unknown): Record<string, DesignPick> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter(
      (entry): entry is [string, DesignPick] =>
        typeof entry[1]?.value === 'string' && typeof entry[1]?.description === 'string',
    ),
  );
}

/** With no description behind them, every set detail was chosen explicitly. */
function explicitDetails(attrs: Record<string, string>): Record<string, DesignPick> {
  return Object.fromEntries(
    Object.entries(attrs)
      .filter(([, value]) => value !== 'Auto')
      .map(([category, value]) => [category, { value, description: '' }]),
  );
}

export function readDraft(): DesignDraft {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE) || '{}');
    const attrs = mergeDescribedAttrs(value.attrs);
    return {
      text: typeof value.text === 'string' ? value.text : '',
      attrs,
      seed: Number.isInteger(value.seed) ? value.seed : pickDesignSeed(false, null),
      profileId: typeof value.profileId === 'string' ? value.profileId : null,
      description: typeof value.description === 'string' ? value.description : '',
      // Drafts saved before descriptions were persisted only held picks.
      picks: value.picks === undefined ? explicitDetails(attrs) : pickRecord(value.picks),
      mapped: stringRecord(value.mapped),
    };
  } catch {
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
 * Replace the design recipe (saved profile, preset, personality or demo). The
 * description belonged to the previous voice, so it is dropped: a free-form
 * engine would otherwise receive both as contradictory directions. The new
 * recipe's details are explicit choices.
 */
export function replaceRecipe(current: DesignDraft, recipe: Partial<DesignDraft>): DesignDraft {
  // Only a saved voice links the draft; a preset, personality or demo starts
  // a new, unsaved design.
  const next = { ...current, ...recipe, profileId: recipe.profileId ?? null, description: '', mapped: {} };
  return { ...next, picks: explicitDetails(next.attrs) };
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
 * Apply the mapping of `described`, which the caller has checked is still the
 * current description. The most recent intent wins per detail: a pick holds
 * until the description changes what it says about that detail (or about its
 * exclusive counterpart). A pick made while `described` was already in effect
 * is newer than it, so a mapping that lands late never undoes it.
 */
export function applyDescription(
  current: DesignDraft,
  mapping: Record<string, string>,
  described: string,
): DesignDraft {
  const mapped = mergeDescribedAttrs(mapping);
  const restated = (category: string) => mapped[category] !== (current.mapped[category] ?? 'Auto');
  let attrs = mapped;
  const picks: Record<string, DesignPick> = {};
  for (const [category, pick] of Object.entries(current.picks)) {
    const older = pick.description !== described;
    if (older && restated(category)) continue;
    const { vdStates, clearedCategory } = applyVdState(attrs, category, pick.value);
    if (older && clearedCategory && restated(clearedCategory)) continue;
    attrs = vdStates;
    picks[category] = pick;
  }
  return { ...current, attrs, picks, mapped };
}

/** Record an explicit pick; one that clears an exclusive category drops that pick. */
export function pickDetail(
  current: DesignDraft,
  category: string,
  value: string,
): { draft: DesignDraft; clearedCategory: string | null } {
  const { vdStates, clearedCategory } = applyVdState(current.attrs, category, value);
  const picks = {
    ...current.picks,
    [category]: { value, description: current.description.trim() },
  };
  if (clearedCategory) delete picks[clearedCategory];
  const profileId = sameAttrs(vdStates, current.attrs) ? current.profileId : null;
  return { draft: { ...current, attrs: vdStates, picks, profileId }, clearedCategory };
}

/**
 * The instruct a design take sends. OmniVoice only accepts its tag set, so the
 * effective details are all it gets. Free-form engines read the description as
 * written, with only the user's picks appended as extra cues: details mapped
 * from the description would repeat or contradict it (#2389).
 */
export function designInstruct(
  draft: Pick<DesignDraft, 'attrs' | 'description' | 'picks'>,
  vocabulary: InstructVocabulary,
): string {
  if (vocabulary !== 'freeform') return buildDesignInstruct(draft.attrs, '').instruct;
  const picked = Object.fromEntries(
    Object.entries(draft.picks).map(([category, pick]) => [category, pick.value]),
  );
  const tags = buildDesignInstruct(picked, '').instruct;
  return [draft.description.trim(), tags].filter(Boolean).join(', ');
}

/** The part of the draft sent with a take so reopening it rebuilds the draft (#2389). */
export function designRecipe(draft: DesignDraft): DesignRecipe {
  return {
    description: draft.description.trim(),
    picks: Object.fromEntries(
      Object.entries(draft.picks).map(([category, pick]) => [category, pick.value]),
    ),
  };
}

function parseRecipe(
  raw: string | null | undefined,
): (DesignRecipe & { mapped: Record<string, string> }) | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value !== 'object' || typeof value.description !== 'string') return null;
    return {
      description: value.description,
      picks: stringRecord(value.picks),
      // Derived by the backend from this description when the take was stored.
      mapped: stringRecord(value.mapped),
    };
  } catch {
    return null;
  }
}

/**
 * Rebuild the Voice Design workspace from a generation-history take. A take
 * with a stored recipe comes back exactly as it was drafted. An older take
 * only has its combined instruct: it returns as the description, so a
 * free-form engine receives it unchanged, with its recognised tags as
 * details, so OmniVoice does too.
 */
export function designDraftFromTake(item: HistoryItem): DesignDraft {
  const recipe = parseRecipe(item.design_recipe);
  const description = recipe?.description ?? item.instruct ?? '';
  const mapped = mergeDescribedAttrs(recipe?.mapped ?? instructToVdStates(description));
  const draft: DesignDraft = {
    text: item.text,
    attrs: mapped,
    seed: item.seed ?? pickDesignSeed(false, null),
    profileId: item.profile_id,
    description,
    picks: Object.fromEntries(
      Object.entries(recipe?.picks ?? {}).map(([category, value]) => [
        category,
        { value, description },
      ]),
    ),
    mapped,
  };
  // The picks were made with this description, so they layer over its mapping.
  return applyDescription(draft, mapped, description);
}

/** Restore a saved design recipe without leaking values from the previous voice. */
export function restoreDesignProfile(
  profile: Profile,
  fallbackSeed: number,
): Pick<DesignDraft, 'attrs' | 'seed' | 'profileId'> & { language: string } {
  const editedAttrs = instructToVdStates(profile.instruct ?? '');
  let attrs = editedAttrs;
  if (profile.vd_states) {
    try {
      const parsed = JSON.parse(profile.vd_states);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        // vd_states is the saved source of truth for explicit controls. Keep
        // any values that are only represented in the descriptive prompt.
        attrs = { ...editedAttrs, ...(parsed as Record<string, string>) };
      }
    } catch {
      /* The complete instruct-derived fallback remains usable. */
    }
  }
  return {
    attrs: mergeDescribedAttrs(attrs),
    seed: profile.seed ?? fallbackSeed,
    profileId: profile.id,
    language: profile.language || 'Auto',
  };
}
