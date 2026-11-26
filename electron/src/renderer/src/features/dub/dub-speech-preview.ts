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

/**
 * A line with a saved take is read back without synthesis. A line that has
 * none yet is auditioned with its voice, as before guided generation, so the
 * row's listen control never answers "not generated yet".
 */
export function segmentAuditionRequest({
  jobId,
  segment,
  session,
  index,
  code,
  savedUrl,
  saved,
}: {
  jobId: string;
  segment: DubSegment;
  session: DubSession;
  index: number;
  code: string | undefined;
  savedUrl?: string;
  saved: boolean;
}): { path: string; init: RequestInit; synthesizes: boolean } {
  if (savedUrl) return { path: savedUrl, init: {}, synthesizes: false };
  if (saved)
    return {
      path: `/dub/preview/${encodeURIComponent(jobId)}/${index}?lang=${encodeURIComponent(code || '')}`,
      init: {},
      synthesizes: false,
    };
  const inputs = segmentGenInputs(segment);
  return {
    path: '/dub/preview-segment/' + encodeURIComponent(jobId),
    synthesizes: true,
    init: {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        segment_id: segment.id,
        text: inputs.text,
        language: inputs.target_lang || code || session.target,
        instruct: inputs.instruct || session.instruct || undefined,
        profile_id: inputs.profile_id || undefined,
        speed: inputs.speed || session.speed || 1,
        duration:
          (session.timingStrategy || 'strict_slot') === 'strict_slot'
            ? Math.max(0.3, segment.end - segment.start)
            : undefined,
      }),
    },
  };
}
