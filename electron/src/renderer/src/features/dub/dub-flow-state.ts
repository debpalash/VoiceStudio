import type { DubSession } from './dub-session';

/** A project that skipped separation cannot request a missing background stem. */
export function dubPreservesBackground(session: DubSession): boolean {
  return (
    !session.subtitleOnly &&
    session.separateBackground !== false &&
    (session.exportOptions?.preserveBg ?? true)
  );
}
