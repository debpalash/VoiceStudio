import { useDebouncedValue } from '@tanstack/react-pacer';
import { useVirtualizer } from '@tanstack/react-virtual';
import { CheckIcon, ChevronDownIcon, LanguagesIcon, SearchIcon, XIcon } from 'lucide-react';
import { useEffect, useId, useMemo, useState, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/popover';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { LANGUAGES, POPULAR_LANGUAGES } from '@/lib/languages';
import { isImeComposing } from '@/lib/ime';
import { setCloneSetting, useCloneSetting } from '@/lib/store/clone-settings';
import { cn } from '@/lib/utils';

type Row = { kind: 'header'; label: string } | { kind: 'item'; value: string };

const ROW_HEIGHT = 32;

function buildRows(
  query: string,
  popularLabel: string,
  allLabel: string,
  options: string[] = LANGUAGES,
): Row[] {
  const q = query.trim().toLowerCase();
  if (!q) {
    const popular = POPULAR_LANGUAGES.filter((l) => options.includes(l));
    return [
      { kind: 'header', label: popularLabel },
      ...popular.map((value): Row => ({ kind: 'item', value })),
      { kind: 'header', label: allLabel },
      ...options.map((value): Row => ({ kind: 'item', value })),
    ];
  }
  return options
    .filter((l) => l.toLowerCase().includes(q))
    .map((value): Row => ({
      kind: 'item',
      value,
    }));
}

export function LanguagePicker({
  value,
  onValueChange,
  options,
  supportedOptions,
  disabled = false,
  className,
}: {
  value?: string;
  onValueChange?: (value: string) => void;
  options?: string[];
  supportedOptions?: readonly string[] | null;
  disabled?: boolean;
  className?: string;
} = {}) {
  const { t } = useTranslation();
  const savedLanguage = useCloneSetting('language');
  const language = value ?? savedLanguage;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [debouncedQuery] = useDebouncedValue(query, { wait: 120 });
  const [active, setActive] = useState(0);
  const [listElement, setListElement] = useState<HTMLDivElement | null>(null);
  const listId = useId();

  const rows = useMemo(
    () =>
      buildRows(debouncedQuery, t('clone.popular_languages'), t('clone.all_languages'), options),
    [debouncedQuery, t, options],
  );

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => listElement,
    enabled: open,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  });

  const allowed = (value: string) =>
    value.toLowerCase() === 'auto' ||
    supportedOptions == null ||
    supportedOptions.includes(value.toLowerCase());
  const firstItem = rows.findIndex((r) => r.kind === 'item' && allowed(r.value));

  const supportedKey =
    supportedOptions == null ? null : JSON.stringify([...new Set(supportedOptions)].sort());

  useEffect(() => {
    if (!open) return;
    setActive(firstItem);
  }, [open, rows, firstItem, supportedKey]);

  const select = (value: string) => {
    if (disabled || !allowed(value)) return;
    if (onValueChange) onValueChange(value);
    else setCloneSetting('language', value);
    setOpen(false);
  };

  const move = (dir: 1 | -1) => {
    if (firstItem < 0) return;
    let next = active;
    for (let i = 0; i < rows.length; i += 1) {
      next = (next + dir + rows.length) % rows.length;
      const row = rows[next];
      if (row?.kind === 'item' && allowed(row.value)) break;
    }
    setActive(next);
    virtualizer.scrollToIndex(next, { align: 'auto' });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (isImeComposing(event)) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      move(1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      move(-1);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (query !== debouncedQuery) return;
      const row = rows[active];
      if (row?.kind === 'item') select(row.value);
    }
  };

  const activeRow = rows[active];
  const activeId = activeRow?.kind === 'item' ? `${listId}-${active}` : undefined;

  return (
    <Popover
      open={open && !disabled}
      onOpenChange={(next) => {
        setOpen(next && !disabled);
        if (!next) setQuery('');
      }}
    >
      <PopoverTrigger
        disabled={disabled}
        render={
          <Button
            disabled={disabled}
            variant="ghost"
            className={cn('h-9 min-w-0 justify-between gap-2 px-2.5 font-normal', className)}
            aria-label={t('clone.language')}
          />
        }
      >
        <LanguagesIcon className="text-muted-foreground" data-icon="inline-start" />
        <span className="min-w-0 flex-1 truncate text-left">{language}</span>
        <ChevronDownIcon className="text-muted-foreground" data-icon="inline-end" />
      </PopoverTrigger>
      <PopoverContent className="flex w-[min(340px,calc(100vw-32px))] flex-col overflow-hidden">
        <div className="relative border-b p-2">
          <SearchIcon
            className="pointer-events-none absolute top-1/2 left-4 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            autoFocus
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={activeId}
            aria-autocomplete="list"
            aria-label={t('clone.language')}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder={t('clone.search_languages', { count: LANGUAGES.length })}
            className="h-8 pl-7"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div
          ref={setListElement}
          id={listId}
          role="listbox"
          aria-label={t('clone.language')}
          className="max-h-[min(360px,60vh)] overflow-y-auto overscroll-contain p-1"
        >
          {rows.length === 0 ? (
            <p className="px-3 py-6 text-center text-[length:var(--text-label)] text-muted-foreground">
              {t('clone.no_language_match', { query: debouncedQuery.trim() })}
            </p>
          ) : (
            <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
              {virtualizer.getVirtualItems().map((item) => {
                const row = rows[item.index];
                if (!row) return null;
                const style = {
                  height: item.size,
                  transform: `translateY(${item.start}px)`,
                } as const;
                if (row.kind === 'header') {
                  return (
                    <div
                      key={item.key}
                      className="absolute top-0 left-0 flex w-full items-end px-2 pb-1 text-[length:var(--text-caption)] font-medium text-muted-foreground"
                      style={style}
                      aria-hidden="true"
                    >
                      {row.label}
                    </div>
                  );
                }
                const selected = row.value === language;
                const isActive = item.index === active;
                return (
                  <button
                    key={item.key}
                    type="button"
                    id={`${listId}-${item.index}`}
                    role="option"
                    aria-selected={selected}
                    aria-disabled={!allowed(row.value)}
                    disabled={!allowed(row.value)}
                    tabIndex={-1}
                    className={cn(
                      'absolute top-0 left-0 flex w-full items-center gap-2 rounded-md px-2 text-left text-sm outline-none disabled:cursor-not-allowed disabled:opacity-50',
                      isActive && 'bg-accent text-accent-foreground',
                      selected && 'font-medium',
                    )}
                    style={style}
                    onMouseMove={() => {
                      if (allowed(row.value)) setActive(item.index);
                    }}
                    onClick={() => select(row.value)}
                  >
                    <span className="min-w-0 flex-1 truncate">{row.value}</span>
                    {selected ? <CheckIcon className="size-4 text-primary" /> : null}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

export function MultiLanguagePicker({
  selected,
  onChange,
  options = LANGUAGES,
  disabled = false,
}: {
  selected: string[];
  onChange: (selected: string[]) => void;
  options?: string[];
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const available = options.filter((language) => !selected.includes(language));
  return (
    <div className="space-y-2">
      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {selected.map((language) => (
            <span
              key={language}
              className="inline-flex h-7 max-w-full items-center gap-1 rounded-full bg-primary/10 px-2 text-xs font-medium text-primary"
            >
              <span className="truncate">{language}</span>
              <button
                type="button"
                disabled={disabled}
                aria-label={`${t('common.delete')} ${language}`}
                className="flex size-4 shrink-0 items-center justify-center rounded-full hover:bg-primary/15 disabled:opacity-50"
                onClick={() => onChange(selected.filter((item) => item !== language))}
              >
                <XIcon className="size-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      {available.length > 0 && (
        <LanguagePicker
          value={t('dub.manage_languages')}
          options={available}
          disabled={disabled}
          onValueChange={(language) => onChange([...selected, language])}
        />
      )}
    </div>
  );
}
