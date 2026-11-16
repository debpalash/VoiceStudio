import { segmentGenInputs, cueSourceId } from '@shared/utils/segments';
import type { DubSession } from './dub-session';

export type DubGenerationOptions = { regenOnly?: string[]; preview?: boolean };

/** One request builder for inspection and execution: counts describe the actual call. */
export function dubGenerationRequest(
  current: DubSession,
  language: string,
  languageCode: string,
  options: DubGenerationOptions = {},
) {
  return {
    segments: current.segments.map((segment) => ({
      source_ids: segment.source_ids,
      start: segment.start,
      end: segment.end,
      gain: segment.gain !== undefined && segment.gain !== 1 ? segment.gain : undefined,
      ...segmentGenInputs(segment),
      // Keeps the imported caption markup for unchanged exports (#2295).
      cue_source_id: cueSourceId(segment),
    })),
    segment_ids: current.segments.map((segment) => segment.id),
    regen_only: options.regenOnly?.length ? options.regenOnly : null,
    language,
    language_code: languageCode,
    num_step: current.steps,
    guidance_scale: current.guidance ?? 2,
    speed: current.speed ?? 1,
    instruct: current.instruct || undefined,
    auto_fit_overflow: current.autoFitOverflow !== false,
    timing_strategy: current.narration?.enabled
      ? 'narration'
      : current.timingStrategy || 'strict_slot',
    ...(current.narration?.enabled
      ? {
          caption_segments: current.narration.captions.map((segment) => ({
            start: segment.start,
            end: segment.end,
            ...segmentGenInputs(segment),
            cue_source_id: cueSourceId(segment),
          })),
          caption_segment_ids: current.narration.captions.map((segment) => segment.id),
        }
      : {}),
    ...(current.timingStrategy === 'smart_fit' && current.fitOptions
      ? { fit_options: current.fitOptions }
      : {}),
    voice_match: current.narration?.enabled ? 'consistent' : current.voiceMatch || 'per_line',
    // Agent timing must measure the same render the user keeps.
    preview:
      current.narration?.enabled || current.quality === 'agent'
        ? false
        : (options.preview ?? false),
  };
}

/** Compact UI revision marker; audio reuse is independently validated by the backend. */
export function dubGenerationKey(
  session: DubSession,
  language: string,
  code: string,
  options: DubGenerationOptions = {},
) {
  const request = dubGenerationRequest(session, language, code, options);
  const text = JSON.stringify(
    session.narration?.enabled || session.subtitleOnly ? { timing_contract: 2, request } : request,
  );
  let first = 2166136261,
    second = 2654435769;
  for (let index = 0; index < text.length; index++) {
    const char = text.charCodeAt(index);
    first = Math.imul(first ^ char, 16777619);
    second = Math.imul(second ^ char, 2246822519);
  }
  return (
    text.length.toString(16) +
    ':' +
    (first >>> 0).toString(16).padStart(8, '0') +
    (second >>> 0).toString(16).padStart(8, '0')
  );
}

export function dubExportRevision(session: DubSession, code: string) {
  return (
    dubGenerationKey(session, session.target, code) +
    ':' +
    JSON.stringify(session.exportOptions || {})
  );
}
