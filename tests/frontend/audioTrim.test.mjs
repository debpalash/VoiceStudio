import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  clamp, encodeWav, computePeaksFromChannel, pickTickInterval,
  xToTime, pickHandle, applyDrag, zoomAtCursor, zoomCenter, sliceToMono,
} from '../../electron/src/shared/utils/audioTrim.js';

test('clamp', () => {
  assert.equal(clamp(5, 0, 10), 5);
  assert.equal(clamp(-1, 0, 10), 0);
  assert.equal(clamp(11, 0, 10), 10);
});

test('encodeWav writes valid RIFF/WAVE header', () => {
  const samples = new Float32Array([0, 0.5, -0.5, 1, -1]);
  const buf = encodeWav(samples, 48000);
  const view = new DataView(buf);
  const tag = (off) => String.fromCharCode(view.getUint8(off), view.getUint8(off+1), view.getUint8(off+2), view.getUint8(off+3));
  assert.equal(tag(0), 'RIFF');
  assert.equal(tag(8), 'WAVE');
  assert.equal(tag(12), 'fmt ');
  assert.equal(view.getUint32(16, true), 16, 'fmt chunk size');
  assert.equal(view.getUint16(20, true), 1, 'PCM');
  assert.equal(view.getUint16(22, true), 1, 'mono');
  assert.equal(view.getUint32(24, true), 48000);
  assert.equal(view.getUint16(34, true), 16, 'bit depth');
  assert.equal(tag(36), 'data');
  assert.equal(view.getUint32(40, true), samples.length * 2);
  assert.equal(buf.byteLength, 44 + samples.length * 2);
  // Peak samples
  assert.equal(view.getInt16(44 + 3 * 2, true), 0x7fff, 'peak positive sample');
  assert.equal(view.getInt16(44 + 4 * 2, true), -0x8000, 'peak negative sample');
});

test('encodeWav clamps out-of-range samples', () => {
  const samples = new Float32Array([2, -2]);
  const buf = encodeWav(samples, 44100);
  const view = new DataView(buf);
  assert.equal(view.getInt16(44, true), 0x7fff);
  assert.equal(view.getInt16(46, true), -0x8000);
});

test('computePeaksFromChannel produces min/max per bucket', () => {
  const ch = new Float32Array(1000);
  for (let i = 0; i < 1000; i++) ch[i] = Math.sin(i * 0.1);
  const peaks = computePeaksFromChannel(ch, 10);
  assert.equal(peaks.length, 20);
  for (let b = 0; b < 10; b++) {
    const mn = peaks[b * 2];
    const mx = peaks[b * 2 + 1];
    assert.ok(mn <= mx, `bucket ${b} min<=max`);
    assert.ok(mn >= -1 && mx <= 1);
  }
});

test('computePeaksFromChannel with empty bucket range stays 0/0', () => {
  const ch = new Float32Array(5); // shorter than buckets
  ch.set([0.2, -0.3, 0.9, -0.8, 0.1]);
  const peaks = computePeaksFromChannel(ch, 100);
  // implementation caps to ch.length buckets = 5
  assert.equal(peaks.length, 5 * 2);
});

test('pickTickInterval picks sane steps', () => {
  assert.equal(pickTickInterval(1), 0.25);     // target ~0.125 → 0.25
  assert.equal(pickTickInterval(10), 2);       // target 1.25 → 2
  assert.equal(pickTickInterval(1700), 300);   // target 212.5 → 300
  assert.equal(pickTickInterval(0.1), 0.05);   // target 0.0125 → 0.05
  assert.equal(pickTickInterval(20000), 1200); // fallback
});

test('xToTime maps canvas x to time linearly', () => {
  assert.equal(xToTime(0, 100, 10, 20), 10);
  assert.equal(xToTime(100, 100, 10, 20), 20);
  assert.equal(xToTime(50, 100, 10, 20), 15);
  assert.equal(xToTime(-5, 100, 10, 20), 10, 'clamps x<0');
  assert.equal(xToTime(999, 100, 10, 20), 20, 'clamps x>width');
});

test('pickHandle classifies near-edge vs inside vs outside', () => {
  const state = { viewStart: 0, viewEnd: 100, start: 20, end: 40 };
  // canvas 1000px wide, state→ sx=200, ex=400
  assert.equal(pickHandle(205, 0, 1000, state, 10), 'start');
  assert.equal(pickHandle(395, 0, 1000, state, 10), 'end');
  assert.equal(pickHandle(300, 0, 1000, state, 10), 'region');
  assert.equal(pickHandle(50, 0, 1000, state, 10), null);
  assert.equal(pickHandle(600, 0, 1000, state, 10), null);
});

test('applyDrag start handle respects minGap', () => {
  const state = { start: 1, end: 5, viewStart: 0, viewEnd: 10, duration: 10 };
  const out = applyDrag(state, 800, 0, 1000, { mode: 'start' }, 0.1);
  // pointerX 800 → t = 8
  // clamp(8, 0, end-0.1=4.9) = 4.9
  assert.equal(out.start.toFixed(2), '4.90');
  assert.equal(out.end, 5);
});

test('applyDrag end handle respects minGap and duration', () => {
  const state = { start: 1, end: 5, viewStart: 0, viewEnd: 10, duration: 10 };
  const out = applyDrag(state, 100, 0, 1000, { mode: 'end' }, 0.1);
  // t=1, clamp(1, start+0.1=1.1, 10)=1.1
  assert.equal(out.end.toFixed(2), '1.10');
  const out2 = applyDrag(state, 2000, 0, 1000, { mode: 'end' }, 0.1);
  assert.equal(out2.end, 10);
});

test('applyDrag region preserves length and clamps', () => {
  const state = { start: 2, end: 4, viewStart: 0, viewEnd: 10, duration: 10 };
  // Region length 2, offset at t=3 (middle), user drags to pointer x=900 → t=9
  const drag = { mode: 'region', regionLen: 2, offset: 1 };
  const out = applyDrag(state, 900, 0, 1000, drag);
  // anchor = 9-1 = 8; 8+2 = 10 ok
  assert.equal(out.start.toFixed(2), '8.00');
  assert.equal(out.end.toFixed(2), '10.00');
  // drag past end — clamps to duration-len
  const out2 = applyDrag(state, 1100, 0, 1000, drag);
  assert.equal(out2.start, 8);
  assert.equal(out2.end, 10);
  // drag before 0
  const out3 = applyDrag(state, -500, 0, 1000, drag);
  assert.equal(out3.start, 0);
  assert.equal(out3.end, 2);
});

test('applyDrag pan clamps within duration', () => {
  const state = { start: 0, end: 1, viewStart: 10, viewEnd: 20, duration: 100 };
  const drag = { mode: 'pan', startClientX: 500, viewStart: 10, viewDur: 10 };
  // Pointer moves left by 100 px: delta = -100, timeDelta = +1 (view shifts right)
  const out = applyDrag(state, 400, 0, 1000, drag);
  assert.equal(out.viewStart, 11);
  assert.equal(out.viewEnd, 21);
  // Pan to very far right — clamp
  const out2 = applyDrag(state, -10000, 0, 1000, drag);
  assert.equal(out2.viewStart, 90, 'pan clamped at duration-viewDur');
  assert.equal(out2.viewEnd, 100);
});

test('zoomAtCursor keeps anchor time under xFrac', () => {
  // view 0..100, cursor at 20% → anchor t=20
  const out = zoomAtCursor(0, 100, 1000, 0.5, 0.2);
  // newDur=50, newVs = 20 - 0.2*50 = 10
  assert.equal(out.viewStart, 10);
  assert.equal(out.viewEnd, 60);
});

test('zoomAtCursor clamps to duration', () => {
  const out = zoomAtCursor(0, 100, 1000, 0.0001, 0.5);
  assert.ok(out.viewEnd - out.viewStart >= 0.01 - 1e-9);
});

test('zoomCenter halves duration', () => {
  const out = zoomCenter(0, 100, 1000, 0.5);
  assert.equal(out.viewStart, 25);
  assert.equal(out.viewEnd, 75);
});

test('sliceToMono mixes channels and slices sample window', () => {
  const sr = 100;
  const L = new Float32Array(sr * 3); // 3 s
  const R = new Float32Array(sr * 3);
  for (let i = 0; i < L.length; i++) { L[i] = 0.4; R[i] = 0.6; }
  const buffer = {
    sampleRate: sr, numberOfChannels: 2,
    getChannelData: (c) => (c === 0 ? L : R),
  };
  const slice = sliceToMono(buffer, 1, 2); // 1 second
  assert.equal(slice.length, sr);
  // mean per sample = (0.4 + 0.6) / 2 = 0.5
  for (let i = 0; i < slice.length; i++) {
    assert.ok(Math.abs(slice[i] - 0.5) < 1e-6);
  }
});

test('sliceToMono handles mono buffer', () => {
  const sr = 48000;
  const L = new Float32Array(sr);
  L.fill(0.1);
  const buffer = { sampleRate: sr, numberOfChannels: 1, getChannelData: () => L };
  const slice = sliceToMono(buffer, 0.25, 0.5);
  assert.equal(slice.length, Math.floor(0.5 * sr) - Math.floor(0.25 * sr));
  assert.ok(Math.abs(slice[0] - 0.1) < 1e-6);
});

test('applyDrag start cannot go below 0', () => {
  const state = { start: 0.5, end: 2, viewStart: 0, viewEnd: 10, duration: 10 };
  const out = applyDrag(state, -100, 0, 1000, { mode: 'start' }, 0.1);
  assert.equal(out.start, 0);
});

test('applyDrag new-selection drags rightward', () => {
  const state = { start: 2, end: 4, viewStart: 0, viewEnd: 10, duration: 10 };
  const drag = { mode: 'new', anchorT: 3 };
  // pointer at x=500 → t=5 → forward drag
  const out = applyDrag(state, 500, 0, 1000, drag, 0.1);
  assert.equal(out.start, 3);
  assert.equal(out.end, 5);
});

test('applyDrag new-selection drags leftward and swaps', () => {
  const state = { start: 2, end: 4, viewStart: 0, viewEnd: 10, duration: 10 };
  const drag = { mode: 'new', anchorT: 5 };
  // pointer at x=200 → t=2 (< anchor) → reversed
  const out = applyDrag(state, 200, 0, 1000, drag, 0.1);
  assert.equal(out.start, 2);
  assert.equal(out.end, 5);
});

test('applyDrag new-selection collapsed respects minGap', () => {
  const state = { start: 0, end: 0, viewStart: 0, viewEnd: 10, duration: 10 };
  const drag = { mode: 'new', anchorT: 3 };
  // same spot as anchor — pointer at x=300 → t=3
  const out = applyDrag(state, 300, 0, 1000, drag, 0.1);
  assert.ok(out.end - out.start >= 0.1 - 1e-9, 'minGap enforced');
});

test('applyDrag region length 0 (edge case)', () => {
  const state = { start: 5, end: 5, viewStart: 0, viewEnd: 10, duration: 10 };
  const drag = { mode: 'region', regionLen: 0, offset: 0 };
  const out = applyDrag(state, 500, 0, 1000, drag);
  assert.equal(out.start, 5);
  assert.equal(out.end, 5);
});
