import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useId,
} from 'react';
import { createPortal } from 'react-dom';
import {
  pinnedRows,
  readRecents,
  withCommittedOption,
  writeRecents,
} from './searchable-select-helpers';
import { Search, ChevronDown, Check, Star, Clock } from 'lucide-react';
import { useTranslation } from 'react-i18next';

const MAX_DISPLAY = 200;

const normalize = (s) => (s || '').toString().toLowerCase();

// Migrated `.ss-group-label` — used for both the pinned header and lazy group
// headers, so share one string.
const GROUP_LABEL_CLS =
  'pt-[4px] px-[10px] pb-[2px] text-[0.55rem] uppercase tracking-[0.06em] text-[color:var(--text-secondary,var(--muted-foreground))] opacity-70 flex items-center gap-[4px]';

export default function SearchableSelect(props) {
  return <ClassicSearchableSelect {...props} />;
}

function ClassicSearchableSelect({
  value,
  onChange,
  options,
  placeholder = 'Select…',
  popular = [],
  recentsKey = '',
  renderLabel,
  renderOption,
  disabled = false,
  buttonStyle,
  buttonClassName = 'input-base',
  ariaLabel,
  size = 'md',
  menuClassName = '',
  // When true, emit a `.ss-group-label` header each time `option.group` changes
  // (and `option.groupLabel` is non-empty) while walking the MAIN rows. Default
  // false so the two pre-existing call sites are unaffected. (#22)
  renderGroupHeaders = false,
  // Gate which committed values get recorded as recents. Default records all
  // (back-compat). VoiceSelector passes a guard so sentinel values
  // ('' / preset: / auto:) never pollute the recents list. (#22)
  isRecentable = () => true,
  // Lift the internal search text / open state so a parent can drive an async,
  // server-backed option source (VoiceSelector's gallery search fetches
  // /archetypes as you type, and only when the dropdown is open). Both default
  // to no-ops so the pre-existing call sites are unaffected. (#1219)
  onQueryChange,
  onOpenChange,
  // Render the dropdown in a body portal with fixed positioning instead of
  // inline. Required inside clipping ancestors (overflow:auto / react-window
  // virtualized rows) where an inline absolute popup would be cut off — the dub
  // segment table is the driving case (#1220). Default false so the existing
  // inline call sites are unaffected.
  menuPortal = false,
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState(0);
  const [recents, setRecents] = useState(() => readRecents(recentsKey));
  const wrapRef = useRef(null);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const menuRef = useRef(null);
  const listboxId = useId();
  const [menuPos, setMenuPos] = useState(null);

  const getVal = useCallback((o) => (typeof o === 'string' ? o : o?.value), []);
  const getLabel = useCallback(
    (o) => {
      if (renderLabel) return renderLabel(o);
      if (typeof o === 'string') return o;
      return o?.label ?? o?.value ?? '';
    },
    [renderLabel],
  );

  const byVal = useMemo(() => {
    const m = new Map();
    for (const o of options) m.set(getVal(o), o);
    return m;
  }, [options, getVal]);

  const currentLabel = useMemo(() => {
    const o = byVal.get(value);
    return o ? getLabel(o) : value || placeholder;
  }, [byVal, value, getLabel, placeholder]);

  const filtered = useMemo(() => {
    const q = normalize(query);
    if (!q) return options;
    return options.filter(
      (o) => normalize(getLabel(o)).includes(q) || normalize(getVal(o)).includes(q),
    );
  }, [options, query, getLabel, getVal]);

  const pinned = useMemo(
    () => pinnedRows({ recents, popular, byValue: byVal, getValue: getVal, query }),
    [recents, popular, byVal, getVal, query],
  );

  const displayed = useMemo(() => {
    const seen = new Set(pinned.map(({ o }) => getVal(o)));
    const rows = filtered
      .filter((option) => {
        const key = getVal(option);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .slice(0, MAX_DISPLAY);
    // A saved value past the slice would otherwise have no row, which lets Enter
    // replace it without a choice. Keep it first so the arrow keys start there.
    return withCommittedOption(rows, filtered, value, getVal, {
      query,
      pinnedValues: new Set(pinned.map(({ o }) => getVal(o))),
    });
  }, [filtered, pinned, getVal, value, query]);

  const flatItems = useMemo(() => {
    const list = [];
    for (const p of pinned) list.push({ o: p.o, kind: p.kind });
    for (const o of displayed) list.push({ o, kind: 'main' });
    return list;
  }, [pinned, displayed]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => {
      // The portaled menu lives outside wrapRef, so it must be checked too —
      // otherwise a click inside the dropdown reads as "outside" and closes it.
      const insideWrap = wrapRef.current && wrapRef.current.contains(e.target);
      const insideMenu = menuRef.current && menuRef.current.contains(e.target);
      if (!insideWrap && !insideMenu) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  // Portal positioning: glue the fixed dropdown to the trigger and keep it there
  // while ancestors scroll / the window resizes. Only active when portaling.
  useLayoutEffect(() => {
    if (!open || !menuPortal) return;
    const place = () => {
      const el = wrapRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      // Clamp within the viewport so a right-edge (narrow-column) trigger can't
      // push the min-220px menu off-screen and force a horizontal scrollbar.
      const width = Math.min(Math.max(r.width, 220), window.innerWidth - 16);
      const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
      // Flip above the trigger when there isn't enough room below (e.g. the last
      // dub segment row, near the viewport bottom) — otherwise a below-anchored
      // fixed menu runs off-screen and scrolling just re-pins it there. Also cap
      // the list to the available space so the chosen side always fits.
      const GAP = 4;
      const vh = window.innerHeight;
      const below = vh - r.bottom - GAP;
      const above = r.top - GAP;
      const openUp = below < 220 && above > below;
      const listMax = Math.max(120, Math.floor(Math.min(280, openUp ? above : below)));
      setMenuPos(
        openUp
          ? { bottom: vh - r.top + GAP, left, width, listMax }
          : { top: r.bottom + GAP, left, width, listMax },
      );
    };
    place();
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    };
  }, [open, menuPortal]);

  useEffect(() => {
    if (open) {
      const timer = setTimeout(() => inputRef.current?.focus(), 0);
      return () => clearTimeout(timer);
    } else {
      setQuery('');
    }
  }, [open]);

  useEffect(() => {
    if (!open || !listRef.current) return;
    const el = listRef.current.querySelector(`[data-idx="${highlight}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  }, [highlight, open]);

  // Surface open/query state to an optional parent driver (#1219). Kept in
  // effects (not inline in handlers) so the reset-on-close above is included.
  useEffect(() => {
    onOpenChange?.(open);
  }, [open, onOpenChange]);
  useEffect(() => {
    onQueryChange?.(query);
  }, [query, onQueryChange]);

  const commit = (o) => {
    const v = getVal(o);
    onChange?.(v);
    if (recentsKey && isRecentable(v)) {
      const next = [v, ...recents.filter((r) => r !== v)].slice(0, 8);
      setRecents(next);
      writeRecents(recentsKey, next);
    }
    setOpen(false);
    wrapRef.current?.querySelector('button')?.focus();
  };

  const onKey = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlight((h) => Math.min(flatItems.length - 1, h + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlight((h) => Math.max(0, h - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const item = flatItems[highlight];
      if (item) commit(item.o);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
      wrapRef.current?.querySelector('button')?.focus();
    } else if (e.key === 'Tab') {
      setOpen(false);
    }
  };

  // `.ss-sm/.ss-md` only sized the trigger (`.ss-{sm,md} .ss-trigger`); apply
  // that directly on the trigger now that the descendant selectors are gone.
  const triggerSizeCls = size === 'sm' ? 'text-[0.65rem] px-[8px] py-[4px]' : 'text-[0.75rem]';

  // WAI-ARIA combobox pattern: the collapsed trigger is the focusable control,
  // so it must open the popup from the keyboard. Without this, ArrowDown/ArrowUp
  // on the focused trigger did nothing — the key handler lives on the search
  // input *inside* the popup and is therefore unreachable while collapsed, and
  // only Enter/Space worked (through the native button activation). Opening the
  // popup keeps the committed value highlighted for ArrowDown (Enter then keeps
  // it, the collapsed-combobox convention) and steps to the previous value for
  // ArrowUp, wrapping at the ends; Home/End jump to the first/last row. The
  // highlight is set before opening so the first paint lands on the intended row.
  const onTriggerKeyDown = (e) => {
    if (disabled || open) return;
    const options = flatItems;
    if (!options.length) {
      // A picker whose list loads while it is open (the gallery, for example) has
      // no rows yet, and a click still opens it. Give the keyboard the same door.
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
      e.preventDefault();
      setHighlight(-1);
      setOpen(true);
      return;
    }
    const selected = options.findIndex(({ o }) => getVal(o) === value);
    // A committed value can be outside the rows this menu renders: the list stops
    // at MAX_DISPLAY, so a saved language past the cap has no row here at all.
    // Opening on the first row would let Enter overwrite that saved value without
    // the user having chosen anything, so open with no highlight and let them move
    // deliberately.
    const committedOffscreen = selected < 0 && Boolean(value);
    let next;
    if (e.key === 'ArrowDown') next = selected >= 0 ? selected : committedOffscreen ? -1 : 0;
    else if (e.key === 'ArrowUp')
      next =
        selected >= 0
          ? (selected - 1 + options.length) % options.length
          : committedOffscreen
            ? -1
            : options.length - 1;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = options.length - 1;
    else return;
    e.preventDefault();
    setHighlight(next);
    setOpen(true);
  };

  // Escape clipping ancestors (overflow:auto / react-window rows) by portaling
  // the dropdown to <body>; otherwise render it inline as an absolute child.
  const wrapMenu = (el) => (menuPortal ? createPortal(el, document.body) : el);

  return (
    // `ss-wrap` is retained as a marker class: residual.css targets
    // `.voice-selector > .ss-wrap` (cross-file child combinator). Its own
    // position/width rule now lives in these utilities.
    <div ref={wrapRef} className="ss-wrap relative w-full">
      <button
        type="button"
        className={`${buttonClassName} flex items-center justify-between gap-[6px] w-full text-left cursor-pointer [font-family:inherit] disabled:cursor-not-allowed disabled:opacity-50 ${triggerSizeCls}`}
        style={buttonStyle}
        onClick={() => {
          if (disabled) return;
          if (!open) {
            setHighlight(
              Math.max(
                0,
                flatItems.findIndex(({ o }) => getVal(o) === value),
              ),
            );
          }
          setOpen(!open);
        }}
        onKeyDown={onTriggerKeyDown}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-label={ariaLabel}
        title={currentLabel}
      >
        <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-[color:var(--text-primary,var(--foreground))]">
          {currentLabel}
        </span>
        <ChevronDown
          size={12}
          className="text-[color:var(--text-secondary,var(--muted-foreground))] shrink-0"
        />
      </button>

      {open &&
        wrapMenu(
          <div
            ref={menuRef}
            className={`${menuClassName} z-[1000] bg-[var(--color-bg,var(--popover))] border-0 rounded-lg shadow-xl overflow-hidden max-w-[calc(100vw-16px)] ${
              menuPortal ? 'fixed' : 'absolute top-[calc(100%+4px)] left-0 right-0'
            }`}
            style={
              menuPortal && menuPos
                ? {
                    left: menuPos.left,
                    width: menuPos.width,
                    ...(menuPos.bottom != null ? { bottom: menuPos.bottom } : { top: menuPos.top }),
                  }
                : undefined
            }
          >
            <div className="relative p-3 flex items-center gap-2">
              <Search
                size={12}
                className="absolute left-5 top-1/2 -translate-y-1/2 text-[var(--chrome-fg-muted,var(--muted-foreground))] pointer-events-none"
              />
              <input
                ref={inputRef}
                className="flex-1 min-w-0 w-full min-h-10 bg-[var(--chrome-hover-bg,var(--accent))] border-0 rounded-md py-2 pr-3 pl-8 text-sm text-[var(--chrome-fg,var(--foreground))] focus-visible:outline-2 focus-visible:outline-[var(--chrome-accent,var(--primary))] [font-family:inherit]"
                role="combobox"
                aria-expanded="true"
                aria-autocomplete="list"
                aria-controls={listboxId}
                aria-activedescendant={
                  flatItems[highlight] ? `${listboxId}-option-${highlight}` : undefined
                }
                aria-label={ariaLabel || t('common.search')}
                placeholder={t('common.search')}
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setHighlight(0);
                }}
                onKeyDown={onKey}
              />
            </div>

            <div
              ref={listRef}
              role="listbox"
              id={listboxId}
              aria-label={ariaLabel || placeholder}
              className="max-h-[280px] overflow-y-auto py-[2px] [&::-webkit-scrollbar]:w-[6px] [&::-webkit-scrollbar-thumb]:bg-[rgba(255,255,255,0.1)] [&::-webkit-scrollbar-thumb]:rounded-[3px]"
              style={menuPortal && menuPos ? { maxHeight: menuPos.listMax } : undefined}
            >
              {flatItems.length === 0 && (
                <div className="py-[8px] px-[10px] text-[0.65rem] text-[color:var(--text-secondary,var(--muted-foreground))] italic text-center">
                  {t('common.no_matches')}
                </div>
              )}

              {pinned.length > 0 && (
                <div className={GROUP_LABEL_CLS}>
                  {recents.length ? (
                    <>
                      <Clock size={9} /> {t('common.recent_and_popular')}
                    </>
                  ) : (
                    <>
                      <Star size={9} /> {t('common.popular_label')}
                    </>
                  )}
                </div>
              )}

              {(() => {
                let lastGroup;
                return flatItems.map((it, idx) => {
                  const v = getVal(it.o);
                  const selected = v === value;
                  const highlighted = idx === highlight;
                  // Group header: emitted lazily on the first MAIN row of a new
                  // group whose option carries a non-empty groupLabel (#22). Pinned
                  // recent/popular rows never trigger a header. `lastGroup` advances
                  // only on main rows so a pinned row can't swallow the first header.
                  const showHeader =
                    renderGroupHeaders &&
                    it.kind === 'main' &&
                    it.o &&
                    it.o.groupLabel &&
                    it.o.group !== lastGroup;
                  if (it.kind === 'main') lastGroup = it.o?.group;
                  return (
                    <React.Fragment key={`${it.kind}-${v}-${idx}`}>
                      {showHeader && <div className={GROUP_LABEL_CLS}>{it.o.groupLabel}</div>}
                      <div
                        data-idx={idx}
                        id={`${listboxId}-option-${idx}`}
                        // Migrated `.ss-option`/`.ss-hl`/`.ss-sel` cascade. Selected
                        // wins its color/weight; selected+highlighted gets the
                        // stronger amber wash; highlight (and hover, when neither
                        // selected nor highlighted) gets the amber accent.
                        className={[
                          'flex min-h-10 items-center gap-2 py-2 px-3 mx-2 my-1 rounded-md text-sm cursor-pointer select-none',
                          selected
                            ? 'text-[var(--chrome-accent,var(--primary))] font-medium'
                            : highlighted
                              ? 'text-[var(--chrome-accent,var(--primary))]'
                              : 'text-[var(--chrome-fg,var(--foreground))] hover:text-[var(--chrome-accent,var(--primary))]',
                          selected && highlighted
                            ? 'bg-[var(--chrome-accent-bg,var(--accent))]'
                            : selected
                              ? 'bg-[var(--chrome-accent-bg,var(--accent))]'
                              : highlighted
                                ? 'bg-[var(--chrome-accent-bg,var(--accent))]'
                                : 'hover:bg-[var(--chrome-accent-bg,var(--accent))]',
                        ].join(' ')}
                        onMouseEnter={() => setHighlight(idx)}
                        onMouseDown={(e) => {
                          e.preventDefault();
                          commit(it.o);
                        }}
                        role="option"
                        aria-selected={selected}
                      >
                        {it.kind === 'recent' && (
                          <Clock
                            size={9}
                            className="text-[color:var(--text-secondary,var(--muted-foreground))] shrink-0"
                          />
                        )}
                        {it.kind === 'popular' && (
                          <Star
                            size={9}
                            className="text-[color:var(--text-secondary,var(--muted-foreground))] shrink-0"
                          />
                        )}
                        <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
                          {renderOption ? renderOption(it.o) : getLabel(it.o)}
                        </span>
                        {selected && (
                          <Check
                            size={14}
                            aria-hidden="true"
                            className="text-[var(--chrome-accent,var(--primary))] shrink-0"
                          />
                        )}
                      </div>
                    </React.Fragment>
                  );
                });
              })()}

              {!query && filtered.length > MAX_DISPLAY && (
                <div className="py-[8px] px-[10px] text-[0.65rem] text-[color:var(--text-secondary,var(--muted-foreground))] italic text-center [border-top:1px_solid_rgba(255,255,255,0.04)]">
                  {t('common.showing_of', { shown: MAX_DISPLAY, total: filtered.length })}
                </div>
              )}
            </div>
          </div>,
        )}
    </div>
  );
}
