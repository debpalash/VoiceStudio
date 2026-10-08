import { toast } from 'sonner';
import type { GenerateResult } from '@/lib/api/types';
import { tr } from '@/lib/i18n-text';

const DROPPED_TOAST_MS = 8000;
const DROPPED_TEXT_PREVIEW_CHARS = 120;

/**
 * Tell the user when part of the text rendered to no audio. The take still
 * plays, so nothing else would reveal the gap. Every surface that shows a
 * generation result must call this; `source` names which result was affected
 * when one view shows several (for example Compare voices A/B).
 */
export function announceDroppedSpeech(
  dropped: GenerateResult['dropped'] | undefined,
  source?: string,
): void {
  if (!dropped || !(dropped.count > 0)) return;
  const preview = dropped.text.trim().slice(0, DROPPED_TEXT_PREVIEW_CHARS);
  const count = dropped.count;
  toast.warning(
    preview
      ? tr('tts.droppedChunksWithText', { count, text: preview })
      : tr('tts.droppedChunks', { count }),
    { duration: DROPPED_TOAST_MS, ...(source ? { description: source } : {}) },
  );
}
