import { generationFailureMessage } from './generationFailureMessage.ts';
import i18n from 'i18next';
import { languageRejectionMessage } from './languageRejection.ts';
/**
 * streamingTts.js — streaming TTS preview (feat: streaming-tts-preview).
 *
 * POSTs /generate with stream=true (NDJSON: "start" → N × "chunk" → "done")
 * and starts playback from the FIRST received chunk while the backend is
 * still synthesizing the rest, instead of waiting for the whole render.
 *
 * Playback is plain Web Audio (AudioContext + scheduled AudioBufferSource
 * nodes) — available in all three Tauri webviews (WKWebView / WebView2 /
 * WebKitGTK) and regular browsers, so the default behavior is identical on
 * macOS, Windows and Linux (strict repo rule). No MediaSource, which WKWebView
 * does not reliably support for audio/wav. Chunks are joined with the same
 * linear crossfade the backend uses for the final file, so the preview
 * timeline matches the saved take.
 *
 * The mini-player integration is the same tracked-'output' claim that
 * playBlobAudio uses (utils/playback.js), so the GlobalAudioPlayer bar shows
 * the streaming state (label + growing duration) with live seek inside the
 * buffered region, pause/resume, and stop.
 *
 * Failure contract: ANY problem after the HTTP response starts — an in-band
 * "error" event, a transport drop mid-body, a Web Audio failure — surfaces as
 * StreamingPreviewError so the caller (useTTS) can fall back to the classic
 * whole-file flow with the user none the wiser. Pre-stream HTTP errors keep
 * their ApiError identity (they would fail the classic flow identically, so
 * falling back would only duplicate the failure).
 */
import { generateSpeechWithinAdmission, withTtsInflight } from '../api/generate';
import { apiFetch } from '../api/client';
import { claimTrackedPlayback } from './playback';

/** True when the webview can progressively play PCM chunks (Web Audio). */
export const supportsStreamingPreview = () =>
  typeof window !== 'undefined' &&
  typeof (window.AudioContext || window.webkitAudioContext) === 'function';

/** A failure AFTER the stream started — the signal to fall back to the
 *  classic whole-file /generate flow. */
export class StreamingPreviewError extends Error {
  constructor(message, opts) {
    super(message, opts);
    this.name = 'StreamingPreviewError';
    // #1190: the backend marks GPU-timeout / pool-saturation error frames
    // `retryable`. Those are NOT worth re-rendering on the classic path — the
    // whole text would be synthesized again against a pool still occupied by
    // the abandoned job, so the user pays the same timeout twice before seeing
    // the same error. Carried here so useTTS can tell the two apart.
    this.retryable = opts?.retryable === true;
    this.retryAfter = opts?.retryAfter ?? null;
    this.terminal = opts?.terminal === true;
    // The backend exception TYPE behind an otherwise generic failure. The
    // floor message is identical for every unclassified engine failure, so
    // without this an auto-filed report cannot be told apart from any
    // other (#1800). Never the exception message — only its class name.
    this.errorClass = opts?.errorClass || null;
  }
}

export const shouldFallbackToClassic = (error) =>
  error instanceof StreamingPreviewError && !error.retryable && !error.terminal;

/** Raw PCM16 bytes (ArrayBuffer / Uint8Array) → Float32 samples in [-1, 1].
 *  The binary-WebSocket twin of decodePcm16Base64 (`/ws/tts` sends raw
 *  frames, not NDJSON base64). Exported for tests. */
export const pcm16BytesToFloat32 = (data) => {
  let bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  // An Int16Array view must start on an even byte; a view carved at an odd
  // offset (sliced framing buffers) would throw a RangeError, so copy it.
  if (bytes.byteOffset % 2) bytes = bytes.slice();
  const pcm = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength >> 1);
  const out = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) out[i] = pcm[i] / 32768;
  return out;
};

/** base64 → Int16 PCM → Float32 samples in [-1, 1]. Exported for tests. */
export const decodePcm16Base64 = (b64) => {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return pcm16BytesToFloat32(bytes);
};

/**
 * Normalized [0..1] waveform peaks across a LIST of sample chunks — the
 * incremental twin of media.js computePeaks (which needs one decoded
 * AudioBuffer). Strided like the original so it stays O(buckets) per call
 * even for hour-long renders. Exported for tests.
 */
export const peaksFromChunkList = (chunkArrays, buckets = 240) => {
  const total = chunkArrays.reduce((n, c) => n + c.length, 0);
  if (!total) return null;
  const n = Math.min(buckets, total);
  const peaks = Array.from({ length: n }, () => 0);
  const bucketSize = total / n;
  const step = Math.max(1, Math.floor(bucketSize / 64));
  let base = 0;
  for (const data of chunkArrays) {
    for (let j = 0; j < data.length; j += step) {
      const v = Math.abs(data[j]);
      const b = Math.min(n - 1, Math.floor((base + j) / bucketSize));
      if (v > peaks[b]) peaks[b] = v;
    }
    base += data.length;
  }
  const top = Math.max(...peaks, 0.001);
  return peaks.map((p) => p / top);
};

export function createStreamingChunkPlayer({ label, sampleRate, crossfadeMs = 0, onDone } = {}) {
  const Context = window.AudioContext || window.webkitAudioContext;
  let audioClock;
  try {
    audioClock = new Context({ sampleRate });
  } catch {
    audioClock = new Context();
  }
  if (audioClock.state === 'suspended') Promise.resolve(audioClock.resume()).catch(() => {});

  const initialDelay = 0.08;
  const drainAllowance = 0.05;
  const restartDelay = 0.02;
  const lateThreshold = 0.01;
  const maximumBlend = Math.max(0, crossfadeMs) / 1000;
  const blocks = [];
  const liveVoices = new Map();
  let voiceNumber = 0;
  let lastVoice = null;
  let duration = 0;
  let clockStart = null;
  let mediaOrigin = 0;
  let inputComplete = false;
  let closed = false;

  const displayedTime = () => {
    if (clockStart === null) return 0;
    const elapsed = mediaOrigin + audioClock.currentTime - clockStart;
    return Math.max(0, Math.min(duration, elapsed));
  };

  const clearVoices = ({ halt }) => {
    for (const voice of liveVoices.values()) {
      if (halt) {
        try {
          voice.source.stop();
        } catch {
          /* Web Audio rejects stop calls for some unstarted sources. */
        }
      }
      try {
        voice.source.disconnect();
        voice.level.disconnect();
      } catch {
        /* A prior teardown may already have removed the graph. */
      }
    }
    liveVoices.clear();
    lastVoice = null;
  };

  const complete = (reason) => {
    if (closed) return;
    closed = true;
    clearInterval(progressTimer);
    clearVoices({ halt: reason !== 'ended' });
    playback.release();
    try {
      Promise.resolve(audioClock.close()).catch(() => {});
    } catch {
      /* The device may have closed this context already. */
    }
    try {
      onDone?.(reason);
    } catch {
      /* Completion observers are isolated from playback cleanup. */
    }
  };

  const queueBlock = (blockIndex, { skip = 0, blend = true } = {}) => {
    const block = blocks[blockIndex];
    const audioBuffer = audioClock.createBuffer(1, block.samples.length, sampleRate);
    const channel = audioBuffer.getChannelData(0);
    if (typeof audioBuffer.copyToChannel === 'function')
      audioBuffer.copyToChannel(block.samples, 0);
    else channel.set(block.samples);

    const source = audioClock.createBufferSource();
    const level = audioClock.createGain();
    source.buffer = audioBuffer;
    source.connect(level);
    level.connect(audioClock.destination);

    const timelineTime = block.at + skip - mediaOrigin;
    const begins = Math.max(clockStart + timelineTime, audioClock.currentTime);
    if (blend && skip === 0 && lastVoice && lastVoice.until > begins) {
      const overlap = Math.min(block.fade, lastVoice.until - begins);
      if (overlap > 0) {
        const fadeEnds = begins + overlap;
        lastVoice.level.gain.setValueAtTime(1, begins);
        lastVoice.level.gain.linearRampToValueAtTime(0, fadeEnds);
        level.gain.setValueAtTime(0, begins);
        level.gain.linearRampToValueAtTime(1, fadeEnds);
      }
    }
    source.start(begins, skip);
    const voice = {
      source,
      level,
      blockIndex,
      until: begins + block.length - skip,
    };
    liveVoices.set(++voiceNumber, voice);
    lastVoice = voice;
  };

  const seek = (value) => {
    if (closed || blocks.length === 0) return;
    const destination = Math.max(0, Math.min(value, Math.max(0, duration - 0.02)));
    clearVoices({ halt: true });
    let containingBlock = 0;
    blocks.forEach((block, index) => {
      if (block.at <= destination) containingBlock = index;
    });
    mediaOrigin = destination;
    clockStart = audioClock.currentTime + restartDelay;
    queueBlock(containingBlock, {
      skip: destination - blocks[containingBlock].at,
      blend: false,
    });
    for (let index = containingBlock + 1; index < blocks.length; index += 1) {
      queueBlock(index);
    }
    playback.update({ currentTime: destination });
  };

  const pause = () => {
    if (closed) return;
    const at = displayedTime();
    try {
      Promise.resolve(audioClock.suspend()).catch(() => {});
    } catch {
      /* Pausing remains best effort on older webviews. */
    }
    playback.update({ paused: true, currentTime: at });
  };

  const resume = () => {
    if (closed) return;
    try {
      Promise.resolve(audioClock.resume()).catch(() => {});
    } catch {
      /* Resuming remains best effort on older webviews. */
    }
    playback.update({ paused: false, currentTime: displayedTime() });
  };

  const playback = claimTrackedPlayback({
    source: 'output',
    label,
    stop: () => complete('stopped'),
    seek,
    pause,
    resume,
  });

  const playbackEnd = () => {
    if (clockStart === null) return audioClock.currentTime;
    return clockStart + duration - mediaOrigin + drainAllowance;
  };

  const progressTimer = setInterval(() => {
    if (closed || audioClock.state !== 'running') return;
    playback.update({ currentTime: displayedTime() });
    if (inputComplete && audioClock.currentTime >= playbackEnd()) {
      playback.update({ currentTime: duration });
      complete('ended');
    }
  }, 250);

  const acceptSamples = (samples) => {
    if (closed || !samples?.length) return;
    const previous = blocks.at(-1);
    const length = samples.length / sampleRate;
    const fade = previous ? Math.min(maximumBlend, previous.length, length) : 0;
    const at = previous ? previous.at + previous.length - fade : 0;
    const block = { samples, length, fade, at };
    blocks.push(block);
    duration = at + length;

    if (clockStart === null) {
      mediaOrigin = 0;
      clockStart = audioClock.currentTime + initialDelay;
      queueBlock(0, { blend: false });
    } else {
      const due = clockStart + at - mediaOrigin;
      if (due < audioClock.currentTime - lateThreshold) {
        mediaOrigin = at;
        clockStart = audioClock.currentTime + restartDelay;
      }
      queueBlock(blocks.length - 1);
    }
    playback.update({
      duration,
      peaks: peaksFromChunkList(blocks.map((item) => item.samples)),
    });
  };

  return {
    appendPcm16Base64: (b64) => acceptSamples(decodePcm16Base64(b64)),
    appendPcm16Bytes: (bytes) => acceptSamples(pcm16BytesToFloat32(bytes)),
    finalize: ({ label: finalLabel } = {}) => {
      if (closed) return;
      inputComplete = true;
      const state = { duration };
      if (finalLabel !== undefined) state.label = finalLabel;
      playback.update(state);
    },
    fail: () => {
      if (!closed) complete('error');
    },
    get stopped() {
      return closed;
    },
  };
}

/**
 * Run one streaming generation end-to-end: POST /generate (stream=true),
 * progressively play the preview, and resolve with the "done" metadata
 * ({ id, audio_path, duration, gen_time, seed, sample_rate }) once the final
 * file is saved server-side (same watermark/history/retention pipeline as the
 * classic flow).
 *
 * @param {FormData} formData  The exact classic /generate form (not mutated).
 * @param {object}   opts
 * @param {AbortSignal} [opts.signal]
 * @param {string}   [opts.label]      Bar label while streaming.
 * @param {string}   [opts.finalLabel] Bar label once generation completes.
 * @param {(res: Response) => void} [opts.onHeaders]  Same X-Seed / routing
 *                    header handling as the classic path.
 * @param {(pct: number) => void}   [opts.onProgress] Chunk progress 0–100.
 * @throws {StreamingPreviewError} on any mid-stream failure (fall back to the
 *         classic flow); ApiError/AbortError propagate untouched.
 */
export async function streamGenerateSpeech(formData, opts = {}) {
  // Admission spans the whole stream, not merely response headers.
  return withTtsInflight(() => _streamGenerateSpeech(formData, opts));
}

async function _streamGenerateSpeech(
  formData,
  { signal, label, finalLabel, onHeaders, onProgress, onWarning } = {},
) {
  const fd = new FormData();
  for (const [k, v] of formData.entries()) fd.append(k, v);
  fd.append('stream', 'true');

  // Pre-stream failures (400/503/transport) throw ApiError here — identical
  // to the classic flow, so they are NOT wrapped for fallback.
  //
  // Routed through generateSpeech() rather than apiFetch() directly: this used
  // to be a second, parallel door onto POST /generate, so everything attached
  // to the "one chokepoint every synth shares" — the in-flight count and the
  // under-provisioned-hardware preflight — silently did not apply to streaming
  // synthesis (Greptile P1, #1288). One door, or it is not a chokepoint.
  const response = await generateSpeechWithinAdmission(fd, { signal });
  onHeaders?.(response);

  let player = null;
  let meta = null;
  try {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let totalChunks = 0;
    let received = 0;

    const handleEvent = (ev) => {
      if (ev.type === 'start') {
        totalChunks = ev.total_chunks || 0;
        player = createStreamingChunkPlayer({
          label,
          sampleRate: ev.sample_rate,
          crossfadeMs: ev.crossfade_ms || 0,
        });
      } else if (ev.type === 'chunk') {
        received += 1;
        player?.appendPcm16Base64(ev.pcm);
        if (totalChunks > 0)
          onProgress?.(Math.min(100, Math.round((received / totalChunks) * 100)));
      } else if (ev.type === 'warning') {
        // #1330: the render finished, but some of the text produced no audio.
        // Not an error — the take is real and playable — so it must not take
        // the stream down; it just must not be silent either.
        onWarning?.(ev);
      } else if (ev.type === 'done') {
        meta = ev;
      } else if (ev.type === 'error') {
        const detail =
          languageRejectionMessage(ev, i18n.t) ||
          generationFailureMessage(ev, i18n.t) ||
          ev.detail ||
          'TTS stream reported an error';
        const terminal =
          ev.terminal === true ||
          ['[clone_ref_unusable]', '[clone_ref_too_long]', '[clone_ref_no_speech]'].some((marker) =>
            detail.includes(marker),
          );
        throw new StreamingPreviewError(detail, {
          retryable: ev.retryable === true,
          retryAfter: ev.retry_after ?? null,
          terminal,
          errorClass: ev.error_class || null,
        });
      }
    };

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) handleEvent(JSON.parse(line));
      }
    }
    if (!meta) throw new StreamingPreviewError('TTS stream ended without a completion event');
    player?.finalize({ label: finalLabel });
    return meta;
  } catch (err) {
    try {
      player?.fail();
    } catch {
      /* teardown must not mask the original error */
    }
    if (err?.name === 'AbortError' || err instanceof StreamingPreviewError) throw err;
    // Anything else mid-stream (transport drop, JSON parse, Web Audio) → the
    // fallback signal.
    throw new StreamingPreviewError(err?.message || String(err), { cause: err });
  }
}
