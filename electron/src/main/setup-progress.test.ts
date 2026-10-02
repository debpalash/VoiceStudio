// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { SetupProgressTracker, cleanProcessLine, parseByteSize } from './setup-progress';

describe('SetupProgressTracker', () => {
  it('turns uv package and byte output into progress with rate and ETA', () => {
    const tracker = new SetupProgressTracker();
    expect(tracker.ingest('Resolved 240 packages in 20ms', 0)).toMatchObject({
      resolvedPackages: 240,
    });
    tracker.ingest('Downloading torch (2.0 GiB)', 1_000);
    tracker.ingest('Downloading torchvision (512 MiB)', 1_000);
    tracker.ingest('torch 1.0 GiB/2.0 GiB', 2_000);
    const progress = tracker.ingest('torch 1.5 GiB/2.0 GiB', 3_000);

    expect(progress).toMatchObject({
      resolvedPackages: 240,
      downloadedBytes: 1.5 * 1024 ** 3,
      totalBytes: 2.5 * 1024 ** 3,
      estimatedBytes: true,
      activePackage: 'torch',
      transferUpdatedAt: 3_000,
    });
    expect(progress?.bytesPerSecond).toBeCloseTo(0.75 * 1024 ** 3);
    expect(progress?.etaSeconds).toBe(2);
  });

  it('counts completed downloads and installation phases without fake byte totals', () => {
    const tracker = new SetupProgressTracker();
    tracker.ingest('Downloaded pydantic-core');
    expect(tracker.snapshot()).toEqual({
      completedDownloads: 1,
      activityUpdatedAt: expect.any(Number),
    });
    expect(tracker.ingest('Prepared 183 packages in 38.2s')).toMatchObject({
      completedDownloads: 1,
      preparedPackages: 183,
    });
    expect(tracker.ingest('Installed 183 packages in 2.1s')).toMatchObject({
      installedPackages: 183,
    });
  });

  it('keeps the largest concurrent artifact visible while small downloads finish', () => {
    const tracker = new SetupProgressTracker();
    tracker.ingest('Downloading torch (3.2 GiB)');
    tracker.ingest('Downloading scipy (34.9 MiB)');

    expect(tracker.ingest('Downloaded scipy')).toMatchObject({
      activePackage: 'torch',
      completedDownloads: 1,
    });
  });

  it('moves from the last announced download to package installation instead of looking stuck', () => {
    const tracker = new SetupProgressTracker();
    tracker.ingest('Downloading scipy (34.9 MiB)', 1_000);

    const progress = tracker.ingest('Downloaded scipy', 2_000);
    expect(progress).toMatchObject({
      downloadsComplete: true,
      downloadedBytes: 34.9 * 1024 ** 2,
      totalBytes: 34.9 * 1024 ** 2,
      activityUpdatedAt: 2_000,
    });
    expect(progress).not.toHaveProperty('activePackage');
  });

  it('normalizes uv units and terminal escape sequences', () => {
    expect(parseByteSize('1.5', 'GiB')).toBe(1.5 * 1024 ** 3);
    expect(cleanProcessLine('\u001b[2K Downloaded torch\r')).toBe('Downloaded torch');
  });
});


describe('concurrent package byte updates', () => {
  it.each([
    ['torch', 'torchvision'],
    ['torchvision', 'torch'],
    ['pydantic', 'pydantic-core'],
    ['lib', 'lib.v2'],
  ])('matches the complete %s / %s package identifiers', (first, second) => {
    const tracker = new SetupProgressTracker();
    tracker.ingest(`Downloading ${first} (10 MiB)`, 0);
    tracker.ingest(`Downloading ${second} (20 MiB)`, 0);
    expect(tracker.ingest(`${second} 2 MiB / 20 MiB`, 1000)).toMatchObject({
      activePackage: second,
      totalBytes: 30 * 1024 ** 2,
      downloadedBytes: 2 * 1024 ** 2,
    });
    expect(tracker.ingest(`Downloaded ${first}`, 1001)).toMatchObject({
      totalBytes: 30 * 1024 ** 2,
      downloadedBytes: 12 * 1024 ** 2,
    });
  });

  it('does not attribute an unannounced package to a matching substring', () => {
    const tracker = new SetupProgressTracker();
    tracker.ingest('Downloading torch (10 MiB)', 0);
    expect(tracker.ingest('torchvision 2 MiB / 20 MiB', 1000)).toBeNull();
    expect(tracker.snapshot()).toMatchObject({totalBytes: 10 * 1024 ** 2, downloadedBytes: 0});
  });
});
