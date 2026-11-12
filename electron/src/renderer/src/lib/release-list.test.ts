import { describe, expect, test } from 'vitest';
import { channelReleases } from './release-list';

const release = (version: string, prerelease = version.includes('-')) => ({
  version,
  name: `v${version}`,
  date: '2026-11-12T00:00:00Z',
  prerelease,
  notes: '',
});

const history = [
  release('0.5.7'),
  release('0.5.8-beta.4'),
  release('0.5.8-beta.5'),
  release('0.5.8'),
  release('0.5.9-7'), // an untagged preview build
];

describe('channelReleases', () => {
  test('Stable lists only stables, newest first, and suggests nothing', () => {
    const { rows, newerStable } = channelReleases(history, 'stable', '0.5.8');
    expect(rows.map((row) => row.version)).toEqual(['0.5.8', '0.5.7']);
    expect(rows[0].current).toBe(true);
    expect(newerStable).toBeNull();
  });

  test('Beta lists only betas and suggests a stable newer than every beta', () => {
    const { rows, newerStable } = channelReleases(history, 'beta', '0.5.8');
    expect(rows.map((row) => row.version)).toEqual(['0.5.8-beta.5', '0.5.8-beta.4']);
    expect(newerStable?.version).toBe('0.5.8');
    expect(newerStable?.current).toBe(true);
  });

  test('Beta suggests nothing once a newer beta follows the stable', () => {
    const { rows, newerStable } = channelReleases(
      [...history, release('0.5.9-beta.1')],
      'beta',
      '0.5.9-beta.1',
    );
    expect(rows[0]).toMatchObject({ version: '0.5.9-beta.1', current: true });
    expect(newerStable).toBeNull();
  });

  test('Beta suggests nothing when the installed beta is newer than the stable', () => {
    // The newer beta may be missing from a short release list.
    const { newerStable } = channelReleases(history, 'beta', '0.5.9-beta.2');
    expect(newerStable).toBeNull();
  });

  test('sorts by version, not by publish date', () => {
    const late = { ...release('0.5.7'), date: '2026-12-01T00:00:00Z' };
    const { rows } = channelReleases([late, release('0.5.8')], 'stable', '0.5.8');
    expect(rows.map((row) => row.version)).toEqual(['0.5.8', '0.5.7']);
  });
});
