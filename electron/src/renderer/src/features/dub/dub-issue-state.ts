import type { DubSession } from './dub-session';
import { segmentGenInputs } from '@shared/utils/segments';
import { dubGenerationKey } from './dub-generation-request';

export function dubCueIssues(session: DubSession) {
  if (session.subtitleOnly || session.narration?.enabled) return [];
  return session.segments.flatMap((segment, index) => {
    if (!segment.text.trim() || segment.end - segment.start > 0.05) return [];
    const inputs = segmentGenInputs(segment);
    const compatible = (other: typeof segment | undefined) => {
      if (!other || Math.max(other.end, segment.end) - Math.min(other.start, segment.start) <= 0.05)
        return false;
      const candidate = segmentGenInputs(other);
      return (
        other.speaker_id === segment.speaker_id &&
        candidate.profile_id === inputs.profile_id &&
        candidate.instruct === inputs.instruct &&
        candidate.target_lang === inputs.target_lang &&
        candidate.speed === inputs.speed &&
        other.direction === segment.direction &&
        other.effect_preset === segment.effect_preset &&
        other.gain === segment.gain &&
        Math.max(0, Math.max(other.start, segment.start) - Math.min(other.end, segment.end)) <= 0.3
      );
    };
    const direction = compatible(session.segments[index + 1])
      ? ('next' as const)
      : compatible(session.segments[index - 1])
        ? ('prev' as const)
        : undefined;
    return [{ segment, index, direction }];
  });
}

export function dubErrorRevision(session: DubSession, id: string, code: string) {
  const segment = session.segments.find((row) => row.id === id);
  return segment ? dubGenerationKey({ ...session, segments: [segment] }, session.target, code) : '';
}

export function dubSegmentIssues(session: DubSession) {
  const short = new Map(dubCueIssues(session).map((issue) => [issue.segment.id, issue]));
  const failure = session.lastGenerationError;
  return session.segments
    .flatMap((segment, index) => {
      const cue = short.get(segment.id);
      const failed =
        failure?.segment_id === segment.id &&
        failure.language === session.target &&
        failure.revision === dubErrorRevision(session, segment.id, failure.language);
      const kind = cue
        ? 'short'
        : failed
          ? failure.code === 'dub_speech_missing'
            ? 'missing'
            : 'timing'
          : segment.qc_flagged
            ? 'qc'
            : (segment.fit_status?.audio_rate || 1) > 1.5
              ? 'fast'
              : segment.fit_status?.status === 'overflows'
                ? 'timing'
                : undefined;
      if (!kind) return [];
      return [
        {
          segment,
          index,
          kind,
          severity: cue || failed ? ('error' as const) : ('warning' as const),
          direction: cue?.direction,
        },
      ];
    })
    .sort(
      (a, b) =>
        Number(b.severity === 'error') - Number(a.severity === 'error') ||
        (session.narration?.enabled ? (b.segment.fit_status?.drift_s || 0) - (a.segment.fit_status?.drift_s || 0) : 0) || a.index - b.index,
    );
}
