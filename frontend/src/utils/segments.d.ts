export interface SegmentInput {
  text: string;
  profile_id?: string;
  instruct?: string;
  speed?: number;
  target_lang?: string;
  direction?: string;
  effect_preset?: string;
  speaker_id?: string;
}
export function segmentGenInputs(segment: SegmentInput): Omit<SegmentInput, 'speaker_id'>;
export function autoProfileId(speakerId: string): string;
export function applySpeakerCloneDefaults<T extends SegmentInput>(
  segments: T[],
  clones: Record<string, unknown>,
): T[];
export function assignSpeakerProfile<T extends SegmentInput>(
  segments: T[],
  speakerId: string,
  profileId: string,
): T[];
export function castParts(segment: SegmentInput): unknown;
export function castSpeakers(segments: SegmentInput[]): unknown[];
export function castSourcesFromJob(job: Record<string, unknown>): Record<string, unknown>;
export function withoutCueSource<T>(segment: T): T;
export function settleCueSources<T>(segments: T[]): T[];
export function cueSourceId(segment: unknown): string | undefined;
export function withOriginalCueSource<T>(segment: T): T;
