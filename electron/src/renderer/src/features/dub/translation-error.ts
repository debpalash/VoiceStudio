import { ApiError } from '@/lib/api/client';
import { tr } from '@/lib/i18n-text';

/** Convert structured translation failures to localized, actionable copy. */
export function describeDubTranslationError(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  if (error.payload?.code === 'source_language_required') return tr('workflowRun.invalid_language');
  if (error.payload?.code !== 'unsupported_translation_language') return null;
  const languages = Array.isArray(error.payload.languages)
    ? error.payload.languages.filter((value): value is string => typeof value === 'string')
    : [];
  return tr('dub_workflow.unsupported_translation_language', {
    languages: languages.join(', ') || tr('common.unknown'),
  });
}
