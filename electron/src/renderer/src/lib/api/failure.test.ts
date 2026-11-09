import { describe, expect, it } from 'vitest';
import '@/i18n';
import i18next from 'i18next';
import { ApiError } from './client';
import { apiFailureGuidance } from './failure';

describe('apiFailureGuidance', () => {
  it('localizes a diagnosis at the top level of the payload', () => {
    const error = new ApiError(500, 'x', { docs_topic: 'DISK_FULL' } as never);
    expect(apiFailureGuidance(error)).toBe(i18next.t('tts_errors.disk_full'));
  });

  it('localizes a diagnosis nested under an HTTPException detail', () => {
    const error = new ApiError(400, 'x', {
      detail: { docs_topic: 'DISK_FULL', hint: 'Free some space.' },
    } as never);
    expect(apiFailureGuidance(error)).toBe(i18next.t('tts_errors.disk_full'));
  });

  it("falls back to a nested hint when the topic isn't in the catalog", () => {
    const error = new ApiError(400, 'x', {
      detail: { docs_topic: 'SOMETHING_NEW', hint: 'Free some space.' },
    } as never);
    expect(apiFailureGuidance(error)).toBe('Free some space.');
  });

  it('returns nothing for an unclassified failure', () => {
    expect(
      apiFailureGuidance(new ApiError(400, 'x', { detail: 'plain' } as never)),
    ).toBeUndefined();
  });
});
