import type { EngineBackend, ExpressionCaps } from '@/lib/api/types';
import { TAGS } from '@/lib/languages';

/** Every reaction tag some shipped engine renders; menus gray out the rest. */
export const KNOWN_TAGS: readonly string[] = [...TAGS, '[breath]'];

export const EXPRESSIVE_DOCS_URL =
  'https://github.com/debpalash/VoiceStudio/blob/main/docs/expressive-speech.md';

/**
 * What the engine does with emotion and reaction tags (#3422). A backend that
 * predates `expression_caps` (an older remote server) keeps its old behaviour:
 * IndexTTS2's flag gates emotion and every tag stays offered.
 */
export function expressionCaps(engine: EngineBackend | null | undefined): ExpressionCaps {
  if (engine?.expression_caps) return engine.expression_caps;
  return { emotion: engine?.supports_emotion ? 'graded' : 'none', tags: [...TAGS] };
}

/**
 * "CosyVoice 3 (9 langs, …)" → "CosyVoice 3", for sentences that name the engine.
 * The bundled engine is listed under the app's own name; "isn't available with
 * VoiceStudio" reads as the whole app lacking it, so name its model instead.
 */
export function engineShortName(engine: EngineBackend | null | undefined): string {
  const name = engine?.display_name ?? engine?.id ?? '';
  const [, short = name, details = ''] = /^(.*?)\s*\((.*)\)\s*$/.exec(name) ?? [];
  if (short === 'VoiceStudio') return details.split(',')[0].split('/').pop()?.trim() || short;
  return short || name;
}
