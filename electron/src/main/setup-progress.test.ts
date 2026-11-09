// @vitest-environment node
import { describe, expect, it } from 'vitest';
import {
  DOWNLOAD_STALL_MS,
  SetupProgressTracker,
  cleanProcessLine,
  parseByteSize,
} from './setup-progress';

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

  it('matches one package across uv spellings of its name', () => {
    const tracker = new SetupProgressTracker();
    tracker.ingest('Downloading pydantic-core (2 MiB)');
    tracker.ingest('Downloading ruamel.yaml (1 MiB)');

    expect(tracker.ingest('pydantic_core 1 MiB/2 MiB')).toMatchObject({
      activePackage: 'pydantic-core',
      downloadedBytes: 1024 ** 2,
      totalBytes: 3 * 1024 ** 2,
    });
    expect(tracker.ingest('Downloaded Pydantic_Core')).toMatchObject({
      completedDownloads: 1,
      activePackage: 'ruamel.yaml',
      downloadsComplete: false,
    });
    expect(tracker.ingest('Downloaded ruamel_yaml')).toMatchObject({
      completedDownloads: 2,
      downloadedBytes: 3 * 1024 ** 2,
      downloadsComplete: true,
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

  it.each([
    ['torch', 'torchvision'],
    ['torchvision', 'torch'],
  ])('credits concurrent bytes to the right package when %s is announced first', (first, second) => {
    const tracker = new SetupProgressTracker();
    const sizes: Record<string, string> = { torch: '20 MiB', torchvision: '10 MiB' };
    tracker.ingest(`Downloading ${first} (${sizes[first]})`);
    tracker.ingest(`Downloading ${second} (${sizes[second]})`);

    expect(tracker.ingest('torchvision ------ 5 MiB/10 MiB')).toMatchObject({
      activePackage: 'torchvision',
      downloadedBytes: 5 * 1024 ** 2,
      totalBytes: 30 * 1024 ** 2,
    });
    expect(tracker.ingest('torch ------ 4 MiB/20 MiB')).toMatchObject({
      activePackage: 'torch',
      downloadedBytes: 9 * 1024 ** 2,
      totalBytes: 30 * 1024 ** 2,
    });
  });

  it.each([
    ['nvidia-cublas', 'nvidia-cublas-cu12'],
    ['ruamel', 'ruamel.yaml'],
  ])('does not confuse %s with %s', (short, long) => {
    const tracker = new SetupProgressTracker();
    tracker.ingest(`Downloading ${short} (8 MiB)`);
    tracker.ingest(`Downloading ${long} (2 MiB)`);
    expect(tracker.ingest(`${long} 1 MiB/2 MiB`)).toMatchObject({
      activePackage: long,
      downloadedBytes: 1024 ** 2,
      totalBytes: 10 * 1024 ** 2,
    });
  });

  it('ignores progress for a package that was never announced', () => {
    const tracker = new SetupProgressTracker();
    tracker.ingest('Downloading torch (20 MiB)');
    expect(tracker.ingest('torchaudio 3 MiB/9 MiB')).toBeNull();
    expect(tracker.snapshot()).toMatchObject({
      activePackage: 'torch',
      totalBytes: 20 * 1024 ** 2,
      downloadedBytes: 0,
    });
  });

  it('normalizes uv units and terminal escape sequences', () => {
    expect(parseByteSize('1.5', 'GiB')).toBe(1.5 * 1024 ** 3);
    expect(cleanProcessLine('\u001b[2K Downloaded torch\r')).toBe('Downloaded torch');
  });

  // #3411: uv prints nothing while a large wheel downloads.
  it('does not let repeated byte reports or announcements hide five minutes without progress', () => {
    const tracker = new SetupProgressTracker();
    tracker.ingest('Downloading torch (3 GiB)', 0);
    tracker.ingest('torch 12 MiB/3 GiB', 1_000);
    tracker.ingest('torch 13 MiB/3 GiB', 2_000);
    expect(tracker.snapshot(2_000).bytesPerSecond).toBeGreaterThan(0);
    for (let at = 32_000; at <= 302_000; at += 30_000) {
      tracker.ingest('Downloading torch (3 GiB)', at);
      tracker.ingest('torch 13 MiB/3 GiB', at);
    }
    expect(tracker.snapshot(302_000)).toMatchObject({ stalled: true, stalledSeconds: 300 });
    expect(tracker.snapshot(302_000).bytesPerSecond).toBeUndefined();
    expect(tracker.snapshot(302_000).etaSeconds).toBeUndefined();
    tracker.ingest('torch 14 MiB/3 GiB', 303_000);
    expect(tracker.snapshot(303_000).stalled).toBeUndefined();
  });

  it('names the source host and flags a stall only when no bytes arrive', () => {
    const tracker = new SetupProgressTracker();
    tracker.setDownloadHosts(new Map([['nvidia_cudnn_cu12', 'files.pythonhosted.org']]));
    tracker.ingest('Downloading nvidia-cudnn-cu12 (634.0MiB)', 0);
    expect(tracker.snapshot(1_000)).toMatchObject({
      activePackage: 'nvidia-cudnn-cu12',
      downloadHost: 'files.pythonhosted.org',
    });

    // Bytes landing in uv's cache keep a silent download live.
    for (let at = 10_000; at <= 120_000; at += 10_000) tracker.observeInFlight(at, at);
    expect(tracker.snapshot(120_000 + DOWNLOAD_STALL_MS - 1).stalled).toBeUndefined();
    expect(tracker.snapshot(120_000 + DOWNLOAD_STALL_MS)).toMatchObject({
      stalled: true,
      stalledSeconds: DOWNLOAD_STALL_MS / 1000,
      downloadHost: 'files.pythonhosted.org',
    });
    // A finished wheel leaving the temp area is not growth; the next one is.
    tracker.observeInFlight(5, 200_000);
    expect(tracker.snapshot(200_000).stalled).toBe(true);
    tracker.observeInFlight(10, 201_000);
    expect(tracker.snapshot(201_000).stalled).toBeUndefined();
  });

  it('never reports a stall once downloads are complete', () => {
    const tracker = new SetupProgressTracker();
    tracker.ingest('Downloading torch (2.0 GiB)', 0);
    tracker.ingest('Downloaded torch', 1_000);
    expect(tracker.snapshot(1_000 + DOWNLOAD_STALL_MS * 3).stalled).toBeUndefined();
    tracker.reset();
    expect(tracker.snapshot(DOWNLOAD_STALL_MS * 3).stalled).toBeUndefined();
  });

  it('shows wheel downloads the shell runs itself, with rate, stall and hand-back to uv (#3411)', () => {
    const tracker = new SetupProgressTracker();
    tracker.setDownloadHosts(new Map([['torch', 'download-r2.pytorch.org']]));
    tracker.observeTransfer('torch', 0, 3_000_000_000, 0);
    tracker.observeTransfer('torch', 500_000_000, 3_000_000_000, 10_000);
    const moving = tracker.observeTransfer('torch', 1_000_000_000, 3_000_000_000, 20_000);
    expect(moving).toMatchObject({
      activePackage: 'torch',
      downloadHost: 'download-r2.pytorch.org',
      downloadedBytes: 1_000_000_000,
      totalBytes: 3_000_000_000,
      downloadsComplete: false,
    });
    expect(moving.bytesPerSecond).toBeCloseTo(50_000_000);
    expect(tracker.snapshot(20_000 + DOWNLOAD_STALL_MS).stalled).toBe(true);
    expect(tracker.observeTransfer('torch', 3_000_000_000, 3_000_000_000, 80_000)).toMatchObject({
      completedDownloads: 1,
      downloadsComplete: true,
    });
    // A failed fast download is withdrawn: uv announces its own download next.
    tracker.observeTransfer('torch', 0, 0, 90_000);
    expect(tracker.snapshot(90_000 + DOWNLOAD_STALL_MS).stalled).toBeUndefined();
  });
});
