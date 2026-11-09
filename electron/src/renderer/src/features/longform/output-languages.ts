import { LANGUAGES } from '@/lib/languages';
import { canonicalLanguage } from '@/lib/language-variants';

/**
 * Narration languages for Stories and Audiobook: the same TTS vocabulary as
 * Clone and Design, so every language the active engine supports is offered
 * (#2810). A saved draft keeps its current value listed so the picker can
 * still flag it when the engine does not support it. A saved "Portuguese"
 * is shown as the regional choice it means (#3143), not as an extra row.
 */
export function outputLanguageOptions(current: string): string[] {
  return !current || LANGUAGES.includes(canonicalLanguage(current))
    ? LANGUAGES
    : [...LANGUAGES, current];
}
