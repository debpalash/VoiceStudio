import { useVirtualizer } from '@tanstack/react-virtual';
import {
  useEffect,
  useLayoutEffect,
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
  /** Card content; the wrapper supplies the list item. */
  renderCard: (item: T, index: number) => ReactNode;
  /** Must match the CSS grid it replaces: min card width and gap, in px. */
  minCardWidth?: number;
  gap?: number;
  estimateRowSize?: number;
  threshold?: number;
  'aria-label'?: string;
}

/**
 * A responsive card grid that mounts only the visible rows once it grows past
 * `threshold`. Columns follow the container width with the same rule as the
 * `auto-fill` CSS grid it stands in for; rows are measured so cards keep their height.
 */
export function VirtualGrid<T>({
  items,
  scrollRef,
  getKey,
  renderCard,
  minCardWidth = 280,
  gap = 12,
  estimateRowSize = 180,
  threshold = VIRTUALIZE_LIST_ABOVE,
  'aria-label': ariaLabel,
}: VirtualGridProps<T>) {
  const virtual = items.length > threshold;
  const listRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [margin, setMargin] = useState(0);
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
  useLayoutEffect(() => {
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
    // The root element changes (ul ↔ div) when the list crosses the threshold:
    // observe the one now mounted.
  }, [virtual]);

  const columns = gridColumns(width, minCardWidth, gap);
  const rowCount = Math.ceil(items.length / columns);
  const virtualizer = useVirtualizer({
    count: virtual ? rowCount : 0,
    getScrollElement: () => (mounted ? scrollRef.current : null),
    estimateSize: () => estimateRowSize,
    overscan: 3,
    gap,
    scrollMargin: margin,
    getItemKey: (row) => `row-${row}-${columns}`,
  });

  if (!virtual) {
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
  }
  return (
    <div
      ref={listRef}
      role="list"
      aria-label={ariaLabel}
      className="relative w-full"
      style={{ height: virtualizer.getTotalSize() }}
    >
      {virtualizer.getVirtualItems().map((row) => {
        const start = row.index * columns;
        if (start >= items.length) return null;
        const slice = items.slice(start, start + columns);
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
            {slice.map((item, offset) => (
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
