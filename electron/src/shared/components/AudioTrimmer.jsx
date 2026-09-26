import React, { useEffect, useRef, useState, useCallback } from 'react';
import { Scissors, Play, Pause, Check, ZoomIn, ZoomOut, Maximize2, Repeat } from 'lucide-react';
import {
  clamp,
  encodeWav,
  computePeaksFromChannel,
  computePeaksAsync,
  pickTickInterval,
  xToTime as xToTimeUtil,
  pickHandle as pickHandleUtil,
  applyDrag as applyDragUtil,
  zoomAtCursor,
  zoomCenter,
  sliceToMono,
  selectionPlayhead,
  loopWindow,
  decodeToMonoLowRate,
  DEFAULT_PEAK_BUCKETS,
} from '../utils/audioTrim.js';
import { Dialog, Button } from '../ui';
import { useTranslation } from 'react-i18next';

const EDGE_GRAB_PX = 10;

function fmtSec(t, precision = 2) {
  if (!isFinite(t)) return '0.00s';
  return `${t.toFixed(precision)}s`;
}

function fmtHMS(t) {
  if (!isFinite(t)) return '0:00.00';
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${s.toFixed(2).padStart(5, '0')}`;
  return `${m}:${s.toFixed(2).padStart(5, '0')}`;
}

export default function AudioTrimmer({ file, maxSeconds = 15, onConfirm, onCancel }) {
  const { t } = useTranslation();
  const waveRef = useRef(null);
  const rulerRef = useRef(null);
  const audioCtxRef = useRef(null);
  const sourceRef = useRef(null);
  const playClockRef = useRef(null);
  const playRafRef = useRef(0);
  const loopRef = useRef(true);
  const containerRef = useRef(null);
  const bufferRef = useRef(null);
  const peaksRef = useRef(null);
  const drawRafRef = useRef(0);
  const dragRafRef = useRef(0);
  const dragStateRef = useRef(null);
  const pointerRef = useRef(null);
  const stateRef = useRef({ start: 0, end: 0, cursor: 0, viewStart: 0, viewEnd: 0, duration: 0 });

  const [ready, setReady] = useState(false);
  const [decoding, setDecoding] = useState(true);
  const [peakProgress, setPeakProgress] = useState(0);
  const [error, setError] = useState('');
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(0);
  const [cursor, setCursor] = useState(0);
  const [viewStart, setViewStart] = useState(0);
  const [viewEnd, setViewEnd] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(true);
  const [startInput, setStartInput] = useState('0.00');
  const [endInput, setEndInput] = useState('0.00');

  const [audioMeta, setAudioMeta] = useState(null);

  useEffect(() => {
    stateRef.current = {
      start,
      end,
      cursor,
      viewStart,
      viewEnd,
      duration: bufferRef.current ? bufferRef.current.duration : 0,
    };
  }, [start, end, cursor, viewStart, viewEnd]);

  useEffect(() => {
    setStartInput(start.toFixed(2));
  }, [start]);
  useEffect(() => {
    setEndInput(end.toFixed(2));
  }, [end]);

  // Decode (low rate mono) + async peaks — keeps UI responsive for long files.
  useEffect(() => {
    if (!file) return;
    let cancelled = false;
    setDecoding(true);
    setReady(false);
    setPeakProgress(0);
    (async () => {
      try {
        const buf = await decodeToMonoLowRate(file, 22050);
        if (cancelled) return;
        bufferRef.current = buf;
        setAudioMeta({ duration: buf.duration, sampleRate: buf.sampleRate });
        // Prime with a coarse synchronous pass so waveform shows something instantly.
        peaksRef.current = computePeaksFromChannel(buf.getChannelData(0), 1024);
        setViewEnd(buf.duration);
        setEnd(Math.min(buf.duration, maxSeconds));
        setStart(0);
        setCursor(0);
        setViewStart(0);
        setDecoding(false);
        setReady(true);
        // Refine peaks asynchronously without blocking UI.
        const refined = await computePeaksAsync(
          buf.getChannelData(0),
          DEFAULT_PEAK_BUCKETS,
          (p) => {
            if (!cancelled) setPeakProgress(p);
          },
        );
        if (cancelled) return;
        peaksRef.current = refined;
        setPeakProgress(1);
      } catch (e) {
        if (!cancelled) setError(t('trimmer.decode_failed', { message: e.message || e }));
        setDecoding(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [file, maxSeconds]);

  // Keep the latest loop preference readable from the rAF playhead loop and the
  // live-bounds effect without re-subscribing them.
  useEffect(() => {
    loopRef.current = loop;
  }, [loop]);

  const sizeCanvas = useCallback((canvas) => {
    if (!canvas) return null;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(1, Math.floor(rect.width * dpr));
    const h = Math.max(1, Math.floor(rect.height * dpr));
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;
    return { w, h, dpr };
  }, []);

  const drawWave = useCallback(() => {
    const buffer = bufferRef.current;
    const peaks = peaksRef.current;
    const canvas = waveRef.current;
    if (!buffer || !peaks || !canvas) return;
    const sized = sizeCanvas(canvas);
    if (!sized) return;
    const { w, h, dpr } = sized;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, w, h);

    const {
      viewStart: vs,
      viewEnd: ve,
      start: s,
      end: e,
      cursor: c,
      duration: dur,
    } = stateRef.current;
    const viewDur = Math.max(1e-6, ve - vs);
    const totalBuckets = peaks.length / 2;
    const secPerBucket = dur / totalBuckets;
    const secPerPixel = viewDur / w;
    const useRaw = secPerPixel < secPerBucket;
    const ch = buffer.getChannelData(0);
    const sr = buffer.sampleRate;

    // Center baseline
    ctx.strokeStyle = 'rgba(255,255,255,0.05)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();

    // Waveform
    ctx.fillStyle = '#7a6f5d';
    for (let x = 0; x < w; x++) {
      const t0 = vs + (x / w) * viewDur;
      const t1 = vs + ((x + 1) / w) * viewDur;
      let mn = 1,
        mx = -1;
      if (useRaw) {
        const i0 = Math.max(0, Math.floor(t0 * sr));
        const i1 = Math.min(ch.length, Math.ceil(t1 * sr));
        for (let i = i0; i < i1; i++) {
          const v = ch[i];
          if (v < mn) mn = v;
          if (v > mx) mx = v;
        }
      } else {
        const b0 = Math.max(0, Math.floor(t0 / secPerBucket));
        const b1 = Math.min(totalBuckets, Math.ceil(t1 / secPerBucket));
        for (let b = b0; b < b1; b++) {
          const pmn = peaks[b * 2];
          const pmx = peaks[b * 2 + 1];
          if (pmn < mn) mn = pmn;
          if (pmx > mx) mx = pmx;
        }
      }
      if (mx < mn) {
        mn = 0;
        mx = 0;
      }
      const y1 = (1 - mx) * 0.5 * h;
      const y2 = (1 - mn) * 0.5 * h;
      ctx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
    }

    // Selection
    const tToX = (t) => ((t - vs) / viewDur) * w;
    const sx = tToX(s);
    const ex = tToX(e);
    const selW = ex - sx;
    ctx.fillStyle = 'rgba(211,134,155,0.18)';
    ctx.fillRect(sx, 0, selW, h);

    // Redraw selection waveform in accent
    ctx.save();
    ctx.beginPath();
    ctx.rect(Math.max(0, sx), 0, Math.max(0, Math.min(w, ex) - Math.max(0, sx)), h);
    ctx.clip();
    ctx.fillStyle = '#d3869b';
    for (let x = Math.max(0, Math.floor(sx)); x < Math.min(w, Math.ceil(ex)); x++) {
      const t0 = vs + (x / w) * viewDur;
      const t1 = vs + ((x + 1) / w) * viewDur;
      let mn = 1,
        mx = -1;
      if (useRaw) {
        const i0 = Math.max(0, Math.floor(t0 * sr));
        const i1 = Math.min(ch.length, Math.ceil(t1 * sr));
        for (let i = i0; i < i1; i++) {
          const v = ch[i];
          if (v < mn) mn = v;
          if (v > mx) mx = v;
        }
      } else {
        const b0 = Math.max(0, Math.floor(t0 / secPerBucket));
        const b1 = Math.min(totalBuckets, Math.ceil(t1 / secPerBucket));
        for (let b = b0; b < b1; b++) {
          const pmn = peaks[b * 2];
          const pmx = peaks[b * 2 + 1];
          if (pmn < mn) mn = pmn;
          if (pmx > mx) mx = pmx;
        }
      }
      if (mx < mn) {
        mn = 0;
        mx = 0;
      }
      const y1 = (1 - mx) * 0.5 * h;
      const y2 = (1 - mn) * 0.5 * h;
      ctx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
    }
    ctx.restore();

    // Selection border
    ctx.strokeStyle = '#d3869b';
    ctx.lineWidth = 1 * dpr;
    ctx.strokeRect(sx + 0.5, 0.5, selW, h - 1);

    // Handles (flags)
    const handleW = 6 * dpr;
    const handleH = Math.min(h, 22 * dpr);
    ctx.fillStyle = '#d3869b';
    ctx.fillRect(sx - handleW / 2, 0, handleW, handleH);
    ctx.fillRect(ex - handleW / 2, 0, handleW, handleH);
    ctx.fillRect(sx - handleW / 2, h - handleH, handleW, handleH);
    ctx.fillRect(ex - handleW / 2, h - handleH, handleW, handleH);

    // Playhead
    if (c >= vs && c <= ve) {
      const cx = tToX(c);
      ctx.fillStyle = '#fabd2f';
      ctx.fillRect(cx, 0, 1.5 * dpr, h);
    }
  }, [sizeCanvas]);

  const drawRuler = useCallback(() => {
    const canvas = rulerRef.current;
    const buffer = bufferRef.current;
    if (!canvas || !buffer) return;
    const sized = sizeCanvas(canvas);
    if (!sized) return;
    const { w, h, dpr } = sized;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, w, h);
    const { viewStart: vs, viewEnd: ve } = stateRef.current;
    const viewDur = Math.max(1e-6, ve - vs);
    const tick = pickTickInterval(viewDur);
    const firstTick = Math.ceil(vs / tick) * tick;

    ctx.font = `${10 * dpr}px -apple-system, system-ui, sans-serif`;
    ctx.fillStyle = '#a89984';
    ctx.strokeStyle = 'rgba(168,153,132,0.3)';
    ctx.lineWidth = 1;
    for (let t = firstTick; t <= ve + 1e-6; t += tick) {
      const x = ((t - vs) / viewDur) * w;
      ctx.beginPath();
      ctx.moveTo(x, h - 6 * dpr);
      ctx.lineTo(x, h);
      ctx.stroke();
      const label = tick >= 1 ? fmtHMS(t) : `${t.toFixed(2)}s`;
      ctx.fillText(label, x + 3 * dpr, h - 8 * dpr);
    }
  }, [sizeCanvas]);

  const scheduleDraw = useCallback(() => {
    if (drawRafRef.current) return;
    drawRafRef.current = requestAnimationFrame(() => {
      drawRafRef.current = 0;
      drawWave();
      drawRuler();
    });
  }, [drawWave, drawRuler]);

  useEffect(() => {
    scheduleDraw();
  }, [start, end, cursor, viewStart, viewEnd, ready, scheduleDraw]);

  useEffect(() => {
    const onResize = () => scheduleDraw();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [scheduleDraw]);

  const canvasRect = () => {
    const c = waveRef.current;
    return c ? c.getBoundingClientRect() : null;
  };

  const xToTime = (clientX) => {
    const rect = canvasRect();
    if (!rect) return 0;
    return xToTimeUtil(
      clientX - rect.left,
      rect.width,
      stateRef.current.viewStart,
      stateRef.current.viewEnd,
    );
  };

  const pickHandleAt = (clientX) => {
    const rect = canvasRect();
    if (!rect) return null;
    return pickHandleUtil(clientX, rect.left, rect.width, stateRef.current, EDGE_GRAB_PX);
  };

  const applyPointer = useCallback(() => {
    const pos = pointerRef.current;
    if (!pos) return;
    pointerRef.current = null;
    const buffer = bufferRef.current;
    const rect = canvasRect();
    if (!buffer || !rect) return;
    const drag = dragStateRef.current;
    if (!drag) {
      const t = xToTimeUtil(
        pos.clientX - rect.left,
        rect.width,
        stateRef.current.viewStart,
        stateRef.current.viewEnd,
      );
      setCursor(clamp(t, 0, stateRef.current.duration));
      return;
    }
    const out = applyDragUtil(stateRef.current, pos.clientX, rect.left, rect.width, drag, 0.02);
    if (drag.mode === 'start') setStart(out.start);
    else if (drag.mode === 'end') setEnd(out.end);
    else if (drag.mode === 'region' || drag.mode === 'new') {
      setStart(out.start);
      setEnd(out.end);
    } else if (drag.mode === 'pan') {
      setViewStart(out.viewStart);
      setViewEnd(out.viewEnd);
    }
  }, []);

  const schedulePointer = useCallback(() => {
    if (dragRafRef.current) return;
    dragRafRef.current = requestAnimationFrame(() => {
      dragRafRef.current = 0;
      applyPointer();
    });
  }, [applyPointer]);

  useEffect(() => {
    const move = (e) => {
      pointerRef.current = { clientX: e.clientX };
      schedulePointer();
    };
    const up = () => {
      dragStateRef.current = null;
      pointerRef.current = null;
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    return () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
  }, [schedulePointer]);

  const onCanvasDown = (e) => {
    if (!ready) return;
    e.preventDefault();
    const buffer = bufferRef.current;
    if (!buffer) return;
    if (e.button === 1 || e.altKey || e.metaKey) {
      dragStateRef.current = {
        mode: 'pan',
        startClientX: e.clientX,
        viewStart: stateRef.current.viewStart,
        viewDur: stateRef.current.viewEnd - stateRef.current.viewStart,
      };
      pointerRef.current = { clientX: e.clientX };
      schedulePointer();
      return;
    }
    const handle = pickHandleAt(e.clientX);
    if (handle === 'region') {
      const t = xToTime(e.clientX);
      dragStateRef.current = {
        mode: 'region',
        regionLen: stateRef.current.end - stateRef.current.start,
        offset: t - stateRef.current.start,
      };
    } else if (handle === 'start' || handle === 'end') {
      dragStateRef.current = { mode: handle };
    } else {
      const t = clamp(xToTime(e.clientX), 0, buffer.duration);
      // Start a fresh selection anchored at click point. Drag extends it.
      setStart(t);
      setEnd(t);
      setCursor(t);
      dragStateRef.current = { mode: 'new', anchorT: t };
    }
    pointerRef.current = { clientX: e.clientX };
    schedulePointer();
  };

  const onWheel = useCallback((e) => {
    const buffer = bufferRef.current;
    if (!buffer) return;
    e.preventDefault();
    const rect = waveRef.current.getBoundingClientRect();
    const { viewStart: vs, viewEnd: ve } = stateRef.current;
    const viewDur = ve - vs;
    if (e.shiftKey) {
      const dir = Math.sign(e.deltaY || e.deltaX);
      const pan = dir * viewDur * 0.15;
      const newVs = clamp(vs + pan, 0, buffer.duration - viewDur);
      setViewStart(newVs);
      setViewEnd(newVs + viewDur);
    } else {
      const xFrac = clamp((e.clientX - rect.left) / rect.width, 0, 1);
      const factor = e.deltaY < 0 ? 0.8 : 1.25;
      const minDur = Math.max(0.01, buffer.sampleRate ? 200 / buffer.sampleRate : 0.01);
      const out = zoomAtCursor(vs, ve, buffer.duration, factor, xFrac, minDur);
      setViewStart(out.viewStart);
      setViewEnd(out.viewEnd);
    }
  }, []);

  useEffect(() => {
    const canvas = waveRef.current;
    if (!canvas) return;
    const handler = (e) => {
      if (ready) onWheel(e);
    };
    canvas.addEventListener('wheel', handler, { passive: false });
    return () => canvas.removeEventListener('wheel', handler);
  }, [onWheel, ready]);

  const zoomIn = () => {
    const buffer = bufferRef.current;
    if (!buffer) return;
    const { viewStart: vs, viewEnd: ve } = stateRef.current;
    const out = zoomCenter(vs, ve, buffer.duration, 0.5);
    setViewStart(out.viewStart);
    setViewEnd(out.viewEnd);
  };
  const zoomOut = () => {
    const buffer = bufferRef.current;
    if (!buffer) return;
    const { viewStart: vs, viewEnd: ve } = stateRef.current;
    const out = zoomCenter(vs, ve, buffer.duration, 2);
    setViewStart(out.viewStart);
    setViewEnd(out.viewEnd);
  };
  const fitAll = () => {
    const buffer = bufferRef.current;
    if (!buffer) return;
    setViewStart(0);
    setViewEnd(buffer.duration);
  };
  const fitSelection = () => {
    const buffer = bufferRef.current;
    if (!buffer) return;
    const { start: s, end: e } = stateRef.current;
    const pad = Math.max(0.1, (e - s) * 0.2);
    const nvs = clamp(s - pad, 0, buffer.duration);
    const nve = clamp(e + pad, nvs + 0.02, buffer.duration);
    setViewStart(nvs);
    setViewEnd(nve);
  };

  // Preview plays the SAME decoded buffer the waveform is drawn from and the
  // confirmed clip is sliced from — never the original file on a second media
  // element. Seeking a media element treats the selection's buffer-seconds as
  // container-seconds, which drift apart for VBR / mis-reported-duration files
  // (#1210): the preview then plays a different region than the one selected
  // and exported. One buffer, one timeline, guarantees they match.
  const stopPlayback = useCallback(() => {
    if (playRafRef.current) {
      cancelAnimationFrame(playRafRef.current);
      playRafRef.current = 0;
    }
    const src = sourceRef.current;
    sourceRef.current = null;
    playClockRef.current = null;
    if (src) {
      try {
        src.onended = null;
        src.stop();
      } catch {}
    }
    setPlaying(false);
  }, []);

  const startPlayhead = useCallback(() => {
    const tick = () => {
      const ctx = audioCtxRef.current;
      const clock = playClockRef.current;
      if (!ctx || !clock || !sourceRef.current) {
        playRafRef.current = 0;
        return;
      }
      const { start: s, end: e } = stateRef.current;
      const elapsed = ctx.currentTime - clock.t0;
      setCursor(selectionPlayhead(s, e, elapsed, loopRef.current));
      if (!loopRef.current && elapsed >= Math.max(1e-6, e - s)) {
        stopPlayback();
        return;
      }
      playRafRef.current = requestAnimationFrame(tick);
    };
    if (!playRafRef.current) playRafRef.current = requestAnimationFrame(tick);
  }, [stopPlayback]);

  const togglePlay = () => {
    const buffer = bufferRef.current;
    if (!buffer) return;
    if (playing) {
      stopPlayback();
      return;
    }
    try {
      let ctx = audioCtxRef.current;
      if (!ctx) {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        ctx = new Ctx();
        audioCtxRef.current = ctx;
      }
      if (ctx.state === 'suspended') ctx.resume().catch(() => {});
      const { start: s, end: e } = stateRef.current;
      // loopWindow floors a zero-width/inverted selection so the loop range can
      // never collapse and fall back to looping the whole buffer (#1210).
      const { loopStart, loopEnd, seg } = loopWindow(s, e, buffer.duration);
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(ctx.destination);
      if (loopRef.current) {
        src.loop = true;
        src.loopStart = loopStart;
        src.loopEnd = loopEnd;
        src.start(0, loopStart);
      } else {
        src.start(0, loopStart, seg);
        src.onended = () => {
          if (sourceRef.current === src) stopPlayback();
        };
      }
      sourceRef.current = src;
      playClockRef.current = { t0: ctx.currentTime };
      setPlaying(true);
      startPlayhead();
    } catch (err) {
      setError(t('trimmer.playback_failed', { message: err.message || err }));
    }
  };

  // Editing the selection mid-preview retargets the live loop window so what
  // you hear keeps matching the (moving) selection.
  useEffect(() => {
    const src = sourceRef.current;
    if (!src || !src.loop) return;
    if (end > start) {
      src.loopStart = start;
      src.loopEnd = end;
    }
  }, [start, end]);

  // Tear playback down on unmount so the AudioContext and its rAF never leak.
  useEffect(
    () => () => {
      stopPlayback();
      const ctx = audioCtxRef.current;
      audioCtxRef.current = null;
      if (ctx) {
        try {
          ctx.close();
        } catch {}
      }
    },
    [stopPlayback],
  );

  const duration = end - start;
  const tooLong = duration > maxSeconds;
  const tooShort = duration < 0.1;

  const commitStartInput = () => {
    const v = parseFloat(startInput);
    if (isFinite(v)) setStart(clamp(v, 0, Math.max(0, end - 0.02)));
    else setStartInput(start.toFixed(2));
  };
  const commitEndInput = () => {
    const buffer = bufferRef.current;
    const v = parseFloat(endInput);
    if (isFinite(v) && buffer) setEnd(clamp(v, start + 0.02, buffer.duration));
    else setEndInput(end.toFixed(2));
  };

  const onKeyDown = (e) => {
    if (!ready) return;
    const buffer = bufferRef.current;
    if (!buffer) return;
    const tgt = e.target;
    if (tgt && (tgt.tagName === 'INPUT' || tgt.tagName === 'TEXTAREA')) return;
    const fine = e.shiftKey ? 0.01 : e.altKey ? 1.0 : 0.1;
    if (e.key === ' ' || e.code === 'Space') {
      e.preventDefault();
      togglePlay();
      return;
    }
    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) setEnd(clamp(end - fine, start + 0.02, buffer.duration));
      else setStart(clamp(start - fine, 0, Math.max(0, end - 0.02)));
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) setEnd(clamp(end + fine, start + 0.02, buffer.duration));
      else setStart(clamp(start + fine, 0, Math.max(0, end - 0.02)));
    } else if (e.key === '+' || e.key === '=') {
      e.preventDefault();
      zoomIn();
    } else if (e.key === '-' || e.key === '_') {
      e.preventDefault();
      zoomOut();
    } else if (e.key === 'Home') {
      e.preventDefault();
      fitAll();
    } else if (e.key === 'End') {
      e.preventDefault();
      fitSelection();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onCancel();
    } else if (e.key === 'Enter') {
      if (!tooLong && !tooShort) handleConfirm();
    }
  };

  const keyHandlerRef = useRef(onKeyDown);
  useEffect(() => {
    keyHandlerRef.current = onKeyDown;
  }, [onKeyDown]);

  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const handler = (e) => keyHandlerRef.current(e);
    node.addEventListener('keydown', handler);
    node.focus();
    return () => node.removeEventListener('keydown', handler);
  }, []);

  const handleConfirm = () => {
    const buffer = bufferRef.current;
    if (!buffer || tooLong || tooShort) return;
    const mono = sliceToMono(buffer, start, end);
    const wav = encodeWav(mono, buffer.sampleRate);
    const base = (file.name || 'trimmed').replace(/\.[^.]+$/, '');
    const trimmed = new File([wav], `${base}_trim.wav`, { type: 'audio/wav' });
    onConfirm(trimmed);
  };

  const duration_ms = Math.max(0, (end - start) * 1000);

  return (
    <Dialog
      open
      onClose={onCancel}
      size="xl"
      title={
        <>
          <Scissors size={15} color="var(--color-brand)" /> {t('trimmer.title')}
        </>
      }
    >
      <div ref={containerRef} tabIndex={-1} className="audio-trimmer">
        <div className="flex gap-[var(--space-6)] items-center [font-size:var(--text-base)] text-fg-muted">
          <span>
            {decoding
              ? t('trimmer.decoding')
              : audioMeta
                ? `${t('trimmer.meta_length', { duration: fmtHMS(audioMeta.duration), sampleRate: audioMeta.sampleRate })}${peakProgress > 0 && peakProgress < 1 ? ` · ${t('trimmer.meta_rendering', { percent: Math.round(peakProgress * 100) })}` : ''}`
                : '…'}
          </span>
          <span className="ml-auto text-fg-subtle [font-size:var(--text-2xs)]">
            {t('trimmer.keyboard_hint')}
          </span>
        </div>

        {error && <div className="text-danger [font-size:var(--text-md)]">{error}</div>}

        {/* Zoom controls */}
        <div className="flex gap-[var(--space-2)] items-center">
          <Button
            variant="subtle"
            iconSize="md"
            onClick={zoomIn}
            disabled={!ready}
            title={t('trimmer.zoom_in')}
          >
            <ZoomIn size={12} />
          </Button>
          <Button
            variant="subtle"
            iconSize="md"
            onClick={zoomOut}
            disabled={!ready}
            title={t('trimmer.zoom_out')}
          >
            <ZoomOut size={12} />
          </Button>
          <Button
            variant="subtle"
            iconSize="md"
            onClick={fitAll}
            disabled={!ready}
            title={t('trimmer.fit_all')}
          >
            <Maximize2 size={12} />
          </Button>
          <Button
            variant="chip"
            size="sm"
            onClick={fitSelection}
            disabled={!ready}
            title={t('trimmer.fit_selection')}
          >
            {t('trimmer.fit_sel_btn')}
          </Button>
          <div className="ml-auto [font-size:var(--text-sm)] text-fg-subtle tabular-nums">
            {t('trimmer.view_range', {
              start: fmtHMS(viewStart),
              end: fmtHMS(viewEnd),
              duration: fmtSec(viewEnd - viewStart, viewEnd - viewStart < 10 ? 2 : 0),
            })}
          </div>
        </div>

        {/* Ruler */}
        <canvas
          ref={rulerRef}
          className="w-full h-[18px] bg-[#141414] rounded-md border-b border-solid border-b-transparent"
        />

        {/* Waveform */}
        <canvas
          ref={waveRef}
          onMouseDown={onCanvasDown}
          className="w-full h-[160px] bg-[#0f1112] rounded-lg border border-solid border-transparent cursor-crosshair touch-none"
        />

        {/* Numeric fields */}
        <div className="grid grid-cols-[1fr_1fr_1fr_auto] gap-[var(--space-4)] items-center">
          <label className="flex items-center gap-[var(--space-2)] bg-[#0f1112] border border-solid border-border rounded-md px-[6px] py-[2px]">
            <span className="[font-size:var(--text-xs)] text-fg-subtle uppercase [letter-spacing:0.05em] font-semibold">
              {t('trimmer.start_label')}
            </span>
            <input
              type="text"
              inputMode="decimal"
              value={startInput}
              onChange={(e) => setStartInput(e.target.value)}
              onBlur={commitStartInput}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  commitStartInput();
                }
              }}
              className="bg-transparent border-0 outline-none text-fg [font-size:var(--text-md)] px-[6px] py-[4px] w-full min-w-[60px] font-mono"
            />
            <span className="[font-size:var(--text-xs)] text-fg-subtle">
              {t('trimmer.unit_seconds')}
            </span>
          </label>
          <label className="flex items-center gap-[var(--space-2)] bg-[#0f1112] border border-solid border-border rounded-md px-[6px] py-[2px]">
            <span className="[font-size:var(--text-xs)] text-fg-subtle uppercase [letter-spacing:0.05em] font-semibold">
              {t('trimmer.end_label')}
            </span>
            <input
              type="text"
              inputMode="decimal"
              value={endInput}
              onChange={(e) => setEndInput(e.target.value)}
              onBlur={commitEndInput}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  commitEndInput();
                }
              }}
              className="bg-transparent border-0 outline-none text-fg [font-size:var(--text-md)] px-[6px] py-[4px] w-full min-w-[60px] font-mono"
            />
            <span className="[font-size:var(--text-xs)] text-fg-subtle">
              {t('trimmer.unit_seconds')}
            </span>
          </label>
          <div className="flex items-center gap-[var(--space-2)] bg-[#0f1112] border border-solid border-border rounded-md px-[6px] py-[2px] cursor-default">
            <span className="[font-size:var(--text-xs)] text-fg-subtle uppercase [letter-spacing:0.05em] font-semibold">
              {t('trimmer.length_label')}
            </span>
            <span
              className={`px-[6px] py-[4px] font-mono [font-size:var(--text-md)] flex-1 ${tooLong ? 'text-danger' : 'text-[#b8bb26]'}`}
            >
              {(duration_ms / 1000).toFixed(2)}
              {t('trimmer.unit_seconds')}
            </span>
            <span className="[font-size:var(--text-xs)] text-fg-subtle">
              {tooLong
                ? t('trimmer.too_long', { max: maxSeconds })
                : tooShort
                  ? t('trimmer.too_short')
                  : t('trimmer.length_ok')}
            </span>
          </div>
          <Button
            variant="icon"
            iconSize="md"
            active={loop}
            onClick={() => setLoop((v) => !v)}
            title={t('trimmer.loop_preview')}
          >
            <Repeat size={12} />
          </Button>
        </div>

        {/* Play / Action row */}
        <div className="flex gap-[var(--space-4)] items-center flex-wrap">
          <Button
            variant="subtle"
            onClick={togglePlay}
            disabled={!ready}
            leading={playing ? <Pause size={12} /> : <Play size={12} />}
            className="audio-trimmer__play-btn"
          >
            {playing ? t('trimmer.pause') : t('trimmer.preview_selection')}
          </Button>
          <span className="[font-size:var(--text-base)] text-fg-subtle">
            {t('trimmer.play_hint')}
          </span>

          <div className="ml-auto flex gap-[var(--space-4)]">
            <Button variant="ghost" onClick={onCancel}>
              {t('trimmer.cancel')}
            </Button>
            <Button
              variant={tooLong || tooShort ? 'danger' : 'primary'}
              disabled={!ready || tooLong || tooShort}
              onClick={handleConfirm}
              leading={<Check size={12} />}
            >
              {t('trimmer.use_trimmed')}
            </Button>
          </div>
        </div>
      </div>
    </Dialog>
  );
}
