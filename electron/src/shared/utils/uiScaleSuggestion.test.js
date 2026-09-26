import { describe, expect, it } from 'vitest';

import { resolveUiScale, suggestUiScale } from './uiScaleSuggestion';

describe('suggestUiScale', () => {
  it('suggests a smaller scale for compact laptop viewports', () => {
    expect(suggestUiScale({ width: 1280, height: 720 })).toBe(0.8);
  });

  it('keeps the reference viewport at 100%', () => {
    expect(suggestUiScale({ width: 1440, height: 900 })).toBe(1);
  });

  it('suggests a larger scale for high-resolution viewports without overgrowing', () => {
    expect(suggestUiScale({ width: 3840, height: 2160 })).toBe(1.3);
  });

  it('uses safe defaults for unavailable dimensions', () => {
    expect(suggestUiScale({})).toBe(1);
  });
});

describe('resolveUiScale', () => {
  it('uses an explicit unconfirmed choice for the live preview', () => {
    expect(
      resolveUiScale({ configured: false, previewed: true, selected: 1.1, suggested: 0.8 }),
    ).toBe(1.1);
  });

  it('uses the screen suggestion before any explicit choice', () => {
    expect(
      resolveUiScale({ configured: false, previewed: false, selected: 1, suggested: 0.8 }),
    ).toBe(0.8);
  });
});
