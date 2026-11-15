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
  /** Card content; the wrapper supplies the list item. */
  renderCard: (item: T, index: number) => ReactNode;
  /** Must match the CSS grid it replaces: min card width and gap, in px. */
  minCardWidth?: number;
  gap?: number;
  estimateRowSize?: number;
  threshold?: number;
  /** Optional viewport-driven column count; keeps the same row DOM for short and long lists. */
  columnCount?: number;
  /** Retain the current media/control owner even outside the visible window. */
  pinnedKey?: Key | null;
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
  columnCount,
  pinnedKey,
  'aria-label': ariaLabel,
}: VirtualGridProps<T>) {
  const virtual = items.length > threshold;
  const controlled = columnCount !== undefined || pinnedKey !== undefined;
  const listRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [margin, setMargin] = useState(0);
  const [mounted, setMounted] = useState(false);
  const [focusedKey, setFocusedKey] = useState<Key | null>(null);
  const focusOwner = useRef<{ key: Key; node: HTMLElement; path: number[] } | null>(null);
  const priorLayout = useRef({
    columns: 0,
    keys: [] as Key[],
    generation: 0,
    rows: new Map<number, Key>(),
  });
  useLayoutEffect(() => {
    if (virtual || !controlled) setMounted(true);
    const scroller = scrollRef.current;
    const list = listRef.current;
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
    // The root element changes (ul ↔ div) when the list crosses the threshold:
    // observe the one now mounted.
  }, [virtual, columnCount]);

  const columns =
    columnCount === undefined
      ? gridColumns(width, minCardWidth, gap)
      : Math.max(1, Math.floor(columnCount));
  const keys = useMemo(() => (controlled ? items.map(getKey) : []), [controlled, items, getKey]);
  const previous = priorLayout.current;
  const generation =
    previous.columns === columns &&
    previous.keys.length === keys.length &&
    previous.keys.every((key, index) => key === keys[index])
      ? previous.generation
      : previous.generation + 1;
  const retainedRows = useMemo(() => {
    const rows = new Set<number>();
    for (const key of [pinnedKey, focusedKey]) {
      if (key == null) continue;
      const index = keys.indexOf(key);
      if (index >= 0) rows.add(Math.floor(index / columns));
    }
    return [...rows].sort((a, b) => a - b);
  }, [keys, columns, pinnedKey, focusedKey]);
  // The media-owning row keeps its React identity when appending pages or
  // changing columns moves it to another row. Nothing keeps visited cards alive.
  const retainedIdentities = new Map<number, Key>();
  const usedIdentities = new Set<Key>();
  for (const key of [pinnedKey, focusedKey]) {
    if (key == null) continue;
    const index = keys.indexOf(key),
      oldIndex = previous.keys.indexOf(key);
    if (index < 0 || oldIndex < 0) continue;
    const row = Math.floor(index / columns),
      oldRow = Math.floor(oldIndex / previous.columns);
    const identity = previous.rows.get(oldRow);
    if (identity !== undefined && !retainedIdentities.has(row) && !usedIdentities.has(identity)) {
      retainedIdentities.set(row, identity);
      usedIdentities.add(identity);
    }
  }
  const rowIdentity = (row: number) => retainedIdentities.get(row) ?? `grid-${generation}-${row}`;
  const rangeExtractor = useCallback(
    (range: Parameters<typeof defaultRangeExtractor>[0]) =>
      [...new Set([...defaultRangeExtractor(range), ...retainedRows])].sort((a, b) => a - b),
    [retainedRows],
  );
  const rowCount = Math.ceil(items.length / columns);
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
        }))
    : Array.from({ length: rowCount }, (_, index) => ({
        index,
        key: rowIdentity(index),
        start: 0,
      }));
  useLayoutEffect(() => {
    if (controlled)
      priorLayout.current = {
        columns,
        keys,
        generation,
        rows: new Map(rows.map((row) => [row.index, rowIdentity(row.index)])),
      };
    const focus = focusOwner.current;
    // Two retained cards can split out of one row on resize. Keep the media
    // row's identity, and restore the same control in the other card without
    // moving scroll or taking focus from another user-selected element.
    if (
      controlled &&
      focus &&
      !focus.node.isConnected &&
      document.activeElement === document.body
    ) {
      const index = keys.indexOf(focus.key);
      let node = listRef.current?.querySelector<HTMLElement>(`[data-grid-item="${index}"]`) ?? null;
      for (const child of focus.path) node = node?.children.item(child) as HTMLElement | null;
      node?.focus({ preventScroll: true });
    }
  });

  if (!virtual && !controlled) {
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
      className={virtual ? 'relative w-full' : 'grid gap-3'}
      style={virtual ? { height: virtualizer.getTotalSize() } : undefined}
      onFocusCapture={
        controlled
          ? (event) => {
              const item = (event.target as HTMLElement).closest<HTMLElement>('[data-grid-item]');
              const index = Number(item?.dataset.gridItem);
              if (item && index >= 0 && index < items.length) {
                const key = getKey(items[index] as T),
                  target = event.target as HTMLElement,
                  path: number[] = [];
                for (
                  let node = target;
                  node !== item && node.parentElement;
                  node = node.parentElement
                )
                  path.unshift([...node.parentElement.children].indexOf(node));
                focusOwner.current = { key, node: target, path };
                setFocusedKey(key);
              }
            }
          : undefined
      }
      onBlurCapture={
        controlled
          ? (event) => {
              if (
                !event.currentTarget.contains(event.relatedTarget as Node | null) &&
                (event.target as HTMLElement).isConnected
              ) {
                focusOwner.current = null;
                setFocusedKey(null);
              }
            }
          : undefined
      }
    >
      {rows.map((row) => {
        const start = row.index * columns;
        if (start >= items.length) return null;
        const slice = items.slice(start, start + columns);
        return (
          <div
            key={controlled ? rowIdentity(row.index) : row.key}
            role="presentation"
            ref={virtual ? virtualizer.measureElement : undefined}
            data-index={row.index}
            className={virtual ? 'absolute top-0 left-0 grid w-full' : 'grid w-full'}
            style={{
              gap,
              gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
              transform: virtual ? `translateY(${row.start - margin}px)` : undefined,
            }}
          >
            {slice.map((item, offset) => (
              <div
                key={getKey(item)}
                role="listitem"
                aria-setsize={items.length}
                aria-posinset={start + offset + 1}
                data-grid-item={start + offset}
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
