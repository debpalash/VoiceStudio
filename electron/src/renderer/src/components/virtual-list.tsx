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
    getItemKey: (index) => (items[index] ? getKey(items[index]) : index),
  });
  useEffect(() => {
    if (!virtual || activeKey == null) return;
    const index = items.findIndex((item) => getKey(item) === activeKey);
    if (index >= 0) virtualizer.scrollToIndex(index, { align: 'auto' });
    // Only a changed active row should scroll; list updates must not fight the user.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey, virtual]);

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
        const item = items[row.index];
        if (!item) return null;
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
