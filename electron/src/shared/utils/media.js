/**
 * Media utilities shared across the app.
 *
 * Extracted from App.jsx to reduce file size and enable independent testing.
 */
import { API_BASE as _PREVIEW_API, isTauriContext } from './apiBase';
import { claimTrackedPlayback } from './playback';
import { apiFetch } from '../api/client';

const isTauri = isTauriContext();

// ── Tauri window maximise on double-click ─────────────────────────────
let tauriWindow = null;
if (isTauri) {
  import('@tauri-apps/api/window').then((m) => {
    tauriWindow = m;
  });
}
export const doubleClickMaximize = () => {
  if (tauriWindow) tauriWindow.getCurrentWindow().toggleMaximize();
};

// ── File → media URL ──────────────────────────────────────────────────
// _PREVIEW_API is now sourced from utils/apiBase.ts so Docker LAN users
// (issue #80) get window.location.hostname:3900 instead of localhost:3900.

/**
 * Convert a File object to a media-safe URL.
 * In Tauri's WebKit, blob: URLs fail for <video>/<audio> elements.
 * We upload to the backend's /preview endpoint and serve via HTTP instead.
 * Falls back to createObjectURL for regular browsers.
 */
export const fileToMediaUrl = async (file, prevUrls) => {
  // Revoke previous blob URLs if they exist
  if (prevUrls?.videoUrl?.startsWith('blob:')) URL.revokeObjectURL(prevUrls.videoUrl);
  if (prevUrls?.audioUrl?.startsWith('blob:')) URL.revokeObjectURL(prevUrls.audioUrl);

  if (isTauri) {
    try {
      const form = new FormData();
      form.append('video', file, file.name || 'media.wav');
      const res = await apiFetch(`${_PREVIEW_API}/preview/upload`, { method: 'POST', body: form });
      const data = await res.json();
      return {
        videoUrl: `${_PREVIEW_API}${data.url}`,
        audioUrl: data.audioUrl ? `${_PREVIEW_API}${data.audioUrl}` : `${_PREVIEW_API}${data.url}`,
      };
    } catch (e) {
      console.warn('Preview upload failed, falling back to blob URL:', e);
    }
  }
  const url = URL.createObjectURL(file);
  return { videoUrl: url, audioUrl: url };
};

// ── Blob audio playback ───────────────────────────────────────────────

/**
 * Downsample a decoded AudioBuffer into normalized [0..1] waveform peaks for
 * the global mini-player. Pure math — exported for unit tests.
 */
export const computePeaks = (audioBuffer, buckets = 240) => {
  try {
    const length = audioBuffer?.length || 0;
    if (!length) return null;
    const channels = Math.min(audioBuffer.numberOfChannels || 1, 2);
    const n = Math.min(buckets, length);
    const peaks = Array.from({ length: n }, () => 0);
    const bucketSize = length / n;
    // Stride within large buckets: peak *shape* is all the UI needs, and a
    // capped sample count keeps this O(buckets) even for hour-long renders.
    const step = Math.max(1, Math.floor(bucketSize / 64));
    for (let c = 0; c < channels; c++) {
      const data = audioBuffer.getChannelData(c);
      for (let i = 0; i < n; i++) {
        const start = Math.floor(i * bucketSize);
        const end = Math.min(length, Math.floor((i + 1) * bucketSize) || start + 1);
        let max = 0;
        for (let j = start; j < end; j += step) {
          const v = Math.abs(data[j]);
          if (v > max) max = v;
        }
        if (max > peaks[i]) peaks[i] = max;
      }
    }
    const top = Math.max(...peaks, 0.001);
    return peaks.map((p) => p / top);
  } catch {
    return null;
  }
};

// Best-effort peaks from an un-decoded blob (browser path). Uses an
// OfflineAudioContext so no audible/running context is spent on it. Peaks are
// a progressive enhancement — any failure just means the mini-player shows a
// plain progress bar.
const decodePeaksFromBlob = async (blob) => {
  try {
    const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!Offline) return null;
    const octx = new Offline(1, 1, 44100);
    const decoded = await octx.decodeAudioData(await blob.arrayBuffer());
    return computePeaks(decoded);
  } catch {
    return null;
  }
};

/**
 * Wire an HTMLAudioElement into the global playback manager as a tracked
 * 'output' playback: label + timeupdate-driven currentTime/duration + real
 * seek/pause/resume for the GlobalAudioPlayer bar.
 *
 * `onDone(reason)` fires exactly once when the audio leaves the bar:
 *   'ended'   — finished naturally
 *   'stopped' — halted via the manager (bar's stop button / another claim)
 *   'error'   — play() failed
 * `cleanup` runs on every exit path (revoke object URLs etc.).
 */
const playTrackedAudioElement = (a, { label, peaksBlob, cleanup, onDone } = {}) => {
  let finished = false;
  const finish = (reason) => {
    if (finished) return;
    finished = true;
    try {
      cleanup?.();
    } catch {
      /* cleanup must not break playback teardown */
    }
    try {
      onDone?.(reason);
    } catch {
      /* consumer callbacks must not break the manager */
    }
  };
  const session = claimTrackedPlayback({
    source: 'output',
    label,
    stop: () => {
      try {
        a.pause();
      } catch {
        /* already stopped */
      }
      finish('stopped');
    },
    seek: (t) => {
      try {
        const d = Number.isFinite(a.duration) ? a.duration : Infinity;
        a.currentTime = Math.max(0, Math.min(t, d));
      } catch {
        /* not seekable yet */
      }
    },
    pause: () => {
      try {
        a.pause();
      } catch {
        /* noop */
      }
    },
    resume: () => {
      a.play().catch(() => {});
    },
  });
  const pushTime = () =>
    session.update({
      currentTime: a.currentTime || 0,
      duration: Number.isFinite(a.duration) ? a.duration : 0,
    });
  // Optional chaining: unit-test doubles of Audio() may omit the event API.
  a.addEventListener?.('timeupdate', pushTime);
  a.addEventListener?.('durationchange', pushTime);
  a.addEventListener?.('loadedmetadata', pushTime);
  a.addEventListener?.('play', () => session.update({ paused: false }));
  a.addEventListener?.('pause', () => session.update({ paused: true }));
  a.addEventListener?.('ended', () => {
    session.release();
    finish('ended');
  });
  if (peaksBlob) {
    decodePeaksFromBlob(peaksBlob).then((peaks) => {
      if (peaks) session.update({ peaks });
    });
  }
  return { session, finish };
};

/**
 * Play a decoded AudioBuffer through a fresh AudioContext (Tauri path — blob
 * URLs don't play in WebKit media elements) as a tracked 'output' playback.
 * Seek re-creates the buffer source at the target offset; pause/resume map to
 * ctx.suspend()/resume() (ctx.currentTime freezes while suspended, so the
 * elapsed-time math stays correct across pauses).
 */
const playTrackedBufferSource = (ctx, decoded, { label, onDone } = {}) => {
  const duration = decoded.duration;
  let srcNode = null;
  let offset = 0;
  let startedAt = 0;
  let finished = false;
  let timer = null;

  const currentPos = () => Math.min(offset + (ctx.currentTime - startedAt), duration);

  const finish = (reason) => {
    if (finished) return;
    finished = true;
    if (timer) clearInterval(timer);
    if (srcNode) srcNode.onended = null;
    try {
      srcNode?.stop();
    } catch {
      /* already stopped */
    }
    try {
      ctx.close();
    } catch {
      /* already closed */
    }
    try {
      onDone?.(reason);
    } catch {
      /* consumer callbacks must not break the manager */
    }
  };

  const session = claimTrackedPlayback({
    source: 'output',
    label,
    stop: () => finish('stopped'),
    seek: (t) => {
      if (finished) return;
      startSource(Math.max(0, Math.min(t, Math.max(0, duration - 0.01))));
      session.update({ currentTime: offset });
    },
    pause: () => {
      if (finished) return;
      ctx
        .suspend()
        .then(() => session.update({ paused: true, currentTime: currentPos() }))
        .catch(() => {});
    },
    resume: () => {
      if (finished) return;
      ctx
        .resume()
        .then(() => session.update({ paused: false }))
        .catch(() => {});
    },
  });

  const startSource = (at) => {
    const prev = srcNode;
    if (prev) {
      prev.onended = null; // superseded by seek — its end must not finish us
      try {
        prev.stop();
      } catch {
        /* already stopped */
      }
      try {
        prev.disconnect();
      } catch {
        /* noop */
      }
    }
    const s = ctx.createBufferSource();
    s.buffer = decoded;
    s.connect(ctx.destination);
    offset = at;
    startedAt = ctx.currentTime;
    s.onended = () => {
      if (srcNode !== s || finished) return;
      session.update({ currentTime: duration });
      session.release();
      finish('ended');
    };
    srcNode = s;
    s.start(0, at);
  };

  startSource(0);
  // The buffer is already decoded — peaks come for free, no second decode.
  session.update({ duration, currentTime: 0, peaks: computePeaks(decoded) });
  timer = setInterval(() => {
    if (!finished && ctx.state === 'running') session.update({ currentTime: currentPos() });
  }, 250);
};

/**
 * Play audio from a Blob. Uses Web Audio API in Tauri (blob URLs blocked)
 * and standard Audio() elsewhere.
 *
 * Registered with the global playback manager (issue #316): starting any
 * other preview stops this one, and `stopActivePlayback()` halts it — the
 * old fire-and-forget version could neither be stopped nor de-overlapped.
 *
 * Every path registers as a *tracked* 'output' playback so the persistent
 * GlobalAudioPlayer bar gets a label, waveform peaks (decoded once from the
 * blob we already hold — never refetched), live time, and seek/pause.
 *
 * @param {Blob} blob
 * @param {object} [meta]
 * @param {string} [meta.label]   User-facing "what is playing" text.
 * @param {(reason: 'ended'|'stopped'|'error') => void} [meta.onDone]
 *        Fires exactly once when playback leaves the global player —
 *        callers chain sequences ('ended'/'error' → next) or reset their
 *        per-item playing state ('stopped').
 */
export const playBlobAudio = async (blob, meta = {}) => {
  if (isTauri) {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    // WebKit suspends AudioContext by default — must resume before decoding
    if (ctx.state === 'suspended') await ctx.resume();
    try {
      const buf = await blob.arrayBuffer();
      const decoded = await ctx.decodeAudioData(buf);
      playTrackedBufferSource(ctx, decoded, meta);
    } catch (e) {
      // Expected & recovered on WebView2 (Windows): decodeAudioData decodes the
      // WHOLE file into one PCM AudioBuffer and chokes on long-form audiobook/
      // story renders (.m4b / AAC) — a `warn`, not a red ERROR, since the
      // streaming fallback below recovers it. (The scary "decode error" line
      // users saw in Logs → Frontend was this expected branch, logged at error
      // level even when playback succeeded.)
      console.warn(
        'playBlobAudio: Web Audio decode failed, falling back to streamed playback:',
        e?.message || e,
      );
      ctx.close();
      // Fallback (#653): a blob: URL won't play in an <audio> element under
      // Tauri's WebKit (see fileToMediaUrl above), so upload to the backend
      // preview endpoint (ffmpeg-extracts a streamable WAV) and play the HTTP
      // URL — the same path video previews already use. Streams; no whole-file
      // decode. NOTE: _PREVIEW_API must be 127.0.0.1 (not localhost) or this
      // fetch misses the IPv4 backend on Windows (see utils/apiBase.ts).
      // (Peaks skipped here on purpose: this blob just failed to decode.)
      try {
        const form = new FormData();
        form.append('video', blob, 'preview.audio');
        const res = await apiFetch(`${_PREVIEW_API}/preview/upload`, {
          method: 'POST',
          body: form,
        });
        const data = await res.json();
        const url = `${_PREVIEW_API}${data.audioUrl || data.url}`;
        const a = new Audio(url);
        const { session, finish } = playTrackedAudioElement(a, {
          label: meta.label,
          onDone: meta.onDone,
        });
        await a.play().catch((err) => {
          session.release();
          finish('error'); // single onDone — the outer catch never re-fires it
          console.error('playBlobAudio: streamed fallback play failed:', err?.message || err);
        });
      } catch (e2) {
        // Real failure — both decode AND the streamed-fallback upload failed
        // (before an element existed, so onDone hasn't fired yet).
        console.error('playBlobAudio: streamed fallback also failed:', e2?.message || e2);
        meta.onDone?.('error');
      }
    }
  } else {
    const url = URL.createObjectURL(blob);
    const a = new Audio(url);
    const { session, finish } = playTrackedAudioElement(a, {
      label: meta.label,
      peaksBlob: blob,
      cleanup: () => URL.revokeObjectURL(url),
      onDone: meta.onDone,
    });
    a.play().catch((e) => {
      session.release();
      finish('error');
      console.error('playBlobAudio play error:', e);
    });
  }
};

// ── Notification ping ─────────────────────────────────────────────────

let _pingCtx = null;
export const playPing = () => {
  try {
    if (!_pingCtx) _pingCtx = new (window.AudioContext || window.webkitAudioContext)();
    const ctx = _pingCtx;
    if (ctx.state === 'suspended') ctx.resume();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = 'sine';
    osc.frequency.setValueAtTime(600, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(900, ctx.currentTime + 0.08);
    osc.frequency.exponentialRampToValueAtTime(1200, ctx.currentTime + 0.15);
    gain.gain.setValueAtTime(0, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(0.18, ctx.currentTime + 0.03);
    gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.25);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.25);
  } catch (e) {}
};

// Re-export for convenience
export { isTauri };
