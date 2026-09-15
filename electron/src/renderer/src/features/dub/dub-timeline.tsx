import { HeadphonesIcon, LoaderCircleIcon, PlayIcon, TriangleAlertIcon, ZoomInIcon, ZoomOutIcon, MaximizeIcon } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  clampSegmentEdit,
  detectOverlaps,
  nearestOnset,
  snapCandidates,
  snapTime,
} from '../../../../../../frontend/src/utils/timeline';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import {
  requestPlaybackRange,
  requestPlaybackSeek,
  usePlaybackClock,
} from '@/lib/audio/playback-clock';
import type { DubSegment } from './dub-session';
import { deleteDubSegment, moveResizeDubSegment } from './dub-session';

type Gesture = {
  index: number;
  id: string;
  mode: 'start' | 'end' | 'move';
  pointerId: number;
  x: number;
  start: number;
  end: number;
};

const formatTime = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds % 60).toFixed(1).padStart(4, '0')}`;
};

export function DubTimeline({
  segments,
  disabled,
  mediaDuration,
  onsets = [],
  peaks = [],
  playbackSource = 'dub-preview',
  previewingId,
  selectedId,
  onSelect,
  onPreviewSegment,
}: {
  segments: DubSegment[];
  disabled: boolean;
  mediaDuration?: number;
  onsets?: number[];
  peaks?: number[];
  playbackSource?: string;
  previewingId?: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onPreviewSegment?: (segment: DubSegment) => void;
}) {
  const { t } = useTranslation();
  const playback = usePlaybackClock(playbackSource);
  const host = useRef<HTMLDivElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const [timelineWidth, setTimelineWidth] = useState(1000);
  const onsetCanvas = useRef<HTMLCanvasElement>(null);
  const segmentRefs = useRef(new Map<string, HTMLDivElement>());
  const gesture = useRef<Gesture | null>(null);
  const liveRef = useRef<{ id: string; start: number; end: number } | null>(null);
  const [live, setLive] = useState<{ id: string; start: number; end: number } | null>(null);
  const [focusId, setFocusId] = useState<string | null>(selectedId || segments[0]?.id || null);
  const [editMode, setEditMode] = useState(false);
  const duration = Math.max(
    0.3,
    Number.isFinite(mediaDuration) ? Number(mediaDuration) : 0,
    playback.duration,
    ...segments.map((segment) => segment.end),
  );
  const effective = useMemo(
    () =>
      live
        ? segments.map((segment) =>
            segment.id === live.id ? { ...segment, start: live.start, end: live.end } : segment,
          )
        : segments,
    [live, segments],
  );
  const overlaps = useMemo(() => detectOverlaps(effective), [effective]);

  useEffect(() => {
    if (focusId && segments.some((segment) => segment.id === focusId)) return;
    setFocusId(selectedId || segments[0]?.id || null);
  }, [focusId, segments, selectedId]);

  useEffect(() => {
    const canvas = onsetCanvas.current;
    const container = host.current;
    if (!canvas || !container) return;
    const draw = () => {
      const width = container.clientWidth;
      const height = container.clientHeight;
      setTimelineWidth(width);
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.min(8192, Math.max(1, Math.round(width * dpr)));
      canvas.height = Math.max(1, Math.round(height * dpr));
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      const context = canvas.getContext('2d');
      if (!context) return;
      context.clearRect(0, 0, canvas.width, canvas.height);
      const color = getComputedStyle(canvas).color;
      if (peaks.length) {
        const middle = canvas.height / 2;
        const step = canvas.width / peaks.length;
        context.fillStyle = color;
        context.globalAlpha = 0.28;
        for (let index = 0; index < peaks.length; index += 1) {
          const amplitude = Math.min(1, Math.max(0.02, peaks[index])) * canvas.height * 0.42;
          context.fillRect(
            index * step,
            middle - amplitude,
            Math.max(1, step * 0.65),
            amplitude * 2,
          );
        }
        context.globalAlpha = 1;
      }
      context.strokeStyle = color;
      context.lineWidth = dpr;
      context.beginPath();
      for (const onset of onsets) {
        if (onset < 0 || onset > duration) continue;
        const x = Math.round((onset / duration) * canvas.width) + 0.5;
        context.moveTo(x, canvas.height * 0.62);
        context.lineTo(x, canvas.height);
      }
      context.stroke();
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(container);
    const themeObserver = new MutationObserver(draw);
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class', 'data-theme', 'style'],
    });
    return () => {
      observer.disconnect();
      themeObserver.disconnect();
    };
  }, [duration, onsets, peaks]);

  const begin = (event: React.PointerEvent<HTMLDivElement>, index: number) => {
    if (disabled || event.button !== 0) return;
    if (((effective[index].end - effective[index].start) / duration) * timelineWidth < 16) return;
    const segment = effective[index];
    const mode =
      (event.target as HTMLElement).dataset.edge === 'start'
        ? 'start'
        : (event.target as HTMLElement).dataset.edge === 'end'
          ? 'end'
          : 'move';
    gesture.current = {
      index,
      id: segment.id,
      mode,
      pointerId: event.pointerId,
      x: event.clientX,
      start: segment.start,
      end: segment.end,
    };
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      /* Pointer capture is absent in some embedded/test DOMs. */
    }
    onSelect(segment.id);
  };

  const move = (event: React.PointerEvent<HTMLDivElement>) => {
    const active = gesture.current;
    const width = host.current?.clientWidth || 0;
    if (!active || !width) return;
    const delta = ((event.clientX - active.x) / width) * duration;
    const proposed =
      active.mode === 'move'
        ? { start: active.start + delta, end: active.end + delta }
        : active.mode === 'start'
          ? { start: active.start + delta, end: active.end }
          : { start: active.start, end: active.end + delta };
    let snapped = proposed;
    if (!event.altKey) {
      const edge = active.mode === 'end' ? proposed.end : proposed.start;
      const candidates = snapCandidates({
        onsets,
        prevEnd: segments[active.index - 1]?.end,
        nextStart: segments[active.index + 1]?.start,
        playhead: playback.time,
        pxPerSec: width / duration,
        t: edge,
      });
      const result = snapTime(edge, candidates, (8 / width) * duration);
      if (result.candidate != null) {
        if (active.mode === 'move') {
          const length = active.end - active.start;
          snapped = { start: result.time, end: result.time + length };
        } else if (active.mode === 'start') snapped = { ...proposed, start: result.time };
        else snapped = { ...proposed, end: result.time };
      }
    }
    const next = clampSegmentEdit(segments, active.index, active.mode, snapped, {
      allowOverlap: event.altKey,
      duration,
    });
    liveRef.current = { id: active.id, ...next };
    setLive(liveRef.current);
  };

  const finish = (event: React.PointerEvent<HTMLDivElement>, commit = true) => {
    const active = gesture.current;
    gesture.current = null;
    const current = liveRef.current;
    if (commit && active && current?.id === active.id)
      moveResizeDubSegment(active.id, { start: current.start, end: current.end });
    liveRef.current = null;
    setLive(null);
    try {
      if (event.currentTarget.hasPointerCapture(event.pointerId))
        event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      /* Pointer capture is absent in some embedded/test DOMs. */
    }
  };

  const selectAndFocus = (id: string) => {
    setFocusId(id);
    onSelect(id);
    requestAnimationFrame(() => segmentRefs.current.get(id)?.focus({ preventScroll: true }));
  };

  const moveFocus = (index: number, direction: -1 | 1) => {
    const next = segments[index + direction];
    if (next) selectAndFocus(next.id);
  };

  const nudge = (event: React.KeyboardEvent<HTMLDivElement>, index: number) => {
    const segment = segments[index];
    if (disabled) return;
    if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      event.stopPropagation();
      const neighbor = segments[index + 1] || segments[index - 1];
      deleteDubSegment(segment.id);
      if (neighbor) selectAndFocus(neighbor.id);
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      event.stopPropagation();
      setEditMode((active) => !active);
      return;
    }
    if (event.key === 'Escape' && editMode) {
      event.preventDefault();
      event.stopPropagation();
      setEditMode(false);
      return;
    }
    if (event.key.toLowerCase() === 's') {
      event.preventDefault();
      event.stopPropagation();
      const adjustEnd = event.shiftKey;
      const onset = nearestOnset(adjustEnd ? segment.end : segment.start, onsets);
      if (onset == null) return;
      const next = clampSegmentEdit(
        segments,
        index,
        adjustEnd ? 'end' : 'start',
        adjustEnd ? { start: segment.start, end: onset } : { start: onset, end: segment.end },
        { duration },
      );
      moveResizeDubSegment(segment.id, next);
      return;
    }
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    const direction = event.key === 'ArrowLeft' ? -1 : 1;
    if (!editMode) {
      moveFocus(index, direction);
      return;
    }
    const amount = (event.ctrlKey || event.metaKey ? 0.1 : 0.01) * direction;
    const mode = event.altKey ? 'move' : event.shiftKey ? 'end' : 'start';
    const next = clampSegmentEdit(
      segments,
      index,
      mode,
      mode === 'move'
        ? { start: segment.start + amount, end: segment.end + amount }
        : mode === 'end'
          ? { start: segment.start, end: segment.end + amount }
          : { start: segment.start + amount, end: segment.end },
      { duration },
    );
    moveResizeDubSegment(segment.id, next);
  };

  return (
    <section
      aria-label={t('segmentEditing.timeline')}
      className="rounded-xl border border-border/60 bg-card/35 p-3 shadow-sm"
    >
      <div className="mb-2 flex justify-end gap-1">
        <Button size="icon-sm" variant="ghost" aria-label={t('trimmer.zoom_out')} disabled={zoom <= 1} onClick={() => setZoom((z) => Math.max(1, z / 2))}><ZoomOutIcon /></Button>
        <Button size="icon-sm" variant="ghost" aria-label={t('trimmer.zoom_in')} disabled={zoom >= 16} onClick={() => setZoom((z) => Math.min(16, z * 2))}><ZoomInIcon /></Button>
        <Button size="icon-sm" variant="ghost" aria-label={t('trimmer.fit_all')} onClick={() => setZoom(1)}><MaximizeIcon /></Button>
      </div>
      <div ref={viewport} className="overflow-x-auto rounded-lg [scrollbar-width:thin]">
      <div
        ref={host}
        style={{ width: `${zoom * 100}%` }}
        role="listbox"
        aria-orientation="horizontal"
        onClick={(event) => {
          if ((event.target as HTMLElement).closest('[data-timeline-segment]')) return;
          const bounds = event.currentTarget.getBoundingClientRect();
          if (bounds.width > 0)
            requestPlaybackSeek(
              playbackSource,
              ((event.clientX - bounds.left) / bounds.width) * duration,
            );
        }}
        className="relative h-16 touch-none overflow-hidden rounded-lg border border-border/50 bg-muted/20 [background-image:repeating-linear-gradient(90deg,transparent_0,transparent_calc(10%_-_1px),color-mix(in_oklch,var(--border),transparent_55%)_10%)]"
      >
        <canvas
          ref={onsetCanvas}
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 text-muted-foreground/45"
        />
        {playback.duration > 0 && (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 z-[1] w-px bg-primary shadow-[0_0_8px_color-mix(in_oklch,var(--primary),transparent_20%)]"
            style={{ left: `${(Math.min(playback.time, duration) / duration) * 100}%` }}
          />
        )}
        {effective.map((segment, index) => (
          <div
            key={segment.id}
            ref={(element) => {
              if (element) segmentRefs.current.set(segment.id, element);
              else segmentRefs.current.delete(segment.id);
            }}
            role="option"
            tabIndex={focusId === segment.id || (!focusId && index === 0) ? 0 : -1}
            data-timeline-segment={segment.id}
            aria-label={`${t('clone.text_label')} ${index + 1}, ${formatTime(segment.start)} - ${formatTime(segment.end)}`}
            aria-selected={selectedId === segment.id}
            onClick={() => selectAndFocus(segment.id)}
            onDoubleClick={() => requestPlaybackRange(playbackSource, segment.start, segment.end)}
            onKeyDown={(event) => nudge(event, index)}
            onFocus={() => setFocusId(segment.id)}
            onBlur={(event) => {
              if (!event.currentTarget.parentElement?.contains(event.relatedTarget))
                setEditMode(false);
            }}
            onPointerDown={(event) => begin(event, index)}
            onPointerMove={move}
            onPointerUp={finish}
            onPointerCancel={(event) => finish(event, false)}
            className={cn(
              'absolute top-2 flex h-10 min-w-0 cursor-grab items-center overflow-hidden rounded-sm bg-primary/30 px-0 text-[10px] font-medium text-foreground outline-none transition-[box-shadow,background-color] active:cursor-grabbing focus-visible:ring-2 focus-visible:ring-ring',
              selectedId === segment.id && 'border-primary/70 bg-primary/35 shadow-sm',
              focusId === segment.id &&
                editMode &&
                'ring-2 ring-primary ring-offset-1 ring-offset-background',
              overlaps.has(segment.id) && 'border-destructive bg-destructive/20',
            )}
            style={{
              left: `${(segment.start / duration) * 100}%`,
              width: `${((segment.end - segment.start) / duration) * 100}%`,
            }}
          >
            {((segment.end - segment.start) / duration) * timelineWidth >= 24 && <span
              data-edge="start"
              aria-hidden="true"
              className="absolute inset-y-0 left-0 w-1.5 cursor-ew-resize bg-foreground/15"
            />}
            {((segment.end - segment.start) / duration) * timelineWidth >= 18 && <span className="pointer-events-none truncate px-1">{index + 1}</span>}
            {selectedId === segment.id && ((segment.end - segment.start) / duration) * timelineWidth > 60 && (
              <span className="ml-auto flex shrink-0 gap-0.5">
                <button
                  type="button"
                  aria-label={`${t('player.play')} ${index + 1}`}
                  title={t('player.play')}
                  className="flex size-5 items-center justify-center rounded-sm bg-background/75 text-foreground shadow-sm hover:bg-background"
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={(event) => {
                    event.stopPropagation();
                    requestPlaybackRange(playbackSource, segment.start, segment.end);
                  }}
                >
                  <PlayIcon className="size-3 fill-current" />
                </button>
                {onPreviewSegment && ((segment.end - segment.start) / duration) * timelineWidth > 100 && (
                  <button
                    type="button"
                    aria-label={t('dub.live_preview')}
                    title={t('dub.live_preview')}
                    disabled={Boolean(previewingId)}
                    className="flex size-5 items-center justify-center rounded-sm bg-background/75 text-foreground shadow-sm hover:bg-background disabled:opacity-50"
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={(event) => {
                      event.stopPropagation();
                      onPreviewSegment(segment);
                    }}
                  >
                    {previewingId === segment.id ? (
                      <LoaderCircleIcon className="size-3 animate-spin" />
                    ) : (
                      <HeadphonesIcon className="size-3" />
                    )}
                  </button>
                )}
              </span>
            )}
            {((segment.end - segment.start) / duration) * timelineWidth >= 24 && <span
              data-edge="end"
              aria-hidden="true"
              className="absolute inset-y-0 right-0 w-1.5 cursor-ew-resize bg-foreground/15"
            />}
          </div>
        ))}
      </div>
      </div>
      <div className="mt-1 flex items-center justify-between font-mono text-[10px] text-muted-foreground tabular-nums">
        <span>0:00.0</span>
        {overlaps.size > 0 && (
          <button type="button" className="flex items-center gap-1 text-destructive text-left" onClick={() => {
            const id = [...overlaps][0];
            setZoom(16);
            selectAndFocus(id);
            requestAnimationFrame(() => segmentRefs.current.get(id)?.scrollIntoView({ block: 'nearest', inline: 'center' }));
          }}>
            <TriangleAlertIcon className="size-3" />
            {t('segmentEditing.overlap')}
          </button>
        )}
        <span>{formatTime(duration)}</span>
      </div>
    </section>
  );
}
