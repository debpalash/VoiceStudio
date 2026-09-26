export const DEFAULT_PEAK_BUCKETS = 8192;

export function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

export function encodeWav(samples, sampleRate, channels = 1) {
  const channelCount = Math.max(1, Math.min(2, Number(channels) || 1));
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buf);
  const writeStr = (off, s) => {
    for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i));
  };
  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channelCount, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * channelCount * 2, true);
  view.setUint16(32, channelCount * 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = clamp(samples[i], -1, 1);
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}

export function computePeaksFromChannel(channel, buckets = DEFAULT_PEAK_BUCKETS) {
  const n = channel.length;
  const eff = Math.max(1, Math.min(buckets, n));
  const peaks = new Float32Array(eff * 2);
  const step = n / eff;
  for (let b = 0; b < eff; b++) {
    const s = Math.floor(b * step);
    const e = Math.min(n, Math.floor((b + 1) * step));
    let mn = 1,
      mx = -1;
    for (let i = s; i < e; i++) {
      const v = channel[i];
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    }
    if (mx < mn) {
      mn = 0;
      mx = 0;
    }
    peaks[b * 2] = mn;
    peaks[b * 2 + 1] = mx;
  }
  return peaks;
}

export function pickTickInterval(viewDur) {
  const candidates = [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600];
  const target = viewDur / 8;
  for (const c of candidates) if (c >= target) return c;
  return 1200;
}

export function xToTime(x, width, viewStart, viewEnd) {
  const clampedX = clamp(x, 0, width);
  return viewStart + (clampedX / Math.max(1, width)) * (viewEnd - viewStart);
}

export function pickHandle(clientX, canvasLeft, canvasWidth, state, edgeGrabPx = 10) {
  const x = clientX - canvasLeft;
  const { viewStart, viewEnd, start, end } = state;
  const viewDur = viewEnd - viewStart;
  if (viewDur <= 0) return null;
  const sx = ((start - viewStart) / viewDur) * canvasWidth;
  const ex = ((end - viewStart) / viewDur) * canvasWidth;
  if (Math.abs(x - sx) < edgeGrabPx) return 'start';
  if (Math.abs(x - ex) < edgeGrabPx) return 'end';
  if (x > sx && x < ex) return 'region';
  return null;
}

export function applyDrag(state, pointerClientX, canvasLeft, canvasWidth, drag, minGap = 0.02) {
  const { start, end, viewStart, viewEnd, duration } = state;
  const t = xToTime(pointerClientX - canvasLeft, canvasWidth, viewStart, viewEnd);
  const out = { start, end, viewStart, viewEnd };
  if (drag.mode === 'start') {
    out.start = clamp(t, 0, Math.max(0, end - minGap));
  } else if (drag.mode === 'end') {
    out.end = clamp(t, start + minGap, duration);
  } else if (drag.mode === 'region') {
    const len = drag.regionLen;
    const anchor = clamp(t - drag.offset, 0, Math.max(0, duration - len));
    out.start = anchor;
    out.end = anchor + len;
  } else if (drag.mode === 'new') {
    const a = clamp(drag.anchorT, 0, duration);
    const p = clamp(t, 0, duration);
    if (p >= a) {
      out.start = a;
      out.end = Math.max(p, a + minGap);
    } else {
      out.end = a;
      out.start = Math.min(p, a - minGap);
    }
    out.start = Math.max(0, out.start);
    out.end = Math.min(duration, out.end);
  } else if (drag.mode === 'pan') {
    const delta = pointerClientX - drag.startClientX;
    const viewDur = drag.viewDur;
    const timeDelta = -(delta / Math.max(1, canvasWidth)) * viewDur;
    const newVs = clamp(drag.viewStart + timeDelta, 0, Math.max(0, duration - viewDur));
    out.viewStart = newVs;
    out.viewEnd = newVs + viewDur;
  }
  return out;
}

export function zoomCenter(viewStart, viewEnd, duration, factor) {
  const viewDur = viewEnd - viewStart;
  const center = (viewStart + viewEnd) / 2;
  const newDur = clamp(viewDur * factor, 0.01, duration);
  const newVs = clamp(center - newDur / 2, 0, Math.max(0, duration - newDur));
  return { viewStart: newVs, viewEnd: newVs + newDur };
}

export function zoomAtCursor(viewStart, viewEnd, duration, factor, xFrac, minDur = 0.01) {
  const viewDur = viewEnd - viewStart;
  const anchor = viewStart + xFrac * viewDur;
  const newDur = clamp(viewDur * factor, minDur, duration);
  let newVs = anchor - xFrac * newDur;
  newVs = clamp(newVs, 0, Math.max(0, duration - newDur));
  return { viewStart: newVs, viewEnd: newVs + newDur };
}

export async function computePeaksAsync(channel, buckets = DEFAULT_PEAK_BUCKETS, onProgress) {
  const n = channel.length;
  const eff = Math.max(1, Math.min(buckets, n));
  const peaks = new Float32Array(eff * 2);
  const step = n / eff;
  const YIELD_EVERY = 64; // buckets per chunk
  for (let b = 0; b < eff; b++) {
    const s = Math.floor(b * step);
    const e = Math.min(n, Math.floor((b + 1) * step));
    let mn = 1,
      mx = -1;
    for (let i = s; i < e; i++) {
      const v = channel[i];
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    }
    if (mx < mn) {
      mn = 0;
      mx = 0;
    }
    peaks[b * 2] = mn;
    peaks[b * 2 + 1] = mx;
    if ((b & (YIELD_EVERY - 1)) === 0) {
      if (onProgress) onProgress(b / eff);
      // Yield to event loop so UI stays responsive.
      await new Promise((r) => setTimeout(r, 0));
    }
  }
  if (onProgress) onProgress(1);
  return peaks;
}

async function probeDuration(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const a = new Audio();
    a.preload = 'metadata';
    const cleanup = () => {
      try {
        URL.revokeObjectURL(url);
      } catch {}
    };
    a.addEventListener(
      'loadedmetadata',
      () => {
        const d = a.duration;
        cleanup();
        resolve(isFinite(d) ? d : 0);
      },
      { once: true },
    );
    a.addEventListener(
      'error',
      () => {
        cleanup();
        reject(new Error('metadata failed'));
      },
      { once: true },
    );
    a.src = url;
  });
}

export async function decodeToMonoLowRate(file, targetSR = 22050) {
  const duration = await probeDuration(file);
  const arr = await file.arrayBuffer();
  const len = Math.max(1, Math.ceil(Math.max(0.001, duration) * targetSR));
  const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  const offline = new Offline(1, len, targetSR);
  const buf = await offline.decodeAudioData(arr);
  return buf;
}

// Playhead position for a selection [startSec, endSec] that has been playing
// for `elapsedSec`, measured on the SAME decoded-buffer timeline the waveform
// and the exported slice use. Kept pure so the preview and the export can never
// drift onto different timelines again (#1210). Looping wraps within the
// selection; non-looping clamps at the selection end.
export function selectionPlayhead(startSec, endSec, elapsedSec, loop) {
  const seg = Math.max(1e-6, endSec - startSec);
  const e = Math.max(0, elapsedSec);
  if (loop) return startSec + (e % seg);
  return Math.min(endSec, startSec + e);
}

// Loop window for a BufferSource previewing selection [startSec, endSec] of a
// `durationSec`-long buffer. A plain canvas click anchors a fresh selection with
// start === end; if a loop source is started with loopStart === loopEnd (or an
// inverted range), the Web Audio node IGNORES the loop points and loops the
// WHOLE buffer — the preview≠selection failure #1210 exists to prevent. Floor
// the segment at MIN_LOOP_SEC so loopStart < loopEnd always holds, and clamp the
// window into the buffer. Pure so the guarantee is unit-tested, not eyeballed.
export const MIN_LOOP_SEC = 0.01;
export function loopWindow(startSec, endSec, durationSec) {
  const start = clamp(startSec, 0, Math.max(0, durationSec));
  const seg = Math.max(MIN_LOOP_SEC, endSec - start);
  const loopEnd = Math.min(durationSec, start + seg);
  return { loopStart: start, loopEnd, seg: loopEnd - start };
}

export function sliceToMono(buffer, startSec, endSec) {
  const sr = buffer.sampleRate;
  const s0 = Math.floor(startSec * sr);
  const s1 = Math.floor(endSec * sr);
  const len = Math.max(0, s1 - s0);
  const chCount = buffer.numberOfChannels;
  const out = new Float32Array(len);
  for (let c = 0; c < chCount; c++) {
    const d = buffer.getChannelData(c);
    for (let i = 0; i < len; i++) out[i] += d[s0 + i] / chCount;
  }
  return out;
}
