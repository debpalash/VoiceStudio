import { Popover } from '@base-ui/react/popover';
import { defaultRangeExtractor, useVirtualizer } from '@tanstack/react-virtual';
import { Check, ChevronDown, ChevronRight, Search, X } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  layoutMenu,
  MORE_ROW,
  rankOptions,
  type MenuRow,
  type SearchOption,
} from '@/lib/language-search';

export interface LanguageMenuProps {
  value: string;
  options: SearchOption[];
  onChange: (value: string) => void;
  ariaLabel: string;
  disabled?: boolean;
  buttonClassName?: string;
  renderOption?: (option: SearchOption) => ReactNode;
  renderValue?: (option: SearchOption | undefined) => ReactNode;
  header?: ReactNode;
  footer?: ReactNode;
  unavailableLabel?: string;
  recentsKey?: string;
}

function readRecents(key: string) {
  if (!key) return [];
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(value)
      ? value.filter((item): item is string => typeof item === 'string').slice(0, 4)
      : [];
  } catch {
    return [];
  }
}

const HEIGHT = 38;
const HEADER_HEIGHT = 28;
const LIST_MAX_HEIGHT = 320;
const rowHeight = (row: MenuRow) =>
  row.kind === 'header' || row.kind === 'family' ? HEADER_HEIGHT : HEIGHT;

/** Virtualized, searchable language menu with recents and collapsed unsupported languages. */
export function LanguageMenu({
  value,
  options,
  onChange,
  ariaLabel,
  disabled = false,
  buttonClassName = '',
  renderOption,
  renderValue,
  header,
  footer,
  unavailableLabel,
  recentsKey = '',
}: LanguageMenuProps) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [activeValue, setActiveValue] = useState(value);
  const [recents, setRecents] = useState(() => readRecents(recentsKey));
  const [list, setList] = useState<HTMLDivElement | null>(null);
  const [columns, setColumns] = useState(1);
  const input = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  const rtl = i18n.dir() === 'rtl';
  const current = options.find((item) => item.value === value);
  const unavailable = unavailableLabel || t('languagePicker.unavailable');
  const sorted = useMemo(() => rankOptions(options, query, recents), [options, query, recents]);
  const { rows, navigation, size } = useMemo(
    () =>
      layoutMenu({
        sorted,
        query,
        recents,
        columns,
        // A saved unsupported choice stays visible instead of hiding behind the toggle.
        expanded,
        toggle: !current?.disabled,
        labels: {
          recent: t('languagePicker.recent'),
          available: t('languagePicker.available'),
          unavailable,
        },
      }),
    [sorted, query, recents, columns, expanded, current?.disabled, unavailable, t],
  );
  const enabledCount = sorted.filter((item) => !item.disabled).length;
  const active = navigation.includes(activeValue) ? activeValue : navigation[0];
  const activeRow = rows.findIndex((row) =>
    row.kind === 'more'
      ? active === MORE_ROW
      : row.kind === 'items' && row.items.some((item) => item.value === active),
  );
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => list,
    enabled: open && !disabled,
    estimateSize: (index) => rowHeight(rows[index]),
    getItemKey: (index) => rows[index].key,
    overscan: 3,
    rangeExtractor: (range) =>
      [...new Set([...defaultRangeExtractor(range), ...(activeRow >= 0 ? [activeRow] : [])])].sort(
        (a, b) => a - b,
      ),
  });
  useEffect(() => {
    if (!list) return;
    const measure = () => setColumns(Math.max(1, Math.min(3, Math.floor(list.clientWidth / 200))));
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(list);
    return () => observer.disconnect();
  }, [list]);
  useEffect(() => {
    virtualizer.measure();
  }, [columns, rows, virtualizer]);
  useEffect(() => {
    if (open && activeRow >= 0) virtualizer.scrollToIndex(activeRow, { align: 'auto' });
  }, [activeRow, open, columns, virtualizer]);
  useEffect(() => {
    if (disabled) {
      setOpen(false);
      setQuery('');
    }
  }, [disabled]);
  const changeOpen = (next: boolean) => {
    setOpen(next && !disabled);
    setQuery('');
    if (next) {
      setActiveValue(value);
      setExpanded(false);
      setRecents(readRecents(recentsKey));
    }
  };
  const commit = (selected: string) => {
    if (selected === MORE_ROW) {
      setExpanded((previous) => !previous);
      setActiveValue(MORE_ROW);
      input.current?.focus();
      return;
    }
    const option = options.find((item) => item.value === selected && !item.disabled);
    // Re-read props, including after a model switch while the popup is open.
    if (disabled || !option) return;
    onChange(option.value);
    if (recentsKey && !option.pinned) {
      const next = [option.value, ...recents.filter((item) => item !== option.value)].slice(0, 4);
      try {
        localStorage.setItem(recentsKey, JSON.stringify(next));
      } catch {
        /* private browsing */
      }
      setRecents(next);
    }
    changeOpen(false);
    trigger.current?.focus();
  };
  const optionId = (key: string) => `${id}-${encodeURIComponent(key)}`;
  const rowClass = (key: string) =>
    `my-0.5 flex min-w-0 items-center gap-2 rounded-md px-2 text-start text-[13px] outline-none disabled:cursor-not-allowed disabled:text-muted-foreground ${active === key ? 'bg-accent ring-1 ring-inset ring-border' : 'enabled:hover:bg-muted'}`;
  const renderRow = (row: MenuRow) => {
    if (row.kind === 'header' || row.kind === 'family')
      return (
        <div
          className={`flex h-full items-end pb-1 text-muted-foreground ${row.kind === 'header' ? 'px-2 text-[11px] font-medium' : 'ps-3 pe-2 text-[11px]'}`}
          aria-hidden="true"
        >
          <span className="truncate">{row.label}</span>
        </div>
      );
    if (row.kind === 'more') {
      const label = row.expanded
        ? t('languagePicker.hideUnsupported')
        : t('languagePicker.showAll');
      return (
        <button
          type="button"
          role="option"
          id={optionId(MORE_ROW)}
          aria-label={label}
          aria-selected={false}
          aria-description={unavailable}
          aria-posinset={row.position + 1}
          aria-setsize={size}
          tabIndex={-1}
          className={`${rowClass(MORE_ROW)} h-[34px] w-full text-muted-foreground`}
          onPointerMove={(event) => {
            if (event.pointerType === 'mouse') setActiveValue(MORE_ROW);
          }}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => commit(MORE_ROW)}
        >
          <ChevronRight
            className={`size-4 shrink-0 transition-transform ${row.expanded ? 'rotate-90' : rtl ? 'rotate-180' : ''}`}
            aria-hidden="true"
          />
          <span className="min-w-0 flex-1 truncate">{label}</span>
          {!row.expanded && (
            <span className="shrink-0 rounded-full bg-muted px-1.5 text-[11px] tabular-nums">
              +{row.hidden}
            </span>
          )}
        </button>
      );
    }
    return (
      <div
        className="grid h-full gap-0.5"
        style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
      >
        {row.items.map((option, index) => (
          <button
            type="button"
            role="option"
            key={option.value}
            id={optionId(option.value)}
            aria-label={option.label}
            aria-selected={option.value === value}
            aria-disabled={Boolean(option.disabled)}
            aria-description={option.disabled ? unavailable : undefined}
            aria-posinset={row.start + index + 1}
            aria-setsize={size}
            disabled={option.disabled}
            tabIndex={-1}
            className={rowClass(option.value)}
            onPointerMove={(event) => {
              if (event.pointerType === 'mouse' && !option.disabled) setActiveValue(option.value);
            }}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => commit(option.value)}
          >
            {renderOption ? (
              renderOption(option)
            ) : (
              <span className="min-w-0 flex-1 truncate">{option.label}</span>
            )}
            <span className="w-3 shrink-0">
              {option.value === value && (
                <Check className="size-3.5 text-primary" aria-hidden="true" />
              )}
            </span>
          </button>
        ))}
      </div>
    );
  };
  return (
    <Popover.Root open={open && !disabled} onOpenChange={changeOpen}>
      <Popover.Trigger
        ref={trigger}
        disabled={disabled}
        aria-label={ariaLabel}
        aria-describedby={current?.disabled ? `${id}-invalid` : undefined}
        className={`inline-flex h-9 min-w-0 items-center gap-2 rounded-lg px-2.5 text-sm outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${buttonClassName}`}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            changeOpen(true);
          }
        }}
      >
        {renderValue ? renderValue(current) : current?.label || value}
        <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
      </Popover.Trigger>
      {current?.disabled && (
        <span id={`${id}-invalid`} className="sr-only">
          {t('languagePicker.chooseSupported')}
        </span>
      )}
      <Popover.Portal>
        <Popover.Positioner
          sideOffset={6}
          align="start"
          collisionPadding={12}
          className="z-[1000] isolate outline-none"
        >
          <Popover.Popup
            initialFocus={input}
            aria-label={ariaLabel}
            data-slot="language-menu"
            className="flex max-h-[min(440px,var(--available-height))] w-[min(640px,calc(100vw-24px))] flex-col overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-xl outline-none"
            dir={rtl ? 'rtl' : 'ltr'}
          >
            <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 transition-colors focus-within:bg-muted/30">
              <Search className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <input
                ref={input}
                role="combobox"
                aria-expanded="true"
                aria-controls={`${id}-list`}
                aria-autocomplete="list"
                aria-activedescendant={active ? optionId(active) : undefined}
                aria-label={ariaLabel}
                aria-describedby={`${id}-status`}
                autoComplete="off"
                spellCheck={false}
                placeholder={t('clone.search_languages', { count: options.length })}
                value={query}
                className="h-10 w-full min-w-0 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground"
                onChange={(event) => {
                  setQuery(event.target.value);
                  setActiveValue('');
                  if (list) list.scrollTop = 0;
                }}
                onKeyDown={(event) => {
                  if (event.nativeEvent.isComposing || event.keyCode === 229) return;
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    if (active) commit(active);
                    return;
                  }
                  // Keep native caret movement while editing. Alt+Left/Right moves between columns.
                  const horizontal =
                    event.altKey && (event.key === 'ArrowLeft' || event.key === 'ArrowRight');
                  if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || horizontal) {
                    event.preventDefault();
                    const index = Math.max(0, navigation.indexOf(active ?? ''));
                    // A visual grid remains a listbox: every enabled row is reachable in reading order.
                    const step = horizontal
                      ? (event.key === 'ArrowRight') !== rtl
                        ? 1
                        : -1
                      : event.key === 'ArrowDown'
                        ? 1
                        : -1;
                    const next =
                      navigation[Math.max(0, Math.min(navigation.length - 1, index + step))];
                    if (next) setActiveValue(next);
                  }
                }}
              />
              {query && (
                <button
                  type="button"
                  aria-label={t('languagePicker.clearSearch')}
                  className="flex size-9 shrink-0 items-center justify-center rounded-md hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => {
                    setQuery('');
                    setActiveValue(value);
                    input.current?.focus();
                  }}
                >
                  <X className="size-4" aria-hidden="true" />
                </button>
              )}
            </div>
            <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 pt-2 pb-1 text-[11px] text-muted-foreground">
              {header}
              <span id={`${id}-status`} role="status" aria-live="polite" aria-atomic="true">
                {t('languagePicker.results', { enabled: enabledCount, total: sorted.length })}
              </span>
            </div>
            <div
              ref={setList}
              id={`${id}-list`}
              role="listbox"
              tabIndex={-1}
              aria-label={ariaLabel}
              data-columns={columns}
              className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-1 pb-1"
              style={{
                // A max, not a fixed height: the popup shrinks the list to the
                // space the positioner has, so the search field never clips.
                maxHeight:
                  Math.min(
                    LIST_MAX_HEIGHT,
                    rows.reduce((height, row) => height + rowHeight(row), 0),
                  ) || HEIGHT,
              }}
            >
              {!rows.length && (
                <p className="p-4 text-sm text-muted-foreground">
                  {t('clone.no_language_match', { query })}
                </p>
              )}
              <div className="relative" style={{ height: virtualizer.getTotalSize() }}>
                {virtualizer.getVirtualItems().map((virtual) => (
                  <div
                    key={virtual.key}
                    className="absolute inset-x-0 top-0"
                    style={{ height: virtual.size, transform: `translateY(${virtual.start}px)` }}
                  >
                    {renderRow(rows[virtual.index])}
                  </div>
                ))}
              </div>
            </div>
            {footer && (
              <div className="shrink-0 border-t border-border px-3 py-1.5 text-[11px] text-muted-foreground">
                {footer}
              </div>
            )}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
