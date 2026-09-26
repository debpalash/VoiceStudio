import { describe, it, expect, beforeEach, vi } from 'vitest';

import { addBreadcrumb, getBreadcrumbs, formatBreadcrumbs, clearBreadcrumbs } from './breadcrumbs';

describe('breadcrumbs', () => {
  beforeEach(() => clearBreadcrumbs());

  it('records actions in order', () => {
    addBreadcrumb('view:clone');
    addBreadcrumb('generate:start (clone)');
    expect(getBreadcrumbs().map((b) => b.action)).toEqual(['view:clone', 'generate:start (clone)']);
  });

  it('collapses immediate repeats so render storms cannot flush the ring', () => {
    addBreadcrumb('view:dub');
    addBreadcrumb('view:dub');
    addBreadcrumb('view:dub');
    expect(getBreadcrumbs()).toHaveLength(1);
  });

  it('caps the ring at 20', () => {
    vi.useFakeTimers();
    for (let i = 0; i < 30; i++) {
      vi.advanceTimersByTime(3000); // past the repeat-collapse window
      addBreadcrumb(`action-${i}`);
    }
    vi.useRealTimers();
    const crumbs = getBreadcrumbs();
    expect(crumbs).toHaveLength(20);
    expect(crumbs[0].action).toBe('action-10');
  });

  it('formats one line per crumb', () => {
    addBreadcrumb('view:settings');
    const out = formatBreadcrumbs();
    expect(out).toMatch(/\d{2}:\d{2}:\d{2} view:settings/);
  });

  it('handles empty ring', () => {
    expect(formatBreadcrumbs()).toBe('');
  });
});
