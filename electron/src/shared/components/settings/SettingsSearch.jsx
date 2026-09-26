import React from 'react';
import { Search, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * SettingsSearch — the filter box at the top of the Settings sidebar.
 *
 * Token-styled to match the chrome. Filters the category list (and, as a
 * bonus, matches individual setting labels — the orchestrator maps a setting
 * hit back to its category). Controlled: the parent owns the query string.
 *
 * Keyboard: ⌘K / Ctrl+K focuses it from anywhere on the page (the parent binds
 * that and passes `inputRef`); Enter or ↓ hands focus to the filtered category
 * list; Escape clears a query, or blurs when there is nothing left to clear.
 * The keycap hint renders the modifier this platform actually uses — the
 * shortcut itself is identical everywhere.
 *
 * @param {string}    value
 * @param {function}  onChange     called with the next string
 * @param {function=} onClear      clears the query (defaults to onChange(''))
 * @param {object=}   inputRef     ref to the <input> (lets the page focus it)
 * @param {function=} onEnterList  move focus into the category list
 */
export default function SettingsSearch({ value, onChange, onClear, inputRef, onEnterList }) {
  const { t } = useTranslation();
  const isMacLike =
    typeof navigator !== 'undefined' && /Mac|iPad|iPhone|iPod/.test(navigator.platform || '');
  const clear = () => (onClear ? onClear() : onChange(''));

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown' || e.key === 'Enter') {
      if (!onEnterList) return;
      e.preventDefault();
      onEnterList();
      return;
    }
    if (e.key === 'Escape') {
      // Clear first, blur second — so Escape never strands a filtered list.
      if (value) {
        e.preventDefault();
        clear();
      } else {
        e.currentTarget.blur();
      }
    }
  };

  return (
    <div className="relative mb-[var(--space-3)]">
      <Search
        size={13}
        aria-hidden="true"
        className="pointer-events-none absolute left-[var(--space-3)] top-1/2 -translate-y-1/2 text-[var(--chrome-fg-dim)]"
      />
      <input
        ref={inputRef}
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={t('settings.search_placeholder', { defaultValue: 'Search settings…' })}
        aria-label={t('settings.search_placeholder', { defaultValue: 'Search settings…' })}
        data-testid="settings-search"
        className="w-full min-w-0 box-border rounded-[var(--chrome-radius-pill)] border border-transparent bg-[color-mix(in_srgb,var(--chrome-bg)_94%,white)] py-[var(--space-2)] pl-[calc(var(--space-3)*2+13px)] pr-[calc(var(--space-3)*2+22px)] text-[color:var(--chrome-fg)] [font-family:var(--font-sans)] text-[length:var(--text-sm)] focus:border-[var(--chrome-accent)] focus:outline-none [&::-webkit-search-cancel-button]:appearance-none"
      />
      {value ? (
        <button
          type="button"
          onClick={clear}
          aria-label={t('common.clear', { defaultValue: 'Clear' })}
          className="absolute right-[var(--space-2)] top-1/2 inline-flex -translate-y-1/2 cursor-pointer items-center justify-center rounded-[var(--chrome-radius-pill)] border-0 bg-transparent p-[3px] text-[var(--chrome-fg-dim)] hover:text-[var(--chrome-fg)] focus-visible:shadow-[var(--focus-ring)] focus-visible:outline-none"
        >
          <X size={13} aria-hidden="true" />
        </button>
      ) : (
        <kbd
          aria-hidden="true"
          data-testid="settings-search-kbd"
          className="pointer-events-none absolute right-[var(--space-2)] top-1/2 -translate-y-1/2 rounded-[4px] bg-[color-mix(in_srgb,var(--chrome-fg)_7%,transparent)] px-[5px] py-[1px] [font-family:var(--chrome-font-mono)] text-[length:var(--chrome-label-size,0.62rem)] text-[color:var(--chrome-fg-dim)]"
        >
          {isMacLike ? '⌘K' : 'Ctrl K'}
        </kbd>
      )}
    </div>
  );
}
