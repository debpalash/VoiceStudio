import { storyToSpans } from '@shared/utils/storyToSpans';
import { castVoice } from './cast-map';
import type { Draft } from './longform-session';
export function storyVoicesReady(draft: Draft, profiles: { id: string }[]) {
  if (
    draft.lines.some((line) => {
      const id = line.profileId || draft.cast.find((c) => c.id === line.character)?.profileId;
      return id && !profiles.some((p) => p.id === id);
    })
  )
    return false;
  const spans = storyToSpans(draft.lines, draft.cast, draft.globalSpeed)
    .flatMap((chapter) => chapter.spans)
    .filter((span) => span.text.trim());
  return (
    spans.length > 0 &&
    spans.every((span) => {
      const id = resolveStoryVoice(draft, span.voice_id, profiles);
      return profiles.some((profile) => profile.id === id);
    })
  );
}

export function resolveStoryVoice(draft: Draft, voice: string | null, profiles: { id: string }[]) {
  return voice
    ? castVoice(draft.voiceCast, voice) ||
        (profiles.some((profile) => profile.id === voice) ? voice : draft.voice)
    : draft.voice;
}
