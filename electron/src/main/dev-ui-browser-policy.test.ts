import { describe, expect, it } from 'vitest';
import { shouldOpenDevUiInBrowser } from './dev-ui-browser-policy';

describe('dev UI browser policy', () => {
  it('keeps the system browser closed unless the developer explicitly opts in', () => {
    expect(shouldOpenDevUiInBrowser({})).toBe(false);
    expect(shouldOpenDevUiInBrowser({ VOICESTUDIO_OPEN_DEV_UI_IN_BROWSER: '0' })).toBe(false);
    expect(shouldOpenDevUiInBrowser({ VOICESTUDIO_OPEN_DEV_UI_IN_BROWSER: '1' })).toBe(true);
  });
});
