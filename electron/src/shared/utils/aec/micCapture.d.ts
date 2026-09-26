export function startMicCapture(
  stream: MediaStream,
  onFrame: (frame: Float32Array) => void,
  options?: { sampleRate?: number; frameSize?: number; channels?: number },
): Promise<(() => Promise<void>) & { sampleRate: number; channels: number }>;
export function buildAntiAliasChain(context: AudioContext, targetRate: number): BiquadFilterNode[];
export function resampleInterleavedFrame(
  frame: Float32Array,
  inputRate: number,
  outputRate: number,
  channels: number,
): Float32Array;
