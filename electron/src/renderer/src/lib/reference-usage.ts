import { CLONE_MAX_SECONDS } from '@/lib/api/generate';
import type { EngineBackend } from '@/lib/api/types';

type EngineRefLimits = Pick<EngineBackend, 'max_ref_seconds' | 'ref_strategy'>;

/** The engine keeps only part of this clip, and chooses that part itself (#2281). */
export function engineTrimsReference(
  engine: EngineRefLimits | null | undefined,
  durationSeconds: number | null,
): boolean {
  const max = engine?.max_ref_seconds;
  return (
    durationSeconds !== null &&
    typeof max === 'number' &&
    max > 0 &&
    durationSeconds > max &&
    (engine?.ref_strategy === 'best_window' || engine?.ref_strategy === 'head')
  );
}

/**
 * What Voice Clone should say about a reference longer than the recommended
 * range: how much of it the active engine really uses. Returns null inside the
 * recommendation, so short clips get no note.
 */
export function referenceUsageNote(
  engine: EngineRefLimits | null | undefined,
  durationSeconds: number | null,
): { kind: 'best_window' | 'head' | 'long'; seconds: number } | null {
  if (durationSeconds === null || durationSeconds <= CLONE_MAX_SECONDS) return null;
  if (engineTrimsReference(engine, durationSeconds)) {
    const seconds = Math.round(engine?.max_ref_seconds ?? 0);
    return engine?.ref_strategy === 'head'
      ? { kind: 'head', seconds }
      : { kind: 'best_window', seconds };
  }
  return { kind: 'long', seconds: CLONE_MAX_SECONDS };
}
