import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import SegmentTrack from './SegmentTrack';

// Mocked transport: fixed pxPerSec/scrollLeft, no WaveSurfer. jsdom has no
// layout, so ResizeObserver reports 0 — stub a viewport width so the
// windowing logic mounts boxes.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub);
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1000);
});

const SEGS = [
  { id: 1, start: 0, end: 2, text: 'first line' },
  { id: '3_a', start: 3, end: 5, text: 'split a' },
  { id: '3_b', start: 5, end: 7, text: 'split b' },
];

function setup(props = {}) {
  const onCommit = vi.fn();
  const onDelete = vi.fn();
  const onSelectSeg = vi.fn();
  const onPlayRange = vi.fn();
  const utils = render(
    <SegmentTrack
      segments={SEGS}
      pxPerSec={100}
      scrollLeft={0}
      duration={10}
      currentTime={0}
      onsets={[0.5, 3.1]}
      onCommit={onCommit}
      onDelete={onDelete}
      onSelectSeg={onSelectSeg}
      onPlayRange={onPlayRange}
      {...props}
    />,
  );
  return { onCommit, onDelete, onSelectSeg, onPlayRange, ...utils };
}

const box = (i) => screen.getAllByRole('option')[i];

describe('SegmentTrack — rendering', () => {
  it('renders one listbox with a box per visible segment', () => {
    setup();
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    expect(screen.getAllByRole('option')).toHaveLength(3);
  });

  it('keys boxes by String(id) — split ids render and carry data-segid', () => {
    setup();
    expect(box(1).dataset.segid).toBe('3_a');
    expect(box(2).dataset.segid).toBe('3_b');
  });

  it('roving tabindex: exactly one tab stop', () => {
    setup();
    const stops = screen.getAllByRole('option').filter((el) => el.tabIndex === 0);
    expect(stops).toHaveLength(1);
  });

  it('marks the selected box', () => {
    setup({ selectedId: '3_a' });
    expect(box(1)).toHaveAttribute('aria-selected', 'true');
    expect(box(0)).toHaveAttribute('aria-selected', 'false');
  });

  it('renders nothing without pxPerSec', () => {
    const { container } = render(<SegmentTrack segments={SEGS} pxPerSec={0} duration={10} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders every segment for ordinary jobs instead of clipping to resize metrics', () => {
    const segments = Array.from({ length: 200 }, (_, id) => ({
      id,
      start: id,
      end: id + 0.5,
      text: `line ${id}`,
    }));
    setup({ segments, duration: 200, pxPerSec: 100, scrollLeft: 10_000 });

    expect(screen.getAllByRole('option')).toHaveLength(200);
  });
});

describe('SegmentTrack — keyboard', () => {
  it('ArrowRight moves focus (no commit outside edit mode)', () => {
    const { onCommit, onSelectSeg } = setup();
    box(0).focus();
    fireEvent.keyDown(box(0), { key: 'ArrowRight' });
    expect(onSelectSeg).toHaveBeenCalledWith('3_a');
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('Enter + ArrowRight nudges start by 10ms; Ctrl = 100ms', () => {
    const { onCommit } = setup();
    box(1).focus();
    fireEvent.keyDown(box(1), { key: 'Enter' });
    fireEvent.keyDown(box(1), { key: 'ArrowRight' });
    expect(onCommit).toHaveBeenCalledWith('3_a', { start: 3.01, end: 5 }, { undo: true });
    // Segments prop is static here (mocked transport), so the next nudge
    // computes from the unchanged start=3.
    fireEvent.keyDown(box(1), { key: 'ArrowLeft', ctrlKey: true });
    expect(onCommit).toHaveBeenLastCalledWith('3_a', expect.objectContaining({ start: 2.9 }), {
      undo: false,
    });
  });

  it('one undo per focus session — only the FIRST nudge passes undo:true', () => {
    const { onCommit } = setup();
    box(0).focus();
    fireEvent.keyDown(box(0), { key: 'Enter' });
    fireEvent.keyDown(box(0), { key: 'ArrowRight' });
    fireEvent.keyDown(box(0), { key: 'ArrowRight' });
    fireEvent.keyDown(box(0), { key: 'ArrowRight' });
    const undoFlags = onCommit.mock.calls.map((c) => c[2].undo);
    expect(undoFlags).toEqual([true, false, false]);
  });

  it('Shift nudges the end edge, Alt moves the whole segment', () => {
    const { onCommit } = setup();
    box(0).focus();
    fireEvent.keyDown(box(0), { key: 'Enter' });
    fireEvent.keyDown(box(0), { key: 'ArrowLeft', shiftKey: true });
    expect(onCommit).toHaveBeenLastCalledWith('1', { start: 0, end: 1.99 }, expect.anything());
    fireEvent.keyDown(box(0), { key: 'ArrowRight', altKey: true });
    const [, patch] = onCommit.mock.calls.at(-1);
    // Alt move preserves the (unchanged prop) segment's duration of 2.0.
    expect(patch.end - patch.start).toBeCloseTo(2.0, 5);
  });

  it('Escape leaves edit mode; arrows go back to roving focus', () => {
    const { onCommit, onSelectSeg } = setup();
    box(0).focus();
    fireEvent.keyDown(box(0), { key: 'Enter' });
    fireEvent.keyDown(box(0), { key: 'Escape' });
    onSelectSeg.mockClear();
    fireEvent.keyDown(box(0), { key: 'ArrowRight' });
    expect(onSelectSeg).toHaveBeenCalledWith('3_a');
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('Delete removes the focused segment', () => {
    const { onDelete } = setup();
    box(1).focus();
    fireEvent.keyDown(box(1), { key: 'Delete' });
    expect(onDelete).toHaveBeenCalledWith('3_a');
  });

  it('S snaps the start edge to the nearest onset as its own undo gesture', () => {
    const { onCommit } = setup();
    box(1).focus(); // start=3, nearest onset 3.1
    fireEvent.keyDown(box(1), { key: 's' });
    expect(onCommit).toHaveBeenCalledWith('3_a', { start: 3.1, end: 5 }, { undo: true });
  });

  it('keyboard edits are blocked while disabled', () => {
    const { onCommit, onDelete } = setup({ disabled: true });
    box(0).focus();
    fireEvent.keyDown(box(0), { key: 'Enter' });
    fireEvent.keyDown(box(0), { key: 'ArrowRight' });
    fireEvent.keyDown(box(0), { key: 'Delete' });
    expect(onCommit).not.toHaveBeenCalled();
    expect(onDelete).not.toHaveBeenCalled();
  });

  it('announces commits via the polite live region', () => {
    setup();
    box(0).focus();
    fireEvent.keyDown(box(0), { key: 'Enter' });
    fireEvent.keyDown(box(0), { key: 'ArrowRight' });
    const live = screen.getByRole('status');
    expect(live).toHaveAttribute('aria-live', 'polite');
    expect(live.textContent).not.toBe('');
  });
});

describe('SegmentTrack — compositor-safe positioning (#373)', () => {
  const baseProps = {
    segments: SEGS,
    pxPerSec: 100,
    duration: 10,
    currentTime: 0,
    onsets: [],
  };

  it('the lane never carries a transform — at rest and after a scroll update', () => {
    // Regression guard for #373: an animated `translateX` on the lane gets
    // composited by Chromium during playback and its boxes flash on some
    // Windows GPU/WebView2 drivers. Any reintroduction must fail here.
    const { rerender } = render(<SegmentTrack {...baseProps} scrollLeft={0} />);
    expect(screen.getByRole('listbox').style.transform).toBe('');
    // Simulate a WaveSurfer autoscroll tick during playback.
    rerender(<SegmentTrack {...baseProps} scrollLeft={250} />);
    expect(screen.getByRole('listbox').style.transform).toBe('');
  });

  it('boxes are laid out in viewport coordinates: left = start·pxPerSec − scrollLeft', () => {
    setup({ scrollLeft: 250 });
    expect(box(0).style.left).toBe('-250px'); // start 0
    expect(box(1).style.left).toBe('50px'); // start 3 → 300 − 250
    expect(box(2).style.left).toBe('250px'); // start 5 → 500 − 250
  });

  it('selfScroll fallback keeps lane coordinates — viewport scroll must not double-shift boxes', () => {
    setup({ selfScroll: true });
    const lane = screen.getByRole('listbox');
    const viewport = lane.parentElement;
    // jsdom has no layout: stub the scroll position, then fire the event the
    // component listens to.
    Object.defineProperty(viewport, 'scrollLeft', { value: 120, configurable: true });
    fireEvent.scroll(viewport);
    expect(lane.style.transform).toBe('');
    expect(box(1).style.left).toBe('300px'); // lane coords: 3s · 100px/s
  });
});

describe('SegmentTrack — engine-independent box paint (#963)', () => {
  const root = document.documentElement;

  afterEach(async () => {
    // Restore the default theme and let the palette observer settle so the
    // module-level cache can't leak into other tests in this file.
    await act(async () => {
      root.style.removeProperty('--chrome-bg');
      root.removeAttribute('data-theme');
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  });

  it('inline background is a literal opaque rgb() — no color-mix/var() the CSSOM could reject', () => {
    // WebView2/Chromium < 111 rejects a color-mix() inline-style assignment
    // wholesale, and .seg-track__box declares no fallback background → the
    // boxes rendered fully transparent (#963). The inline value must be
    // plain rgb() so every engine parses it.
    setup();
    for (const el of screen.getAllByRole('option')) {
      expect(el.style.background).toMatch(/^rgb\(\d{1,3}, \d{1,3}, \d{1,3}\)$/);
    }
    // Default theme, first palette slot: 0.45·rgb(211,134,155) over #0f1011.
    expect(box(0).style.background).toBe('rgb(103, 69, 79)');
  });

  it('boxes re-blend live when the theme changes ([data-theme] on <html>)', async () => {
    setup();
    expect(box(0).style.background).toBe('rgb(103, 69, 79)');
    await act(async () => {
      // Same seam App.jsx uses: swap --chrome-bg and flag the theme.
      root.style.setProperty('--chrome-bg', '#1e293b');
      root.setAttribute('data-theme', 'slate');
      await new Promise((resolve) => setTimeout(resolve, 0)); // flush MutationObserver
    });
    // round(0.45·[211,134,155] + 0.55·[30,41,59])
    expect(box(0).style.background).toBe('rgb(111, 83, 102)');
  });
});

describe('SegmentTrack — pointer + selection', () => {
  it('pointerdown selects the segment (table sync)', () => {
    const { onSelectSeg } = setup();
    fireEvent.pointerDown(box(2), { button: 0, clientX: 550 });
    expect(onSelectSeg).toHaveBeenCalledWith('3_b');
  });

  it('a drag commits ONCE on pointerup with undo:true', () => {
    const { onCommit } = setup();
    const el = box(1); // start 3, end 5 @ 100px/s
    fireEvent.pointerDown(el, { button: 0, clientX: 400 });
    fireEvent.pointerMove(el, { clientX: 405, altKey: true }); // Alt: no snap
    fireEvent.pointerMove(el, { clientX: 410, altKey: true });
    expect(onCommit).not.toHaveBeenCalled(); // live drag stays local
    fireEvent.pointerUp(el);
    expect(onCommit).toHaveBeenCalledTimes(1);
    const [id, patch, opts] = onCommit.mock.calls[0];
    expect(id).toBe('3_a');
    expect(patch.start).toBeCloseTo(3.1, 2);
    expect(patch.end).toBeCloseTo(5.1, 2);
    expect(opts).toEqual({ undo: true });
  });

  it('Alt-drag past the clamp allows at most 200ms overlap', () => {
    const { onCommit } = setup();
    const el = box(1); // next segment starts at 5
    fireEvent.pointerDown(el, { button: 0, clientX: 400 });
    fireEvent.pointerMove(el, { clientX: 700, altKey: true }); // try +3s
    fireEvent.pointerUp(el);
    const [, patch] = onCommit.mock.calls[0];
    expect(patch.end).toBeCloseTo(5.2, 3); // next.start + MAX_OVERLAP
    expect(patch.start).toBeCloseTo(3.2, 3);
  });

  it('drag without snap clamps at the neighbour boundary', () => {
    const { onCommit } = setup();
    const el = box(1); // prev ends at 2
    fireEvent.pointerDown(el, { button: 0, clientX: 400 });
    fireEvent.pointerMove(el, { clientX: 100, altKey: false }); // try to move to 0
    fireEvent.pointerUp(el);
    const [, patch] = onCommit.mock.calls[0];
    expect(patch.start).toBeCloseTo(2, 3); // hard non-overlap clamp
  });

  it('double-click plays the slot on the main player', () => {
    const { onPlayRange } = setup();
    fireEvent.doubleClick(box(0));
    expect(onPlayRange).toHaveBeenCalledWith(0, 2);
  });

  it('no gestures while disabled', () => {
    const { onCommit } = setup({ disabled: true });
    const el = box(1);
    fireEvent.pointerDown(el, { button: 0, clientX: 400 });
    fireEvent.pointerMove(el, { clientX: 450 });
    fireEvent.pointerUp(el);
    expect(onCommit).not.toHaveBeenCalled();
  });
});
