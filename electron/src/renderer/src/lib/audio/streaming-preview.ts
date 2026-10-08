import { claimPlayback } from './playback';

type AudioContextWindow = Window & {
  webkitAudioContext?: typeof AudioContext;
};

export interface StreamingPreview {
  appendPcm16Base64(value: string): void;
  appendPcm16Bytes(value: ArrayBuffer): void;
  finalize(): void;
  fail(): void;
}

export function supportsStreamingPreview(): boolean {
  if (typeof window === 'undefined') return false;
  return Boolean(window.AudioContext || (window as AudioContextWindow).webkitAudioContext);
}

function decodePcm16Base64(value: string): Float32Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  const pcm = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength >> 1);
  const samples = new Float32Array(pcm.length);
  for (let index = 0; index < pcm.length; index += 1) samples[index] = pcm[index]! / 32768;
  return samples;
}

function decodePcm16Bytes(value: ArrayBuffer): Float32Array {
  const pcm = new Int16Array(value, 0, value.byteLength >> 1);
  const samples = new Float32Array(pcm.length);
  for (let index = 0; index < pcm.length; index += 1) samples[index] = pcm[index]! / 32768;
  return samples;
}

/** Play backend PCM chunks immediately while the final WAV is still rendering. */
export function createStreamingPreview(
  sampleRate: number,
  crossfadeMs = 0,
  onDone?: () => void,
): StreamingPreview {
  const Context = window.AudioContext || (window as AudioContextWindow).webkitAudioContext;
  if (!Context) throw new Error('Web Audio is unavailable');
  const context = new Context({ sampleRate });
  const nodes: Array<{ source: AudioBufferSourceNode; gain: GainNode }> = [];
  const crossfadeSeconds = Math.max(0, crossfadeMs) / 1000;
  let nextStart = context.currentTime + 0.03;
  let previousDuration = 0;
  let finished = false;
  let complete = false;
  let timer: ReturnType<typeof setInterval> | undefined;
  let release: (() => void) | undefined;

  const stop = () => {
    if (finished) return;
    finished = true;
    if (timer) clearInterval(timer);
    for (const node of nodes) {
      try {
        node.source.stop();
      } catch {
        /* The source may have already ended. */
      }
      node.source.disconnect();
      node.gain.disconnect();
    }
    nodes.length = 0;
    void context.close().catch(() => {});
    release?.();
    onDone?.();
  };

  release = claimPlayback(stop, 'output');
  if (context.state === 'suspended') void context.resume().catch(() => {});
  timer = setInterval(() => {
    // Close only after the last scheduled source has fully played; closing
    // the context earlier cuts the tail of the final chunk.
    if (complete && context.currentTime >= nextStart) stop();
  }, 100);

  return {
    appendPcm16Base64(value) {
      append(decodePcm16Base64(value));
    },
    appendPcm16Bytes(value) {
      append(decodePcm16Bytes(value));
    },
    finalize() {
      complete = true;
      if (!nodes.length) stop();
    },
    fail: stop,
  };

  function append(samples: Float32Array) {
    if (finished) return;
    if (!samples.length) return;
    const duration = samples.length / sampleRate;
    let fade = nodes.length ? Math.min(crossfadeSeconds, previousDuration, duration) : 0;
    let start = nextStart - fade;
    if (start < context.currentTime + 0.01) {
      // Underrun: the backend paused long enough that the planned start is
      // already behind the device clock. Crossfade only the part that still
      // overlaps the previous source; if it already ended, start at full gain.
      start = context.currentTime + 0.02;
      fade = Math.max(0, Math.min(fade, nextStart - start));
    }

    const buffer = context.createBuffer(1, samples.length, sampleRate);
    buffer.getChannelData(0).set(samples);
    const source = context.createBufferSource();
    const gain = context.createGain();
    source.buffer = buffer;
    source.connect(gain);
    gain.connect(context.destination);
    if (fade > 0) {
      const previous = nodes.at(-1);
      previous?.gain.gain.setValueAtTime(1, start);
      previous?.gain.gain.linearRampToValueAtTime(0, start + fade);
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(1, start + fade);
    }
    source.start(start);
    nodes.push({ source, gain });
    previousDuration = duration;
    nextStart = start + duration;
  }
}
