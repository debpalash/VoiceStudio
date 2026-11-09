import { expect, it, vi } from 'vitest';

vi.mock('@/lib/i18n-text', () => ({
  tr: (key: string, options?: Record<string, unknown>) =>
    key === 'common.unknown'
      ? 'unknown'
      : key === 'workflowRun.invalid_language'
        ? 'Choose source and target languages.'
        : `Unsupported: ${String(options?.languages)}`,
}));
import { ApiError } from '@/lib/api/client';
import { describeDubTranslationError } from './translation-error';

it('localizes structured unsupported-language failures', () => {
  const error = new ApiError(400, 'raw backend text', {
    code: 'unsupported_translation_language',
    languages: ['xx', 'kas'],
  });
  expect(describeDubTranslationError(error)).toBe('Unsupported: xx, kas');
});

it('ignores unrelated failures and handles an absent language list', () => {
  expect(describeDubTranslationError(new Error('network'))).toBeNull();
  expect(
    describeDubTranslationError(
      new ApiError(400, 'raw', {
        code: 'unsupported_translation_language',
      }),
    ),
  ).toBe('Unsupported: unknown');
});

it('localizes the missing ASR source-language recovery action', () => {
  const error = new ApiError(400, 'raw English backend text', { code: 'source_language_required' });
  expect(describeDubTranslationError(error)).toBe('Choose source and target languages.');
});
