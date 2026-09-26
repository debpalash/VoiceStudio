export function attachPlaybackTap(
  mediaEl: HTMLMediaElement,
  options?: { sampleRate?: number; frameSize?: number },
): Promise<() => Promise<void>>;
