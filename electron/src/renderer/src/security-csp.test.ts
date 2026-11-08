import { describe, expect, it } from 'vitest';
import html from '../index.html?raw';

describe('renderer content security policy', () => {
  it('lets the interface reach no analytics host; the backend sends opted-in events', () => {
    expect(html).not.toMatch(/posthog|supermetry/i);
  });
});
