import type { HistoryItem, Profile } from '@/lib/api/types';
import {
  instructToVdStates,
  mergeDescribedAttrs,
} from '@shared/utils/voiceInstruct';
import { pickDesignSeed } from '@shared/utils/seed';
export const STORAGE = 'voicestudio.design.v1';
export const DESIGN_DRAFT_EVENT = 'voicestudio:design-draft';

export interface DesignDraft {
  text: string;
  attrs: Record<string, string>;
  seed: number;
  profileId: string | null;
}

export function readDraft() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE) || '{}');
    return {
      text: typeof value.text === 'string' ? value.text : '',
      attrs: mergeDescribedAttrs(value.attrs),
      seed: Number.isInteger(value.seed) ? value.seed : pickDesignSeed(false, null),
      profileId: typeof value.profileId === 'string' ? value.profileId : null,
    };
  } catch {
    return {
      text: '',
      attrs: mergeDescribedAttrs(),
      seed: pickDesignSeed(false, null),
      profileId: null,
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

/** Rebuild the Voice Design workspace from a generation-history recipe. */
export function designDraftFromTake(item: HistoryItem): DesignDraft {
  return {
    text: item.text,
    attrs: mergeDescribedAttrs(instructToVdStates(item.instruct ?? '')),
    seed: item.seed ?? pickDesignSeed(false, null),
    profileId: item.profile_id,
  };
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
