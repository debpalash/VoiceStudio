import { Popover } from '@base-ui/react/popover';
import { defaultRangeExtractor, useVirtualizer } from '@tanstack/react-virtual';
import { Check, ChevronDown, Search, X } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export interface SearchOption {
  value: string;
  label: string;
  search: string[];
  disabled?: boolean;
  pinned?: boolean;
}

export interface VirtualSelectProps {
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

export const normalizeSearch = (value: string) =>
  value.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase().trim();

export function rankOptions(options: SearchOption[], query: string, recents: string[]) {
  const normalized = normalizeSearch(query);
  const terms = normalized.split(/\s+/).filter(Boolean);
  return options
    .map((option, order) => {
      const fields = option.search;
      const exact = fields.some((field) => field === normalized);
      const matches = terms.every((term) => fields.some((field) => field.includes(term)));
      const prefix = fields.some((field) => field.startsWith(normalized));
      const recent = recents.indexOf(option.value);
      return {
        option,
        order,
        matches,
        score: terms.length
          ? exact
            ? 0
            : prefix
              ? 1
              : 2
          : option.pinned
            ? -2
            : recent < 0
              ? 10
              : recent,
      };
    })
    .filter((item) => item.matches)
    .sort(
      (a, b) =>
        Number(Boolean(a.option.disabled)) - Number(Boolean(b.option.disabled)) ||
        a.score - b.score ||
        a.order - b.order,
    )
    .map(({ option }) => option);
}

function readRecents(key: string) {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(value)
      ? value.filter((item): item is string => typeof item === 'string').slice(0, 4)
      : [];
  } catch {
    return [];
  }
}

type Row = { label: string } | { items: SearchOption[]; start: number };
const HEIGHT = 38;
const HEADER_HEIGHT = 28;
const LIST_MAX_HEIGHT = 320;

/** Opt-in virtual surface for SearchableSelect; classic consumers retain their contract. */
export function VirtualSearchableSelect({
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
}: VirtualSelectProps) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeValue, setActiveValue] = useState(value);
  const [recents, setRecents] = useState(() => readRecents(recentsKey));
  const [list, setList] = useState<HTMLDivElement | null>(null);
  const [columns, setColumns] = useState(1);
  const input = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  const rtl = i18n.dir() === 'rtl';
  const current = options.find((item) => item.value === value);
  const sorted = useMemo(() => rankOptions(options, query, recents), [options, query, recents]);
  const enabled = sorted.filter((item) => !item.disabled);
  const active = enabled.find((item) => item.value === activeValue) ?? enabled[0];
  const rows = useMemo(() => {
    const result: Row[] = [];
    let position = 0;
    for (const isDisabled of [false, true]) {
      const group = sorted.filter((item) => Boolean(item.disabled) === isDisabled);
      if (!group.length) continue;
      if (isDisabled) result.push({ label: unavailableLabel || t('languagePicker.unavailable') });
      for (let index = 0; index < group.length; index += columns) {
        result.push({ items: group.slice(index, index + columns), start: position });
        position += Math.min(columns, group.length - index);
      }
    }
    return result;
  }, [sorted, columns, unavailableLabel, t]);
  const activeRow = rows.findIndex(
    (row) => 'items' in row && row.items.some((item) => item.value === active?.value),
  );
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => list,
    enabled: open && !disabled,
    estimateSize: (index) => ('label' in rows[index] ? HEADER_HEIGHT : HEIGHT),
    getItemKey: (index) =>
      'label' in rows[index] ? 'disabled-header' : rows[index].items[0].value,
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
      setRecents(readRecents(recentsKey));
    }
  };
  const commit = (option: SearchOption) => {
    // Re-read props, including after a model switch while the popup is open.
    if (disabled || !options.some((item) => item.value === option.value && !item.disabled)) return;
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
  const optionId = (option: SearchOption) => `${id}-${encodeURIComponent(option.value)}`;
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
                    const index = Math.max(
                      0,
                      enabled.findIndex((item) => item.value === active?.value),
                    );
                    // A visual grid remains a listbox: every enabled option is reachable in reading order.
                    const step = horizontal
                      ? (event.key === 'ArrowRight') !== rtl
                        ? 1
                        : -1
                      : event.key === 'ArrowDown'
                        ? 1
                        : -1;
                    const next = enabled[Math.max(0, Math.min(enabled.length - 1, index + step))];
                    if (next) setActiveValue(next.value);
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
                {t('languagePicker.results', { enabled: enabled.length, total: sorted.length })}
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
                    rows.reduce(
                      (height, row) => height + ('label' in row ? HEADER_HEIGHT : HEIGHT),
                      0,
                    ),
                  ) || HEIGHT,
              }}
            >
              {!rows.length && (
                <p className="p-4 text-sm text-muted-foreground">
                  {t('clone.no_language_match', { query })}
                </p>
              )}
              <div className="relative" style={{ height: virtualizer.getTotalSize() }}>
                {virtualizer.getVirtualItems().map((virtual) => {
                  const row = rows[virtual.index];
                  return (
                    <div
                      key={virtual.key}
                      className="absolute inset-x-0 top-0"
                      style={{ height: virtual.size, transform: `translateY(${virtual.start}px)` }}
                    >
                      {'label' in row ? (
                        <div
                          className="flex h-full items-center px-2 text-xs text-muted-foreground"
                          aria-hidden="true"
                        >
                          {row.label}
                        </div>
                      ) : (
                        <div
                          className="grid h-full gap-0.5"
                          style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
                        >
                          {row.items.map((option, index) => (
                            <button
                              type="button"
                              role="option"
                              key={option.value}
                              id={optionId(option)}
                              aria-label={option.label}
                              aria-selected={option.value === value}
                              aria-disabled={Boolean(option.disabled)}
                              aria-description={
                                option.disabled
                                  ? unavailableLabel || t('languagePicker.unavailable')
                                  : undefined
                              }
                              aria-posinset={row.start + index + 1}
                              aria-setsize={sorted.length}
                              disabled={option.disabled}
                              tabIndex={-1}
                              className={`my-0.5 flex min-w-0 items-center gap-2 rounded-md px-2 text-start text-[13px] outline-none disabled:cursor-not-allowed disabled:text-muted-foreground ${active?.value === option.value ? 'bg-accent ring-1 ring-inset ring-border' : 'enabled:hover:bg-muted'} `}
                              onPointerMove={(event) => {
                                if (event.pointerType === 'mouse' && !option.disabled)
                                  setActiveValue(option.value);
                              }}
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => commit(option)}
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
                      )}
                    </div>
                  );
                })}
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
