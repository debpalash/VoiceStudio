import { segmentGenInputs } from '@shared/utils/segments';
import type { DubSegment, DubSession } from './dub-session';

/** Auditions describe one take; edits must never keep a stale green badge. */
export function speechPreviewKey(segment: DubSegment, session: DubSession): string {
  return JSON.stringify([
    segmentGenInputs(segment),
    session.target,
    session.steps,
    session.guidance,
    session.speed,
    session.instruct,
    session.voiceMatch,
  ]);
}

export function speechPreviewFor(
  segment: DubSegment,
  session: DubSession,
  language: string | undefined,
) {
  if (!language) return undefined;
  const preview = session.speechPreviews?.[language]?.[segment.id];
  return preview?.key === speechPreviewKey(segment, session) ? preview.url : undefined;
}
