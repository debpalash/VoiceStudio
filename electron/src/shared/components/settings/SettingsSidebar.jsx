import React from 'react';
import { RotateCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { GROUPS } from './settingsCategories';

/**
 * SettingsSidebar — the grouped category navigation for the Settings hub.
 *
 * Wide (≥760px of scaled shell content): a vertical rail of group headers + category items (icon +
 * label; active item = brand accent with an inset accent bar). Restart-bearing
 * categories show a small ↻ glyph. Group headers stick to the top of the rail
 * while you scroll, so the group you are inside is always named.
 *
 * Narrow (<760px of scaled shell content): collapses to a single native <select> drop-down (with
 * <optgroup> per group) so the whole IA stays reachable on a phone-width window.
 *
 * `visibleIds` (a Set) filters which categories render — the search box in the
 * parent drives it. Groups with no visible items are hidden entirely; when the
 * search matches NOTHING, a "no results" empty state (with a clear-search
 * action) replaces both layouts so the nav never renders blank. While filtering,
 * the matched span of each label is accent-highlighted so it is obvious WHY a
 * category survived the filter.
 *
 * Keyboard: the rail is a single tab stop (roving tabindex — only the active
 * item is tabbable). ↑/↓ move between categories across group boundaries,
 * Home/End jump to the first/last visible one, and selection follows focus the
 * way a single-select nav is expected to behave. Arrowing past the first item
 * hands focus back to the search box (`onFocusSearch`), so search → list → search
 * is one continuous keyboard loop.
 *
 * @param {Set<string>} visibleIds     category ids to show (search-filtered)
 * @param {string}      active         active category id
 * @param {function}    onSelect       (id) => void
 * @param {string=}     query          current search query (for highlight + empty state)
 * @param {function=}   onClearSearch  clears the search query
 * @param {function=}   onFocusSearch  moves focus back to the search input
 */
export default function SettingsSidebar({
  visibleIds,
  active,
  onSelect,
  query,
  onClearSearch,
  onFocusSearch,
}) {
  const { t } = useTranslation();
  const isVisible = (id) => !visibleIds || visibleIds.has(id);
  const label = (it) => t(it.labelKey, { defaultValue: it.defaultLabel });
  const anyVisible = GROUPS.some((g) => g.items.some((it) => isVisible(it.id)));

  // Flattened visible order — what ↑/↓ actually walk (groups are presentation).
  const order = React.useMemo(
    () => GROUPS.flatMap((g) => g.items.filter((it) => isVisible(it.id)).map((it) => it.id)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [visibleIds],
  );

  const itemRefs = React.useRef(new Map());
  const setItemRef = React.useCallback((id, el) => {
    if (el) itemRefs.current.set(id, el);
    else itemRefs.current.delete(id);
  }, []);

  // Keep the selected category on screen when it changes from somewhere other
  // than a click — a deep-link, or search moving selection to the first match.
  React.useEffect(() => {
    const el = itemRefs.current.get(active);
    el?.scrollIntoView?.({ block: 'nearest' });
  }, [active]);

  const focusItem = React.useCallback(
    (id) => {
      onSelect(id);
      // Focus after the click-driven re-render so the roving tabindex has
      // already moved to the new item.
      requestAnimationFrame(() => itemRefs.current.get(id)?.focus?.());
    },
    [onSelect],
  );

  const onKeyDown = React.useCallback(
    (e) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      const from = order.indexOf(active);
      let next = null;
      if (e.key === 'ArrowDown') next = order[Math.min(order.length - 1, from + 1)];
      else if (e.key === 'ArrowUp') {
        if (from <= 0) {
          // Past the top of the list — hand the keyboard back to search.
          if (onFocusSearch) {
            e.preventDefault();
            onFocusSearch();
          }
          return;
        }
        next = order[from - 1];
      } else if (e.key === 'Home') next = order[0];
      else if (e.key === 'End') next = order[order.length - 1];
      else return;
      if (!next) return;
      e.preventDefault();
      focusItem(next);
    },
    [order, active, focusItem, onFocusSearch],
  );

  if (!anyVisible) {
    return (
      <nav aria-label={t('settings.title', { defaultValue: 'Settings' })}>
        <div
          data-testid="settings-search-empty"
          className="px-[var(--space-3)] py-[var(--space-3)] [font-family:var(--font-sans)] text-[length:var(--text-sm)] text-[color:var(--chrome-fg-muted)]"
        >
          <p className="m-0 mb-[var(--space-3)]">
            {t('settings.search_no_results', {
              defaultValue: 'No settings match “{{query}}”',
              query: query ?? '',
            })}
          </p>
          {onClearSearch && (
            <button
              type="button"
              onClick={onClearSearch}
              className="cursor-pointer appearance-none rounded-[var(--chrome-radius-pill)] border border-transparent bg-[var(--chrome-hover-bg)] px-[var(--space-3)] py-[var(--space-2)] [font-family:var(--font-sans)] text-[length:var(--text-sm)] font-medium text-[color:var(--chrome-fg)] hover:text-[color:var(--chrome-accent)] focus-visible:shadow-[var(--focus-ring)] focus-visible:outline-none"
            >
              {t('common.clear', { defaultValue: 'Clear' })}
            </button>
          )}
        </div>
      </nav>
    );
  }

  return (
    <nav
      aria-label={t('settings.title', { defaultValue: 'Settings' })}
      className="@min-[760px]/settings-shell:flex @min-[760px]/settings-shell:min-h-0 @min-[760px]/settings-shell:flex-1 @min-[760px]/settings-shell:flex-col"
    >
      {/* Narrow: dropdown navigator */}
      <div className="@min-[760px]/settings-shell:hidden">
        <select
          value={active}
          onChange={(e) => onSelect(e.target.value)}
          aria-label={t('settings.title', { defaultValue: 'Settings' })}
          data-testid="settings-nav-select"
          className="w-full min-w-0 box-border rounded-[var(--chrome-radius-pill)] border border-transparent bg-[color-mix(in_srgb,var(--chrome-bg)_94%,white)] px-[var(--space-4)] py-[var(--space-3)] text-[color:var(--chrome-fg)] [font-family:var(--font-sans)] text-[length:var(--text-sm)] focus:border-[var(--chrome-accent)] focus:outline-none"
        >
          {GROUPS.map((g) => {
            const items = g.items.filter((it) => isVisible(it.id));
            if (items.length === 0) return null;
            return (
              <optgroup key={g.id} label={t(g.labelKey, { defaultValue: g.defaultLabel })}>
                {items.map((it) => (
                  <option key={it.id} value={it.id}>
                    {label(it)}
                  </option>
                ))}
              </optgroup>
            );
          })}
        </select>
      </div>

      {/* Wide: vertical grouped rail */}
      <div
        data-testid="settings-nav-scroll"
        onKeyDown={onKeyDown}
        className="settings-sidebar-scroll hidden min-h-0 flex-1 flex-col gap-[var(--space-4)] overflow-y-auto overscroll-contain pr-[var(--space-2)] @min-[760px]/settings-shell:flex"
      >
        {GROUPS.map((g) => {
          const items = g.items.filter((it) => isVisible(it.id));
          if (items.length === 0) return null;
          return (
            <div key={g.id} className="flex flex-col gap-[2px]">
              <div className="sticky top-0 z-[1] bg-[var(--chrome-bg)] px-[var(--space-3)] pb-[2px] pt-[1px] [font-family:var(--chrome-font-mono)] text-[length:var(--chrome-label-size,0.62rem)] font-semibold uppercase tracking-[var(--chrome-label-track,0.06em)] text-[color:var(--chrome-fg-dim)]">
                {t(g.labelKey, { defaultValue: g.defaultLabel })}
              </div>
              {items.map((it) => {
                const isActive = it.id === active;
                const Icon = it.icon;
                return (
                  <button
                    key={it.id}
                    ref={(el) => setItemRef(it.id, el)}
                    type="button"
                    onClick={() => onSelect(it.id)}
                    aria-current={isActive ? 'page' : undefined}
                    // Roving tabindex: the rail is one tab stop, arrows move
                    // inside it (the ARIA authoring practice for a nav list).
                    tabIndex={isActive ? 0 : -1}
                    data-testid={`settings-nav-${it.id}`}
                    className={cn(
                      'group relative flex w-full appearance-none items-center gap-[var(--space-3)] rounded-[var(--chrome-radius-pill)] border border-transparent bg-transparent px-[var(--space-3)] py-[var(--space-2)] text-left [font-family:var(--font-sans)] text-[length:var(--text-sm)] transition-[background,color] duration-[120ms] focus-visible:shadow-[var(--focus-ring)] focus-visible:outline-none',
                      isActive
                        ? 'bg-[var(--chrome-hover-bg)] font-semibold text-[color:var(--chrome-fg)] shadow-[inset_2px_0_0_var(--chrome-accent)]'
                        : 'font-medium text-[color:var(--chrome-fg-muted)] hover:bg-[var(--chrome-hover-bg)] hover:text-[color:var(--chrome-fg)]',
                    )}
                  >
                    {Icon && (
                      <Icon
                        size={14}
                        aria-hidden="true"
                        className={cn(
                          'shrink-0',
                          isActive
                            ? 'text-[var(--chrome-accent)]'
                            : 'text-[var(--chrome-fg-dim)] group-hover:text-[var(--chrome-fg-muted)]',
                        )}
                      />
                    )}
                    <span className="flex-auto truncate">
                      <Highlighted text={label(it)} query={query} />
                    </span>
                    {it.restart && (
                      <span
                        role="img"
                        aria-label={t('settings.restart_required', {
                          defaultValue: 'Restart required',
                        })}
                        title={t('settings.restart_required', {
                          defaultValue: 'Restart required',
                        })}
                        className="inline-flex shrink-0 items-center"
                      >
                        <RotateCw
                          size={11}
                          aria-hidden="true"
                          className="text-[var(--chrome-fg-dim)] opacity-70"
                        />
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          );
        })}
      </div>
    </nav>
  );
}

/**
 * Accent-highlights the matched span of a category label while a search filter
 * is active. Only the label's own match is highlighted — a category that made
 * the cut on a hidden `keywords` entry simply renders plain, which is itself
 * the useful signal ("matched on something other than the name").
 */
function Highlighted({ text, query }) {
  const q = (query || '').trim();
  if (!q) return text;
  const at = text.toLowerCase().indexOf(q.toLowerCase());
  if (at < 0) return text;
  return (
    <>
      {text.slice(0, at)}
      <mark className="bg-transparent font-semibold text-[color:var(--chrome-accent)]">
        {text.slice(at, at + q.length)}
      </mark>
      {text.slice(at + q.length)}
    </>
  );
}
