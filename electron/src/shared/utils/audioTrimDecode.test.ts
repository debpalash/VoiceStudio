// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { decodeToMonoLowRate } from './audioTrim';
afterEach(() => vi.unstubAllGlobals());

function sourceBlob(bytes: number[]) {
  const data = new Uint8Array(bytes);
  const file = new Blob([data]);
  // jsdom lacks Blob.arrayBuffer; keep the byte source controlled while using
  // the renderer's real DOM setup, just as the hosted shared suite does.
  Object.defineProperty(file, 'arrayBuffer', { value: async () => data.buffer });
  return file;
}

it('decodes a trim source without waiting for a media-element metadata event', async () => {
  vi.useFakeTimers();
  try {
    const metadata = vi.fn();
    vi.stubGlobal('Audio', class { constructor() { metadata(); } addEventListener() {} });
    const decoded = { duration: 2, length: 44100, sampleRate: 22050 };
    const decode = vi.fn(async () => decoded);
    const context = vi.fn(function (this: unknown) { return { decodeAudioData: decode }; });
    vi.stubGlobal('window', { OfflineAudioContext: context });
    const file = sourceBlob([1, 2, 3]);
    const pending = decodeToMonoLowRate(file);
    const bounded = Promise.race([pending, new Promise((resolve) => setTimeout(() => resolve('metadata stalled'), 100))]);
    await vi.advanceTimersByTimeAsync(100);
    expect(await bounded).toBe(decoded);
    expect(context).toHaveBeenCalledWith(1, expect.any(Number), 22050);
    expect(decode).toHaveBeenCalledWith(await file.arrayBuffer());
    expect(metadata).not.toHaveBeenCalled();
  } finally { vi.useRealTimers(); }
});

it('uses the configured sample rate and reports actual decoder failures', async () => {
  const failure = new Error('Invalid audio data');
  const decode = vi.fn(async () => { throw failure; });
  const context = vi.fn(function (this: unknown) { return { decodeAudioData: decode }; });
  vi.stubGlobal('window', { webkitOfflineAudioContext: context });
  vi.stubGlobal('Audio', class { duration = 1; addEventListener(name: string, callback: () => void) { if (name === 'loadedmetadata') queueMicrotask(callback); } });
  await expect(decodeToMonoLowRate(sourceBlob([0]), 16000)).rejects.toBe(failure);
  expect(context).toHaveBeenCalledWith(1, expect.any(Number), 16000);
});
