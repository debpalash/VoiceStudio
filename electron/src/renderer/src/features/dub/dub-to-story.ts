/**
 * Carrying a finished dub into the Stories editor.
 *
 * The transform itself is shared with the browser app
 * (`frontend/src/utils/dubToStory`); this is the Electron wiring around it, kept
 * out of `dub-page.tsx` so the rules that decide whether the action is offered,
 * and what it overwrites, can be tested without mounting the page.
 */
import { dubToStory } from '../../../../../../frontend/src/utils/dubToStory';
import {
  editLongform,
  longformSession,
  storiesImportEpoch,
  type Draft,
} from '../longform/longform-session';
import { clearedScriptPatch, scriptSize } from '../longform/story-clear';

/**
 * Dub phases whose segments are worth carrying over: the text has settled and
 * the user has seen it. Deliberately an allow-list — a phase added later has to
 * opt in rather than inherit an action that may not suit it. `translating` is
 * absent because the lines are still being rewritten under the user.
 */
export const STORY_FROM_DUB_PHASES = ['editing', 'generating', 'done'];

/**
 * What the transform reads off a segment. Looser than `DubSegment` because the
 * shared util is written against unknown fields — segments can arrive from an
 * imported SRT as well as from diarisation — and because it keeps the tests
 * free of the twenty fields a real `DubSegment` carries and this never touches.
 */
export interface DubSegmentLike {
  id?: unknown;
  text?: unknown;
  start?: unknown;
  speaker_id?: unknown;
  profile_id?: unknown;
  merge_parts?: unknown;
}

/** Whether the dub has something a Story could be built from. */
export function canCreateStoryFromDub(session: { phase: string; segments: unknown[] }): boolean {
  return session.segments.length > 0 && STORY_FROM_DUB_PHASES.includes(session.phase);
}

/**
 * Whether loading a dub would overwrite work, which is what decides if the user
 * is asked first. Count pending imports and old renders too: both are lost when
 * the dub replaces the draft, even if the current script has no lines.
 */
export function storiesDraftOccupied(draft: Draft): boolean {
  return (
    scriptSize('stories', draft) > 0 ||
    draft.cast.length > 0 ||
    Object.keys(draft.voiceCast).length > 0 ||
    Boolean(draft.output || draft.outputScript || draft.outputChapters.length)
  );
}

/**
 * Replace the Stories draft with this dub's cast and lines.
 *
 * Returns false and changes nothing when there is nothing to load, or when a
 * longform render is running — `editLongform` is a no-op then, so the caller
 * must not navigate to an editor that would still be showing the old script.
 *
 * The project link is dropped: the lines are no longer the ones that were
 * opened, and keeping the id would let a later save overwrite a saved project
 * with the contents of a dub.
 */
export function loadDubIntoStories(
  segments: DubSegmentLike[],
  options: {
    profiles?: { id?: unknown; name?: unknown }[];
    unknownSpeakerLabel: string;
    newLineId?: () => string;
  },
): boolean {
  if (longformSession.state.active) return false;
  const { cast, tracks } = dubToStory(segments, {
    profiles: options.profiles,
    unknownSpeakerLabel: options.unknownSpeakerLabel,
  });
  if (tracks.length === 0) return false;
  const newLineId = options.newLineId ?? (() => crypto.randomUUID());
  storiesImportEpoch.current += 1;
  editLongform('stories', {
    // Clearing the script the same way "Clear script" does, so a pending import
    // cannot survive underneath the lines that just replaced it.
    ...clearedScriptPatch('stories'),
    cast,
    lines: tracks.map((line) => ({ ...line, id: newLineId(), profileId: null })),
    voiceCast: {},
    projectId: null,
    output: '',
    outputScript: '',
    outputChapters: [],
    outputCachedChapters: 0,
    outputFailedChapters: 0,
  });
  return true;
}
