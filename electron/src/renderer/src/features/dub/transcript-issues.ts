import type { DubSegment } from './dub-session';

export function getDubTranslationError(segment: DubSegment, language?: string) {
  // The scalar belongs to the last translation attempt. Only legacy records
  // without a per-language map can safely use it for the current transcript.
  if (segment.translate_errors) return language ? segment.translate_errors[language] : undefined;
  return segment.translate_error;
}
