export function decodeToMonoLowRate(file: Blob, targetSR?: number): Promise<AudioBuffer>;
export function sliceToMono(buffer: AudioBuffer, startSec: number, endSec: number): Float32Array;
export function encodeWav(
  samples: Float32Array,
  sampleRate: number,
  channels?: number,
): ArrayBuffer;
