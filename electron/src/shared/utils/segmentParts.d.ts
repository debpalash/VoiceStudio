export interface SegmentAttribution {
  speaker_id?: string;
  profile_id?: string;
  direction?: string;
  gain?: number;
  target_lang?: string;
}

export interface SegmentPart extends SegmentAttribution {
  textStart: number;
  textEnd: number;
}

export interface SegmentWithParts extends SegmentAttribution {
  id?: string | number;
  text?: string;
  text_original?: string;
  start?: number;
  end?: number;
  merge_parts?: SegmentPart[];
  merge_parts_original?: SegmentPart[];
}

export const ATTRIBUTION_FIELDS: readonly (keyof SegmentAttribution)[];
export function attributionOf(segment: SegmentWithParts): SegmentAttribution;
export function applyAttribution<T extends SegmentWithParts>(
  segment: T,
  attribution: SegmentAttribution,
): T;
export function partsFor(segment: SegmentWithParts): SegmentPart[];
export function originalPartsFor(segment: SegmentWithParts): SegmentPart[];
export function mergeTextOffset(segment: SegmentWithParts): number;
export function mergedParts(a: SegmentWithParts, b: SegmentWithParts): SegmentPart[];
export function mergedOriginalParts(a: SegmentWithParts, b: SegmentWithParts): SegmentPart[];
export function clipParts(parts: SegmentPart[], from: number, to: number): SegmentPart[];
export function keepParts(parts: SegmentPart[]): SegmentPart[] | undefined;
export function attributionAt(parts: SegmentPart[], at: number): SegmentAttribution;
export function nextSegmentId(existing: Iterable<unknown>, base?: unknown): string;
export const MIN_INSERT_GAP_S: number;
export function visibleMergeAvailability(
  segments: SegmentWithParts[],
  visibleSegments: SegmentWithParts[],
  segment: SegmentWithParts,
): { canMerge: boolean; canMergePrev: boolean };
export function insertionSlot(
  previous: SegmentWithParts,
  next?: SegmentWithParts,
  options?: { defaultDur?: number; minGap?: number },
): { start: number; end: number };
