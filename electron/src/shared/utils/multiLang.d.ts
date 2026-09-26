export interface MultiLanguageTarget {
  lang: string;
  code: string;
}

export function hasCompleteTranslation(
  segments: Array<{ text?: string; text_original?: string; translations?: Record<string, string> }>,
  languageCode: string,
): boolean;

export function multiLangTargets(
  activeLanguage: string,
  activeCode: string,
  selected: MultiLanguageTarget[],
): MultiLanguageTarget[];

export function translationProgressByCode(
  segments: Array<{ text?: string; text_original?: string; translations?: Record<string, string> }>,
  targets: MultiLanguageTarget[],
): Record<string, { ready: number; total: number }>;
