import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { useTranslation } from 'react-i18next';
import { Play, Headphones } from 'lucide-react';
import {
  getRegionColors,
  subscribeRegionColors,
  SNAP_PX,
  visibleSegmentRange,
  snapTime,
  snapCandidates,
  clampSegmentEdit,
  detectOverlaps,
  nearestOnset,
} from '../utils/timeline';

const ONSET_STRIP_H = 8; // px — non-interactive onset tick strip
const KB_STEP_S = 0.01; // ←/→ nudge
const KB_STEP_BIG_S = 0.1; // Ctrl+←/→ nudge
const DRAG_DEADZONE_PX = 3;
// Small/medium dubbing jobs are cheap enough to render in full. Avoid tying
// visibility to transient WaveSurfer resize/zoom metrics until the transcript
// is genuinely large; this also keeps short multi-speaker clips complete.
const VIRTUALIZE_THRESHOLD = 200;

const fmt = (t) => {
  const m = Math.floor(t / 60);
  const s = (t % 60).toFixed(2);
  return `${m}:${s.padStart(5, '0')}`;
};

/**
 * SegmentTrack — custom DOM segment editor lane for the dub timeline (#280).
 *
 * Replaces the WaveSurfer Regions plugin in the editing path. Renders one
 * absolutely-positioned box per segment, each placed in PURE LAYOUT from a
 * single {pxPerSec, scrollLeft} source (read off WaveSurfer's wrapper by the
 * parent), so boxes stay pixel-aligned with the waveform across
 * zoom/scroll/resize. The lane itself must NEVER be transform-animated: a
 * lane translateX'd on every playback tick gets promoted to a compositor
 * layer by Chromium, and composited semi-transparent paints flash
 * invisible/visible on some Windows GPU/WebView2 drivers (#373; #381 only
 * dampened it). Virtualized by TIME — only the boxes inside the visible
 * window (+ buffer) are mounted.
 *
 * Props:
 *   segments        sorted-by-start segment array (store shape)
 *   pxPerSec        px per second (single alignment source)
 *   scrollLeft      px (ignored when selfScroll — WebKit fallback)
 *   duration        track duration (s)
 *   currentTime     playhead (s)
 *   onsets          speech onset times (s) for snap + tick strip
 *   disabled        locks all editing (generation in flight)
 *   selectedId      selected segment id (String), selection syncs the table
 *   onSelectSeg     (id|null) => void
 *   incrementalPlan { stale: [ids], fresh: [ids] } | null
 *   onCommit        (id, {start,end}, {undo}) => void — ONE per gesture
 *   onDelete        (id) => void
 *   onPlayRange     (start, end) => void — play the slot on the main player
 *   onPreviewSegment(seg) => void — synthesize-and-play this segment's dub
 *   onEnsureVisible (timeS) => void — ask parent to scroll a time into view
 *   selfScroll      WebKit fallback: lane scrolls itself (fixed pxPerSec)
 */
export default function SegmentTrack({
  segments = [],
  pxPerSec = 0,
  scrollLeft = 0,
  duration = 0,
  currentTime = 0,
  onsets = [],
  disabled = false,
  selectedId = null,
  onSelectSeg,
  incrementalPlan = null,
  onCommit,
  onDelete,
  onPlayRange,
  onPreviewSegment,
  onEnsureVisible,
  selfScroll = false,
}) {
  const { t } = useTranslation();
  const hostRef = useRef(null);
  const viewportRef = useRef(null);
  const canvasRef = useRef(null);
  const boxRefs = useRef(new Map());
  const gestureRef = useRef(null); // live pointer gesture
  const kbGestureRef = useRef(false); // first nudge of a focus session pushed undo?

  const [viewWidth, setViewWidth] = useState(0);
  const [innerScroll, setInnerScroll] = useState(0); // selfScroll mode only
  const [live, setLive] = useState(null); // {id,start,end} during drag
  const [activeEdge, setActiveEdge] = useState(null); // dragged edge time (onset highlight)
  const [focusId, setFocusId] = useState(null);
  const [editMode, setEditMode] = useState(false);
  const [announceMsg, setAnnounceMsg] = useState('');

  const effScroll = selfScroll ? innerScroll : scrollLeft;
  const announce = useCallback((msg) => setAnnounceMsg(msg), []);

  // ── Geometry ────────────────────────────────────────────────────────────
  useLayoutEffect(() => {
    const el = hostRef.current;
    if (!el) return undefined;
    const measure = () => setViewWidth(el.clientWidth || 0);
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const indexById = useMemo(() => {
    const m = new Map();
    segments.forEach((s, i) => m.set(String(s.id), i));
    return m;
  }, [segments]);

  // Segments with the in-flight drag override applied (render + overlap).
  const effSegments = useMemo(() => {
    if (!live) return segments;
    return segments.map((s) =>
      String(s.id) === live.id ? { ...s, start: live.start, end: live.end } : s,
    );
  }, [segments, live]);

  const overlaps = useMemo(() => detectOverlaps(effSegments), [effSegments]);
  const staleSet = useMemo(
    () => new Set((incrementalPlan?.stale || []).map(String)),
    [incrementalPlan],
  );
  const freshSet = useMemo(
    () => new Set((incrementalPlan?.fresh || []).map(String)),
    [incrementalPlan],
  );

  const viewStart = pxPerSec > 0 ? effScroll / pxPerSec : 0;
  const viewEnd = pxPerSec > 0 ? (effScroll + viewWidth) / pxPerSec : 0;
  const [lo, hi] = useMemo(
    () => visibleSegmentRange(effSegments, viewStart, viewEnd, 2),
    [effSegments, viewStart, viewEnd],
  );

  // Palette snapshot re-blends against the new --chrome-bg on theme change
  // (#963) — new array identity per re-blend, so the memo below recolors.
  const regionColors = useSyncExternalStore(subscribeRegionColors, getRegionColors);
  const speakerColor = useMemo(() => {
    const speakers = [...new Set(segments.map((s) => s.speaker_id).filter(Boolean))];
    const bySpeaker = new Map(speakers.map((sp, i) => [sp, regionColors[i % regionColors.length]]));
    return (seg, idx) => bySpeaker.get(seg.speaker_id) || regionColors[idx % regionColors.length];
  }, [segments, regionColors]);

  // ── Onset tick strip (one viewport-sized canvas, non-interactive) ───────
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv || viewWidth <= 0) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(viewWidth * dpr);
    const h = Math.round(ONSET_STRIP_H * dpr);
    if (cv.width !== w) cv.width = w;
    if (cv.height !== h) cv.height = h;
    const ctx = cv.getContext('2d');
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    if (pxPerSec <= 0) return;
    const snapS = SNAP_PX / pxPerSec;
    const t0 = effScroll / pxPerSec - 1;
    const t1 = (effScroll + viewWidth) / pxPerSec + 1;
    for (const o of onsets) {
      if (o < t0 || o > t1) continue;
      const x = Math.round((o * pxPerSec - effScroll) * dpr) + 0.5;
      const hot = activeEdge != null && Math.abs(o - activeEdge) <= snapS;
      ctx.strokeStyle = hot ? '#fabd2f' : 'rgba(168,153,132,0.45)';
      ctx.lineWidth = hot ? 2 * dpr : 1 * dpr;
      ctx.beginPath();
      ctx.moveTo(x, hot ? 0 : h * 0.35);
      ctx.lineTo(x, h);
      ctx.stroke();
    }
  }, [onsets, pxPerSec, effScroll, viewWidth, activeEdge]);

  // ── Selection / focus plumbing ──────────────────────────────────────────
  const ensureVisible = useCallback(
    (timeS) => {
      if (selfScroll) {
        const vp = viewportRef.current;
        if (vp && pxPerSec > 0) {
          const x = timeS * pxPerSec;
          if (x < vp.scrollLeft || x > vp.scrollLeft + vp.clientWidth) {
            vp.scrollLeft = Math.max(0, x - vp.clientWidth * 0.3);
          }
        }
        return;
      }
      onEnsureVisible?.(timeS);
    },
    [selfScroll, pxPerSec, onEnsureVisible],
  );

  const selectAndFocus = useCallback(
    (sid) => {
      setFocusId(sid);
      onSelectSeg?.(sid);
    },
    [onSelectSeg],
  );

  // Keep DOM focus on the roving-focus box after re-renders, but only when
  // focus already lives inside the track (never steal it from elsewhere).
  useEffect(() => {
    if (focusId == null) return;
    const el = boxRefs.current.get(String(focusId));
    const host = hostRef.current;
    if (el && host && host.contains(document.activeElement) && document.activeElement !== el) {
      el.focus({ preventScroll: true });
    }
  }, [focusId, lo, hi]);

  // ── Pointer gestures (drag = move, handles = resize) ────────────────────
  const onBoxPointerDown = useCallback(
    (e) => {
      if (e.button !== 0) return;
      const sid = e.currentTarget.dataset.segid;
      selectAndFocus(sid);
      if (disabled) return;
      const idx = indexById.get(sid);
      const s = segments[idx];
      if (!s || pxPerSec <= 0) return;
      const handle = e.target?.dataset?.handle || null;
      gestureRef.current = {
        sid,
        idx,
        mode: handle || 'move',
        startX: e.clientX,
        origStart: s.start,
        origEnd: s.end,
        moved: false,
        last: null,
      };
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        /* jsdom */
      }
    },
    [disabled, indexById, segments, pxPerSec, selectAndFocus],
  );

  const onBoxPointerMove = useCallback(
    (e) => {
      const g = gestureRef.current;
      if (!g || pxPerSec <= 0) return;
      const dx = e.clientX - g.startX;
      if (!g.moved && Math.abs(dx) < DRAG_DEADZONE_PX) return;
      g.moved = true;
      const dt = dx / pxPerSec;
      let proposed;
      if (g.mode === 'move') proposed = { start: g.origStart + dt, end: g.origEnd + dt };
      else if (g.mode === 'start') proposed = { start: g.origStart + dt, end: g.origEnd };
      else proposed = { start: g.origStart, end: g.origEnd + dt };

      // Snap (unless Alt): onsets + adjacent edges + playhead + low-zoom grid.
      const alt = e.altKey;
      if (!alt) {
        const thresholdS = SNAP_PX / pxPerSec;
        const edge = g.mode === 'end' ? proposed.end : proposed.start;
        const cands = snapCandidates({
          onsets,
          prevEnd: segments[g.idx - 1]?.end,
          nextStart: segments[g.idx + 1]?.start,
          playhead: currentTime,
          pxPerSec,
          t: edge,
        });
        const r = snapTime(edge, cands, thresholdS);
        if (r.candidate != null) {
          if (g.mode === 'move') {
            const dur = g.origEnd - g.origStart;
            proposed = { start: r.time, end: r.time + dur };
          } else if (g.mode === 'start') {
            proposed = { ...proposed, start: r.time };
          } else {
            proposed = { ...proposed, end: r.time };
          }
        }
      }
      const clamped = clampSegmentEdit(segments, g.idx, g.mode, proposed, {
        allowOverlap: alt,
        duration: duration || Infinity,
      });
      g.last = clamped;
      setLive({ id: String(g.sid), ...clamped });
      setActiveEdge(g.mode === 'end' ? clamped.end : clamped.start);
    },
    [pxPerSec, onsets, segments, currentTime, duration],
  );

  const onBoxPointerUp = useCallback(() => {
    const g = gestureRef.current;
    gestureRef.current = null;
    setActiveEdge(null);
    setLive(null);
    if (!g || !g.moved || !g.last) return;
    if (g.last.start === g.origStart && g.last.end === g.origEnd) return;
    // ONE undo entry per drag gesture — live positions never hit the store.
    onCommit?.(g.sid, g.last, { undo: true });
    const idx = g.idx;
    if (g.mode === 'move') {
      announce(t('timeline.moved_announce', { index: idx + 1, start: fmt(g.last.start) }));
    } else {
      announce(
        t('timeline.resized_announce', {
          index: idx + 1,
          start: fmt(g.last.start),
          end: fmt(g.last.end),
        }),
      );
    }
  }, [onCommit, announce, t]);

  // ── Keyboard (roving tabindex, #298 pattern) ────────────────────────────
  const moveFocus = useCallback(
    (sid, dir) => {
      const idx = indexById.get(sid);
      if (idx == null) return;
      const next = segments[idx + dir];
      if (!next) return;
      const nid = String(next.id);
      selectAndFocus(nid);
      ensureVisible(next.start);
      // Focus lands via the effect above once the box is mounted; force it if
      // the box is already in the window.
      const el = boxRefs.current.get(nid);
      if (el) el.focus({ preventScroll: true });
    },
    [indexById, segments, selectAndFocus, ensureVisible],
  );

  const nudge = useCallback(
    (sid, dir, e) => {
      if (disabled) return;
      const idx = indexById.get(sid);
      const s = segments[idx];
      if (!s) return;
      const step = e.ctrlKey || e.metaKey ? KB_STEP_BIG_S : KB_STEP_S;
      const delta = dir * step;
      let mode;
      let proposed;
      if (e.altKey) {
        mode = 'move';
        proposed = { start: s.start + delta, end: s.end + delta };
      } else if (e.shiftKey) {
        mode = 'end';
        proposed = { start: s.start, end: s.end + delta };
      } else {
        mode = 'start';
        proposed = { start: s.start + delta, end: s.end };
      }
      const clamped = clampSegmentEdit(segments, idx, mode, proposed, {
        duration: duration || Infinity,
      });
      if (Math.abs(clamped.start - s.start) < 1e-4 && Math.abs(clamped.end - s.end) < 1e-4) return;
      // First nudge of the focus session pushes undo; the rest coalesce.
      onCommit?.(sid, clamped, { undo: !kbGestureRef.current });
      kbGestureRef.current = true;
      announce(
        t('timeline.resized_announce', {
          index: idx + 1,
          start: fmt(clamped.start),
          end: fmt(clamped.end),
        }),
      );
    },
    [disabled, indexById, segments, duration, onCommit, announce, t],
  );

  const snapFocusedEdge = useCallback(
    (sid, useEndEdge) => {
      if (disabled || !onsets.length) return;
      const idx = indexById.get(sid);
      const s = segments[idx];
      if (!s) return;
      const mode = useEndEdge ? 'end' : 'start';
      const target = nearestOnset(useEndEdge ? s.end : s.start, onsets);
      if (target == null) return;
      const proposed = useEndEdge ? { start: s.start, end: target } : { start: target, end: s.end };
      const clamped = clampSegmentEdit(segments, idx, mode, proposed, {
        duration: duration || Infinity,
      });
      if (Math.abs(clamped.start - s.start) < 1e-4 && Math.abs(clamped.end - s.end) < 1e-4) return;
      onCommit?.(sid, clamped, { undo: true }); // discrete action = own gesture
      announce(t('timeline.snapped_announce', { time: fmt(target) }));
    },
    [disabled, onsets, indexById, segments, duration, onCommit, announce, t],
  );

  const handleDelete = useCallback(
    (sid) => {
      if (disabled) return;
      const idx = indexById.get(sid);
      if (idx == null) return;
      const neighbour = segments[idx + 1] || segments[idx - 1];
      onDelete?.(sid);
      announce(t('timeline.deleted_announce', { index: idx + 1 }));
      if (neighbour) selectAndFocus(String(neighbour.id));
    },
    [disabled, indexById, segments, onDelete, announce, selectAndFocus, t],
  );

  const onBoxKeyDown = useCallback(
    (e) => {
      const sid = e.currentTarget.dataset.segid;
      switch (e.key) {
        case 'ArrowLeft':
        case 'ArrowRight': {
          e.preventDefault();
          e.stopPropagation();
          const dir = e.key === 'ArrowLeft' ? -1 : 1;
          if (editMode) nudge(sid, dir, e);
          else moveFocus(sid, dir);
          break;
        }
        case 'Enter':
          e.preventDefault();
          e.stopPropagation();
          if (disabled) break;
          if (editMode) {
            setEditMode(false);
            kbGestureRef.current = false;
            announce(t('timeline.edit_mode_off'));
          } else {
            setEditMode(true);
            kbGestureRef.current = false;
            announce(t('timeline.edit_mode_on'));
          }
          break;
        case 'Escape':
          if (editMode) {
            e.preventDefault();
            e.stopPropagation();
            setEditMode(false);
            kbGestureRef.current = false;
            announce(t('timeline.edit_mode_off'));
          }
          break;
        case 'Delete':
        case 'Backspace':
          e.preventDefault();
          e.stopPropagation();
          handleDelete(sid);
          break;
        case 's':
        case 'S':
          e.preventDefault();
          e.stopPropagation();
          snapFocusedEdge(sid, e.shiftKey);
          break;
        default:
          break;
      }
    },
    [editMode, disabled, nudge, moveFocus, handleDelete, snapFocusedEdge, announce, t],
  );

  const onBoxBlur = useCallback((e) => {
    // Leaving the track ends the keyboard edit session.
    const host = hostRef.current;
    if (host && !host.contains(e.relatedTarget)) {
      setEditMode(false);
      kbGestureRef.current = false;
    }
  }, []);

  if (!segments.length || pxPerSec <= 0) return null;

  const innerWidth = Math.max(viewWidth, Math.ceil(duration * pxPerSec));
  const playheadX = currentTime * pxPerSec - effScroll;
  const windowed =
    effSegments.length <= VIRTUALIZE_THRESHOLD ? effSegments : effSegments.slice(lo, hi);
  // Scroll offset baked into each box's `left` (viewport coordinates) instead
  // of a `translateX` on the lane — an animated lane transform is composited
  // by Chromium and flashes on some Windows GPU/WebView2 drivers (#373). In
  // the selfScroll fallback the viewport is a real scroll container, so boxes
  // stay in lane coordinates (offset 0). The virtualization window above
  // derives from the same effScroll, so both stay consistent by construction.
  const laneShift = selfScroll ? 0 : effScroll;

  return (
    <div
      className={`seg-track relative w-full select-none mt-[2px] shrink-0 flex-none min-h-[50px] ${disabled ? 'is-disabled' : ''}`}
      ref={hostRef}
    >
      <canvas
        ref={canvasRef}
        className="block w-full pointer-events-none"
        style={{ height: ONSET_STRIP_H }}
        aria-hidden="true"
      />
      <div
        ref={viewportRef}
        className={`relative w-full h-[40px] overflow-hidden ${selfScroll ? 'seg-track__viewport--scroll' : ''}`}
        onScroll={selfScroll ? (e) => setInnerScroll(e.currentTarget.scrollLeft) : undefined}
      >
        <div
          className="relative h-full"
          role="listbox"
          aria-label={t('timeline.track_label')}
          aria-orientation="horizontal"
          title={t('timeline.keyboard_hint')}
          style={{
            // selfScroll needs the full content width for the native
            // scrollbar range; in synced mode the lane is a static
            // viewport-sized strip (NO transform — see laneShift above).
            width: selfScroll ? innerWidth : '100%',
          }}
        >
          {windowed.map((s) => {
            const sid = String(s.id);
            const idx = indexById.get(sid) ?? 0;
            const left = s.start * pxPerSec - laneShift;
            const width = Math.max(2, (s.end - s.start) * pxPerSec);
            const isSel = selectedId != null && String(selectedId) === sid;
            const isFocus = focusId === sid;
            const hasOverlap = overlaps.has(sid);
            const stale = staleSet.has(sid);
            const fresh = !stale && freshSet.has(sid);
            const cls = [
              'seg-track__box',
              isSel && 'is-selected',
              isFocus && editMode && 'is-editing',
              hasOverlap && 'is-overlap',
              stale && 'is-stale',
              fresh && 'is-fresh',
              live?.id === sid && 'is-dragging',
            ]
              .filter(Boolean)
              .join(' ');
            return (
              <div
                key={sid}
                ref={(el) => {
                  if (el) boxRefs.current.set(sid, el);
                  else boxRefs.current.delete(sid);
                }}
                role="option"
                aria-selected={isSel}
                aria-label={t('timeline.segment_aria', {
                  index: idx + 1,
                  start: fmt(s.start),
                  end: fmt(s.end),
                })}
                tabIndex={isFocus || (focusId == null && idx === 0) ? 0 : -1}
                data-segid={sid}
                className={cls}
                style={{ left, width, background: speakerColor(s, idx) }}
                title={hasOverlap ? t('timeline.overlap_warning') : s.text || ''}
                onPointerDown={onBoxPointerDown}
                onPointerMove={onBoxPointerMove}
                onPointerUp={onBoxPointerUp}
                onPointerCancel={onBoxPointerUp}
                onDoubleClick={() => onPlayRange?.(s.start, s.end)}
                onKeyDown={onBoxKeyDown}
                onFocus={() => setFocusId(sid)}
                onBlur={onBoxBlur}
              >
                {!disabled && (
                  <span
                    className="seg-track__handle--l absolute top-0 bottom-0 w-[6px] cursor-ew-resize z-[2]"
                    data-handle="start"
                  />
                )}
                <span className="flex-1 min-w-0 px-[8px] text-[9px] leading-[1.2] text-[#ebdbb2] whitespace-nowrap overflow-hidden text-ellipsis pointer-events-none">
                  {s.text?.length > 32 ? `${s.text.slice(0, 30)}…` : s.text || ''}
                </span>
                {isSel && !disabled && width > 64 && (
                  <span className="inline-flex gap-[2px] mr-[8px] z-[3]">
                    <button
                      type="button"
                      className="inline-flex items-center justify-center w-[16px] h-[16px] p-0 border border-transparent rounded-sm bg-[rgba(40,40,40,0.85)] text-[#ebdbb2] cursor-pointer hover:border-transparent hover:text-[#d3869b]"
                      aria-label={t('timeline.play_slot')}
                      title={t('timeline.play_slot')}
                      onPointerDown={(ev) => ev.stopPropagation()}
                      onClick={(ev) => {
                        ev.stopPropagation();
                        onPlayRange?.(s.start, s.end);
                      }}
                    >
                      <Play size={9} />
                    </button>
                    {onPreviewSegment && (
                      <button
                        type="button"
                        className="inline-flex items-center justify-center w-[16px] h-[16px] p-0 border border-transparent rounded-sm bg-[rgba(40,40,40,0.85)] text-[#ebdbb2] cursor-pointer hover:border-transparent hover:text-[#d3869b]"
                        aria-label={t('timeline.preview_dub')}
                        title={t('timeline.preview_dub')}
                        onPointerDown={(ev) => ev.stopPropagation()}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          onPreviewSegment(s);
                        }}
                      >
                        <Headphones size={9} />
                      </button>
                    )}
                  </span>
                )}
                {!disabled && (
                  <span
                    className="seg-track__handle--r absolute top-0 bottom-0 w-[6px] cursor-ew-resize z-[2]"
                    data-handle="end"
                  />
                )}
              </div>
            );
          })}
        </div>
        {playheadX >= 0 && playheadX <= viewWidth && (
          <div
            className="absolute top-0 bottom-0 w-px bg-[#d3869b] opacity-80 pointer-events-none"
            style={{ left: playheadX }}
            aria-hidden="true"
          />
        )}
      </div>
      <div className="seg-track__sr-announce" aria-live="polite" role="status">
        {announceMsg}
      </div>
    </div>
  );
}
