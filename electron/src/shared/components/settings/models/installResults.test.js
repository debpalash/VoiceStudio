import { describe, it, expect } from 'vitest';
import { failedInstalls, installFailureMessage } from './installResults';

const models = [{ repo_id: 'a/first' }, { repo_id: 'b/second' }, { repo_id: 'c/third' }];

describe('failedInstalls', () => {
  it('names the repository each rejection belongs to, even when earlier requests succeeded', () => {
    // The bug class: filtering rejected results first and then indexing the
    // request list pairs 'c/third's error with 'a/first'.
    const results = [
      { status: 'fulfilled', value: {} },
      { status: 'fulfilled', value: {} },
      { status: 'rejected', reason: new Error('gated repo') },
    ];
    const failed = failedInstalls(results, models);
    expect(failed).toEqual([{ repo: 'c/third', error: results[2].reason }]);
    expect(installFailureMessage(failed)).toBe('c/third: gated repo');
  });

  it('keeps request order across several failures and accepts non-Error reasons', () => {
    const results = [
      { status: 'rejected', reason: 'offline' },
      { status: 'fulfilled', value: {} },
      { status: 'rejected', reason: new Error('disk full') },
    ];
    expect(installFailureMessage(failedInstalls(results, models))).toBe(
      'a/first: offline · c/third: disk full',
    );
  });

  it('returns nothing when every install started', () => {
    expect(failedInstalls([{ status: 'fulfilled' }], models)).toEqual([]);
    expect(failedInstalls(undefined, models)).toEqual([]);
  });
});
