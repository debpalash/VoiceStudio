// Microphone PCM capture for the opt-in AEC path and for WebViews that cannot
// construct MediaRecorder. Routes a getUserMedia stream through an
// AudioWorklet that emits fixed-size Float32 frames; the caller converts,
// tags, streams, or WAV-encodes them.

const WORKLET_URL = '/aec-worklet.js';

// Anti-alias filtering for the decimation below. resampleInterleavedFrame
// picks samples by linear interpolation, which is not a low-pass: taking a
// 48 kHz stream to 16 kHz that way folds everything above 8 kHz back down
// into the speech band as tones that were never spoken, and the ASR is fed
// the result. The browser only hands us 48 kHz when it refuses the requested
// 16 kHz AudioContext — WKWebView does — so this is the normal path there,
// not an edge case.
//
// Three cascaded Butterworth-Q biquads (~36 dB/octave) run in the audio
// graph rather than per frame, so the filter keeps its state across frame
// boundaries instead of restarting 50 times a second. The cutoff sits below
// Nyquist to leave room for the rolloff; speech has little energy up there.
const ANTIALIAS_STAGES = 3;
const ANTIALIAS_CUTOFF_RATIO = 0.4;

export function buildAntiAliasChain(ctx, targetRate) {
  if (typeof ctx.createBiquadFilter !== 'function') return [];
  const stages = [];
  for (let stage = 0; stage < ANTIALIAS_STAGES; stage += 1) {
    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = ANTIALIAS_CUTOFF_RATIO * targetRate;
    filter.Q.value = Math.SQRT1_2; // Butterworth — flat passband, no resonant peak
    stages.push(filter);
  }
  return stages;
}

export function resampleInterleavedFrame(frame, inputRate, outputRate, channels) {
  if (inputRate === outputRate || frame.length === 0) return frame;

  const inputFrames = Math.floor(frame.length / channels);
  const outputFrames = Math.max(1, Math.round((inputFrames * outputRate) / inputRate));
  const output = new Float32Array(outputFrames * channels);
  const sourceStep = inputRate / outputRate;

  for (let outputIndex = 0; outputIndex < outputFrames; outputIndex += 1) {
    const sourcePosition = outputIndex * sourceStep;
    const lowerIndex = Math.min(Math.floor(sourcePosition), inputFrames - 1);
    const upperIndex = Math.min(lowerIndex + 1, inputFrames - 1);
    const mix = sourcePosition - lowerIndex;

    for (let channel = 0; channel < channels; channel += 1) {
      const lower = frame[lowerIndex * channels + channel];
      const upper = frame[upperIndex * channels + channel];
      output[outputIndex * channels + channel] = lower + (upper - lower) * mix;
    }
  }

  return output;
}

/**
 * Start capturing ``stream`` as Float32 mono frames at ``sampleRate``.
 *
 * @param {MediaStream} stream      mic stream from getUserMedia
 * @param {(frame: Float32Array) => void} onFrame  called per frame
 * @param {{sampleRate?: number, frameSize?: number, channels?: number}} opts
 * @returns {Promise<() => Promise<void>>}  async stop() that tears down the graph
 */
export async function startMicCapture(
  stream,
  onFrame,
  { sampleRate = 16000, frameSize = 320, channels = 1 } = {},
) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx({ sampleRate });
  if (ctx.state === 'suspended') {
    try {
      await ctx.resume();
    } catch {
      /* reported below — a context that never runs emits no frames at all */
    }
  }
  if (ctx.state === 'suspended') {
    // Failing loudly beats the alternative: a suspended context runs no
    // worklet, so the pill sits on "Listening" forever while not one frame
    // is captured. The caller can surface this; silence cannot be surfaced.
    try {
      await ctx.close();
    } catch {
      /* ignore */
    }
    throw new Error('mic-suspended: the audio context could not be resumed');
  }
  await ctx.audioWorklet.addModule(WORKLET_URL);
  const src = ctx.createMediaStreamSource(stream);
  const sourceFrameSize = Math.max(1, Math.round((frameSize * ctx.sampleRate) / sampleRate));
  const node = new AudioWorkletNode(ctx, 'aec-frame-emitter', {
    processorOptions: { frameSize: sourceFrameSize, channels },
  });
  node.port.onmessage = (e) =>
    onFrame(resampleInterleavedFrame(e.data, ctx.sampleRate, sampleRate, channels));
  // Mic → [anti-alias] → worklet. Only when the browser refused the requested
  // rate; when it honors it there is no decimation and nothing to filter.
  const antiAlias = ctx.sampleRate > sampleRate ? buildAntiAliasChain(ctx, sampleRate) : [];
  const chain = [src, ...antiAlias, node];
  for (let i = 0; i < chain.length - 1; i += 1) chain[i].connect(chain[i + 1]);

  const stop = async function stop() {
    try {
      node.port.onmessage = null;
    } catch {
      /* ignore */
    }
    try {
      node.disconnect();
    } catch {
      /* ignore */
    }
    try {
      src.disconnect();
    } catch {
      /* ignore */
    }
    for (const filter of antiAlias) {
      try {
        filter.disconnect();
      } catch {
        /* ignore */
      }
    }
    try {
      await ctx.close();
    } catch {
      /* ignore */
    }
  };
  // Existing callers use this value as a function. The property lets generic
  // PCM/WAV recording encode the delivered frames at their actual rate.
  stop.sampleRate = sampleRate;
  stop.channels = channels;
  return stop;
}
