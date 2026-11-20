import { defaultRangeExtractor, useVirtualizer } from '@tanstack/react-virtual';
import {
  useEffect,
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type Key,
  type ReactNode,
  type RefObject,
} from 'react';

/** Lists at or below this many rows render fully; longer ones mount only a window. */
export const VIRTUALIZE_LIST_ABOVE = 100;

interface VirtualListProps<T> {
  items: readonly T[];
  /** The element that actually scrolls (the list sits inside it). */
  scrollRef: RefObject<HTMLElement | null>;
  getKey: (item: T) => Key;
  /** Row content; the wrapper supplies the `li`. */
  renderRow: (item: T, index: number) => ReactNode;
  estimateSize?: number;
  threshold?: number;
  /** Scrolls this row into view when it changes, since it may not be mounted. */
  activeKey?: Key | null;
  className?: string;
  rowClassName?: string;
  'aria-label'?: string;
}

/**
 * A list that stays a real `ul`/`li` list, mounting only the visible rows once it
 * grows past `threshold`. Rows are measured, so inline expansions (confirmations,
 * wrapped titles) keep their height. Small lists render every row unchanged.
 */
export function VirtualList<T>({
  items,
  scrollRef,
  getKey,
  renderRow,
  estimateSize = 48,
  threshold = VIRTUALIZE_LIST_ABOVE,
  activeKey,
  className,
  rowClassName,
  'aria-label': ariaLabel,
}: VirtualListProps<T>) {
  const virtual = items.length > threshold;
  const listRef = useRef<HTMLUListElement>(null);
  // The list may sit below a header inside the scroller; offset the window by it.
  const [margin, setMargin] = useState(0);
  // The scroller's ref attaches after this component's first layout effect, so the
  // virtualizer cannot see it yet; one extra render after mount attaches it.
  const [mounted, setMounted] = useState(false);
  useLayoutEffect(() => {
    setMounted(true);
    const scroller = scrollRef.current;
    const list = listRef.current;
    if (!scroller || !list) return;
    const next = Math.round(
      list.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop,
    );
    setMargin((current) => (current === next ? current : next));
  });
  const virtualizer = useVirtualizer({
    count: virtual ? items.length : 0,
    getScrollElement: () => (mounted ? scrollRef.current : null),
    estimateSize: () => estimateSize,
    overscan: 8,
    scrollMargin: margin,
    getItemKey: (index) => (index < items.length ? getKey(items[index] as T) : index),
  });
  useEffect(() => {
    // Wait for the scroller: a scroll issued before it attaches is dropped.
    if (!virtual || !mounted || activeKey == null) return;
    const index = items.findIndex((item) => getKey(item) === activeKey);
    if (index >= 0) virtualizer.scrollToIndex(index, { align: 'auto' });
    // Only a changed active row should scroll; list updates must not fight the user.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey, virtual, mounted]);

  if (!virtual) {
    return (
      <ul ref={listRef} aria-label={ariaLabel} className={className}>
        {items.map((item, index) => (
          <li key={getKey(item)} className={rowClassName}>
            {renderRow(item, index)}
          </li>
        ))}
      </ul>
    );
  }
  return (
    <ul
      ref={listRef}
      aria-label={ariaLabel}
      className="relative w-full"
      style={{ height: virtualizer.getTotalSize() }}
    >
      {virtualizer.getVirtualItems().map((row) => {
        if (row.index >= items.length) return null;
        const item = items[row.index] as T;
        return (
          <li
            key={row.key}
            ref={virtualizer.measureElement}
            data-index={row.index}
            aria-setsize={items.length}
            aria-posinset={row.index + 1}
            className={`absolute top-0 left-0 w-full ${rowClassName ?? ''}`}
            style={{ transform: `translateY(${row.start - margin}px)` }}
          >
            {renderRow(item, row.index)}
          </li>
        );
      })}
    </ul>
  );
}

/** Column count of `repeat(auto-fill, minmax(min(minWidth, 100%), 1fr))` at a width. */
export function gridColumns(width: number, minWidth: number, gap: number): number {
  if (!(width > 0)) return 1;
  return Math.max(1, Math.floor((width + gap) / (minWidth + gap)));
}

interface VirtualGridProps<T> {
  items: readonly T[];
  scrollRef: RefObject<HTMLElement | null>;
  getKey: (item: T) => Key;
  renderCard: (item: T, index: number) => ReactNode;
  minCardWidth?: number;
  gap?: number;
  estimateRowSize?: number;
  threshold?: number;
  /** Optional viewport-driven columns; controlled cards keep stable sibling keys. */
  columnCount?: number;
  /** Bounded live media owners; callers do not retain every visited card. */
  pinnedKeys?: readonly Key[];
  'aria-label'?: string;
}

/** Responsive cards with a windowed long-list mode and measured row heights. */
export function VirtualGrid<T>({
  items,
  scrollRef,
  getKey,
  renderCard,
  minCardWidth = 280,
  gap = 12,
  estimateRowSize = 180,
  threshold = VIRTUALIZE_LIST_ABOVE,
  columnCount,
  pinnedKeys,
  'aria-label': ariaLabel,
}: VirtualGridProps<T>) {
  const virtual = items.length > threshold;
  const controlled = columnCount !== undefined || pinnedKeys !== undefined;
  const listRef = useRef<HTMLDivElement>(null);
  const cardNodes = useRef(new Map<Key, HTMLDivElement>());
  // One observer for the grid's lifetime. Rebuilding it every render meant a new
  // ResizeObserver, a full observe() pass and a disconnect on every scroll frame,
  // for the whole list. The window changes far more often than the set of visible
  // rows does, so reconcile subscriptions instead of recreating the observer.
  const gridObserver = useRef<ResizeObserver | null>(null);
  const gridObserved = useRef(new Map<Element, number>());
  const [width, setWidth] = useState(0);
  const [margin, setMargin] = useState(0);
  const [mounted, setMounted] = useState(false);
  const [focusedKey, setFocusedKey] = useState<Key | null>(null);
  useLayoutEffect(() => {
    if (virtual || !controlled) setMounted(true);
    const scroller = scrollRef.current,
      list = listRef.current;
    if (!scroller || !list) return;
    const next = Math.round(
      list.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop,
    );
    setMargin((current) => (current === next ? current : next));
  });
  useLayoutEffect(() => {
    if (columnCount !== undefined) return;
    const list = listRef.current;
    if (!list) return;
    setWidth(list.offsetWidth);
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width;
      if (typeof next === 'number') setWidth(next);
    });
    observer.observe(list);
    return () => observer.disconnect();
  }, [virtual, columnCount]);
  const columns =
    columnCount === undefined
      ? gridColumns(width, minCardWidth, gap)
      : Math.max(1, Math.floor(columnCount));
  const keys = useMemo(() => (controlled ? items.map(getKey) : []), [controlled, items, getKey]);
  const rowCount = Math.ceil(items.length / columns);
  const retainedRows = useMemo(() => {
    const rows = new Set<number>();
    for (const key of pinnedKeys || []) {
      const index = keys.indexOf(key);
      if (index >= 0) rows.add(Math.floor(index / columns));
    }
    const focus = focusedKey == null ? -1 : keys.indexOf(focusedKey);
    if (focus >= 0) {
      const row = Math.floor(focus / columns);
      // Tab and Shift+Tab need the next/previous card before focus moves there.
      for (const index of [row - 1, row, row + 1])
        if (index >= 0 && index < rowCount) rows.add(index);
    }
    return [...rows].sort((a, b) => a - b);
  }, [keys, columns, pinnedKeys, focusedKey, rowCount]);
  const rangeExtractor = useCallback(
    (range: Parameters<typeof defaultRangeExtractor>[0]) =>
      [...new Set([...defaultRangeExtractor(range), ...retainedRows])].sort((a, b) => a - b),
    [retainedRows],
  );
  const virtualizer = useVirtualizer({
    count: virtual ? rowCount : 0,
    getScrollElement: () => (mounted ? scrollRef.current : null),
    estimateSize: () => estimateRowSize,
    overscan: 3,
    gap,
    scrollMargin: margin,
    getItemKey: (row) => `row-${row}-${columns}`,
    rangeExtractor,
  });
  const visibleRows = virtualizer.getVirtualItems();
  const rows = virtual
    ? visibleRows.length
      ? visibleRows
      : retainedRows.map((index) => ({
          index,
          key: `row-${index}-${columns}`,
          start: margin + index * (estimateRowSize + gap),
          end: margin + index * (estimateRowSize + gap) + estimateRowSize,
        }))
    : Array.from({ length: rowCount }, (_, index) => ({
        index,
        key: `row-${index}`,
        start: 0,
        end: 0,
      }));
  useLayoutEffect(() => {
    if (!controlled || !virtual) return;
    const elements = new Map<Element, number>();
    for (const row of rows) {
      const item = items[row.index * columns];
      if (item === undefined) continue;
      const node = cardNodes.current.get(getKey(item));
      if (node) elements.set(node, row.index);
    }
    // Grid items stretch to the tallest card in each row. Measuring one
    // stretched card keeps row heights without reparenting any media owner.
    const measure = (node: Element, index: number) => {
      // Browser focus/click preparation can scroll before its scroll event is
      // delivered. A size correction now would write the virtualizer's stale
      // offset and move the control between pointerdown and pointerup. The
      // observed scroll triggers another render/measurement with the new offset.
      const scroller = scrollRef.current;
      if (
        scroller &&
        virtualizer.scrollOffset !== null &&
        Math.abs(scroller.scrollTop - virtualizer.scrollOffset) > 0.5
      )
        return;
      const height = node.getBoundingClientRect().height;
      if (height > 0) virtualizer.resizeItem(index, height);
    };
    elements.forEach((index, node) => measure(node, index));
    if (typeof ResizeObserver === 'undefined') return;
    if (!gridObserver.current) {
      gridObserver.current = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const index = gridObserved.current.get(entry.target);
          if (index !== undefined) measure(entry.target, index);
        }
      });
    }
    const observer = gridObserver.current;
    const previouslyObserved = gridObserved.current;
    gridObserved.current = elements;
    previouslyObserved.forEach((_index, node) => {
      if (!elements.has(node)) observer.unobserve(node);
    });
    elements.forEach((_index, node) => {
      if (!previouslyObserved.has(node)) observer.observe(node);
    });
  });
  // The observer outlives every render, so unmount is the only place to release it.
  useEffect(
    () => () => {
      gridObserver.current?.disconnect();
      gridObserver.current = null;
      gridObserved.current = new Map();
    },
    [],
  );

  if (controlled) {
    const children: ReactNode[] = [];
    let previousRow = -1,
      previousEnd = 0;
    for (const row of rows) {
      if (virtual && row.index > previousRow + 1) {
        const height = row.start - margin - previousEnd - (previousRow < 0 ? gap : 2 * gap);
        children.push(
          <div
            key={`gap-${row.index}`}
            role="presentation"
            aria-hidden="true"
            style={{ gridColumn: '1 / -1', height: Math.max(0, height) }}
          />,
        );
      }
      const start = row.index * columns;
      for (let index = start; index < Math.min(items.length, start + columns); index++) {
        const item = items[index] as T,
          key = getKey(item);
        children.push(
          <div
            key={`card-${typeof key}-${key}`}
            role="listitem"
            aria-setsize={items.length}
            aria-posinset={index + 1}
            data-grid-item={index}
            className="min-w-0"
            ref={(node) => {
              if (node) cardNodes.current.set(key, node);
              else cardNodes.current.delete(key);
            }}
          >
            {renderCard(item, index)}
          </div>,
        );
      }
      previousRow = row.index;
      previousEnd = row.end - margin;
    }
    // With no rows yet (scroll element not resolved on first render) the tail
    // spacer alone holds the full height so the scroll position is not clamped.
    if (virtual && previousRow < rowCount - 1)
      children.push(
        <div
          key="gap-tail"
          role="presentation"
          aria-hidden="true"
          style={{
            gridColumn: '1 / -1',
            height: Math.max(
              0,
              virtualizer.getTotalSize() - previousEnd - (rows.length ? gap : 0),
            ),
          }}
        />,
      );
    return (
      <div
        ref={listRef}
        role="list"
        aria-label={ariaLabel}
        className="grid w-full"
        style={{ gap, gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
        onFocusCapture={(event) => {
          const item = (event.target as HTMLElement).closest<HTMLElement>('[data-grid-item]');
          const index = Number(item?.dataset.gridItem);
          if (item && index >= 0 && index < items.length) setFocusedKey(getKey(items[index] as T));
        }}
        onBlurCapture={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null))
            setFocusedKey(null);
        }}
      >
        {children}
      </div>
    );
  }
  // Existing uncontrolled callers retain their original markup and layout.
  if (!virtual)
    return (
      <ul
        ref={listRef as unknown as RefObject<HTMLUListElement>}
        aria-label={ariaLabel}
        className="grid grid-cols-[repeat(auto-fill,minmax(min(280px,100%),1fr))] gap-3"
      >
        {items.map((item, index) => (
          <li key={getKey(item)} className="min-w-0">
            {renderCard(item, index)}
          </li>
        ))}
      </ul>
    );
  return (
    <div
      ref={listRef}
      role="list"
      aria-label={ariaLabel}
      className="relative w-full"
      style={{ height: virtualizer.getTotalSize() }}
    >
      {visibleRows.map((row) => {
        const start = row.index * columns;
        if (start >= items.length) return null;
        return (
          <div
            key={row.key}
            role="presentation"
            ref={virtualizer.measureElement}
            data-index={row.index}
            className="absolute top-0 left-0 grid w-full"
            style={{
              gap,
              gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
              transform: `translateY(${row.start - margin}px)`,
            }}
          >
            {items.slice(start, start + columns).map((item, offset) => (
              <div
                key={getKey(item)}
                role="listitem"
                aria-setsize={items.length}
                aria-posinset={start + offset + 1}
                className="min-w-0"
              >
                {renderCard(item, start + offset)}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
