import { apiFetch } from '@/lib/api/client';
import { exportStoryAudio, exportStems } from '@shared/utils/storyExport';
import { overridesToRequest } from '@shared/utils/longformOverrides';
import { resolveStoryVoice } from './story-inputs';
import type { Draft, Line } from './longform-session';
import { beginAppActivity } from '@/lib/app-activity';
export function storyChunkBody(
  draft: Draft,
  text: string,
  profileId: string | null,
  speed: number | null,
  profiles: { id: string }[],
) {
  const body = new FormData();
  body.set('text', text);
  body.set('num_step', String(draft.overrides.numStep ?? 32));
  body.set('speed', String(speed || 1));
  const id = resolveStoryVoice(draft, profileId, profiles);
  if (id) body.set('profile_id', id);
  for (const [key, value] of Object.entries(overridesToRequest(draft.overrides, draft.language)))
    body.set(key, String(value));
  return body;
}
export async function previewStoryLine(
  draft: Draft,
  line: Line,
  signal: AbortSignal,
  profiles: { id: string }[],
) {
  const finishActivity = beginAppActivity('synthesis');
  try {
    const helpers = storyAudioHelpers(draft, signal, profiles);
    const result = await exportStoryAudio([line], helpers.resolve, helpers.fetchChunk);
    signal.throwIfAborted();
    return result.blob;
  } finally {
    finishActivity();
  }
}
function storyAudioHelpers(draft: Draft, signal: AbortSignal, profiles: { id: string }[]) {
  return {
    resolve: (track: Line) => ({
      profileId:
        track.profileId ||
        draft.cast.find((character) => character.id === track.character)?.profileId ||
        draft.voice,
      speed: track.speed || (draft.globalSpeed !== 1 ? draft.globalSpeed : null),
    }),
    fetchChunk: async (text: string, profileId: string | null, speed: number | null) => {
      signal.throwIfAborted();
      const response = await apiFetch('/generate', {
        method: 'POST',
        body: storyChunkBody(draft, text, profileId, speed, profiles),
        signal,
      });
      return response.blob();
    },
  };
}
export async function renderStoryStems(
  draft: Draft,
  signal: AbortSignal,
  profiles: { id: string }[],
  progress: (done: number, total: number) => void,
) {
  const finishActivity = beginAppActivity('synthesis');
  try {
    const helpers = storyAudioHelpers(draft, signal, profiles);
    const result = await exportStems(
      draft.lines.filter((line) => line.text.trim()),
      helpers.resolve,
      helpers.fetchChunk,
      progress,
    );
    signal.throwIfAborted();
    return result;
  } finally {
    finishActivity();
  }
}
