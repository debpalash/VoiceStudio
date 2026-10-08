/** Mirrors the backend's MIN_FREE_GB for older backends that omit it. */
export const DEFAULT_MODEL_DISK_HEADROOM_GB = 10;

export interface ModelDiskShortfall {
  need: string;
  headroom: number;
  total: string;
  free: number;
}

/**
 * The backend refuses a model install unless the download plus a fixed
 * headroom fits on the model disk (`disk_space_error`). Warn with that same
 * rule and name every term, so "needs 4.8 GB, 10.2 GB free" never reads as a
 * contradiction (#2597). Null when it fits or the free space is unknown.
 */
export function modelDiskShortfall(
  downloadGb: number,
  freeGb: number | null | undefined,
  headroomGb: number | null | undefined,
): ModelDiskShortfall | null {
  if (freeGb == null || !(downloadGb > 0)) return null;
  const headroom = headroomGb ?? DEFAULT_MODEL_DISK_HEADROOM_GB;
  const total = downloadGb + headroom;
  if (total <= freeGb) return null;
  return { need: downloadGb.toFixed(1), headroom, total: total.toFixed(1), free: freeGb };
}
