/** A deliberate source choice wins; old auto-detect draft values are not languages. */
export function dubSourceLanguage(selected?: string, detected?: string): string | undefined {
  const unknown = new Set(['auto', 'und', 'unknown', 'none']);
  return [selected, detected]
    .map((value) => value?.trim())
    .find((value) => value !== undefined && value !== '' && !unknown.has(value.toLowerCase()));
}
