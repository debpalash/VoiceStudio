/** Localize only the known structured failure, never a server-supplied key. */
export function languageRejectionMessage(
  value: unknown,
  translate: (key: string, options: { language: string }) => string,
): string | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const failure = value as { code?: unknown; language?: unknown };
  if (failure.code !== 'profile_language_rejected' || typeof failure.language !== 'string')
    return undefined;
  return translate('tts_errors.profile_language_rejected', { language: failure.language });
}
