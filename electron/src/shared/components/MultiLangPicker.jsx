import React, { useState, useMemo, useRef, useEffect, useLayoutEffect, useId } from 'react';
import { createPortal } from 'react-dom';
import { Check, X, Search, Globe, ChevronDown } from 'lucide-react';
import { POPULAR_LANGS } from '../utils/constants';
import { LANG_CODES } from '../utils/languages';
import { useTranslation } from 'react-i18next';
import LanguageFlag from './LanguageFlag';

/**
 * MultiLangPicker — chip-based multi-language selector for batch dubbing.
 *
 * Shows selected languages as removable badges. Click "+" to open a
 * searchable dropdown with Popular + All Languages sections.
 */
export default function MultiLangPicker({
  selected = [], // array of { lang: string, code: string }
  onChange, // (newSelected) => void
  disabled = false,
  progressByCode = {},
  single = false,
  compact = false,
  options = LANG_CODES,
  ariaLabel,
}) {
  const { t } = useTranslation();
  const [dropOpen, setDropOpen] = useState(false);
  const [query, setQuery] = useState('');
  const dropRef = useRef(null);
  const menuRef = useRef(null);
  const triggerRef = useRef(null);
  const inputRef = useRef(null);
  const menuId = useId();
  const [menuPos, setMenuPos] = useState(null);

  // Close dropdown on outside click
  useEffect(() => {
    if (!dropOpen) return;
    const handler = (e) => {
      const insideTrigger = dropRef.current?.contains(e.target);
      const insideMenu = menuRef.current?.contains(e.target);
      if (!insideTrigger && !insideMenu) setDropOpen(false);
    };
    const onKeyDown = (e) => {
      if (e.key !== 'Escape') return;
      setDropOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener('mousedown', handler);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', handler);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [dropOpen]);

  // The picker appears inside scrollable/clipping dubbing panels. A body
  // portal escapes every overflow ancestor; fixed positioning plus an
  // above/below flip keeps the menu inside the viewport at either edge.
  useLayoutEffect(() => {
    if (!dropOpen) return;
    const place = () => {
      const trigger = triggerRef.current;
      if (!trigger) return;
      const rect = trigger.getBoundingClientRect();
      const margin = 8;
      const gap = 4;
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      const width = Math.min(Math.max(rect.width, 320), viewportWidth - margin * 2);
      const left = Math.min(
        Math.max(margin, rect.left),
        Math.max(margin, viewportWidth - width - margin),
      );
      const below = viewportHeight - rect.bottom - gap - margin;
      const above = rect.top - gap - margin;
      const openUp = below < 360 && above > below;
      const maxHeight = Math.max(0, Math.min(360, openUp ? above : below));
      setMenuPos(
        openUp
          ? { bottom: viewportHeight - rect.top + gap, left, width, maxHeight }
          : { top: rect.bottom + gap, left, width, maxHeight },
      );
    };
    place();
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    };
  }, [dropOpen]);

  // Focus search when dropdown opens
  useEffect(() => {
    if (dropOpen && inputRef.current) inputRef.current.focus();
  }, [dropOpen]);
  useEffect(() => {
    if (!disabled) return undefined;
    const frame = requestAnimationFrame(() => setDropOpen(false));
    return () => cancelAnimationFrame(frame);
  }, [disabled]);

  const selectedCodes = useMemo(() => new Set(selected.map((s) => s.code)), [selected]);
  const completedCount = useMemo(
    () =>
      selected.filter(({ code }) => {
        const progress = progressByCode[code];
        return progress?.total > 0 && progress.ready === progress.total;
      }).length,
    [progressByCode, selected],
  );
  const selectedFiltered = useMemo(() => {
    const normalized = query.toLowerCase().trim();
    if (!normalized) return selected;
    return selected.filter(
      (item) =>
        item.lang.toLowerCase().includes(normalized) ||
        item.code.toLowerCase().includes(normalized),
    );
  }, [query, selected]);

  const addLang = (lang, code) => {
    if (single) {
      onChange?.([{ lang, code }]);
      setDropOpen(false);
      setQuery('');
      triggerRef.current?.focus();
      return;
    }
    if (selectedCodes.has(code)) return;
    onChange?.([...selected, { lang, code }]);
    setQuery('');
  };

  const removeLang = (code) => {
    onChange?.(selected.filter((s) => s.code !== code));
  };

  const filteredLangs = useMemo(() => {
    const q = query.toLowerCase().trim();
    return options.filter(
      (lc) =>
        !selectedCodes.has(lc.code) &&
        (!q || lc.label.toLowerCase().includes(q) || lc.code.toLowerCase().includes(q)),
    );
  }, [query, selectedCodes, options]);

  const popularFiltered = useMemo(() => {
    const q = query.toLowerCase().trim();
    return POPULAR_LANGS.map((lang) => {
      const match = options.find((lc) => lc.label.toLowerCase() === lang.toLowerCase());
      return match ? { lang, code: match.code } : null;
    }).filter(
      (item) =>
        item &&
        !selectedCodes.has(item.code) &&
        (!q || item.lang.toLowerCase().includes(q) || item.code.includes(q)),
    );
  }, [query, selectedCodes, options]);

  return (
    <div className="relative" ref={dropRef}>
      <button
        ref={triggerRef}
        type="button"
        className="flex min-h-[30px] max-w-full items-center gap-[7px] rounded-[var(--chrome-radius-pill)] border border-transparent bg-[var(--chrome-hover-bg)] px-[9px] py-[4px] text-left text-[color:var(--chrome-fg)] cursor-pointer transition-colors hover:bg-[var(--chrome-accent-bg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--chrome-accent)] disabled:cursor-not-allowed disabled:opacity-50"
        onClick={() => setDropOpen((open) => !open)}
        disabled={disabled}
        style={single ? { minHeight: compact ? 36 : 44, width: '100%' } : undefined}
        aria-haspopup="dialog"
        aria-expanded={dropOpen}
        aria-controls={dropOpen ? menuId : undefined}
        aria-label={ariaLabel || t('dub.manage_languages')}
      >
        {single ? (
          <>
            <LanguageFlag code={selected[0]?.code} />
            <span className="min-w-0 truncate text-sm">{selected[0]?.lang}</span>
            <ChevronDown size={14} className="shrink-0" aria-hidden="true" />
          </>
        ) : (
          <>
            <Globe size={11} className="shrink-0" aria-hidden="true" />
            <span className="truncate text-[0.7rem] font-medium">{t('dub.manage_languages')}</span>
            <span className="shrink-0 font-mono text-[0.62rem] text-[color:var(--chrome-fg-muted)]">
              {t('dub.languages_selected', { count: selected.length })}
            </span>
            {selected.length > 0 && (
              <span className="min-w-0 truncate font-mono text-[0.6rem] text-[color:var(--chrome-fg-dim)]">
                {t('dub.languages_done')}: {completedCount} · {t('dub.languages_pending')}:{' '}
                {selected.length - completedCount}
              </span>
            )}
          </>
        )}
      </button>

      {dropOpen &&
        !disabled &&
        createPortal(
          <div
            ref={menuRef}
            id={menuId}
            className="multi-lang__drop"
            role="dialog"
            aria-label={ariaLabel || t('dub.manage_languages')}
            onKeyDown={(event) => {
              if (!single) return;
              const buttons = Array.from(menuRef.current.querySelectorAll('button'));
              if (event.key === 'Enter' && event.target === inputRef.current) {
                event.preventDefault();
                buttons[0]?.click();
              }
              if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                const index = buttons.indexOf(document.activeElement);
                const next =
                  event.key === 'ArrowDown'
                    ? index + 1
                    : index < 0
                      ? buttons.length - 1
                      : index - 1;
                buttons[(next + buttons.length) % buttons.length]?.focus();
              }
            }}
            style={
              menuPos
                ? {
                    left: menuPos.left,
                    width: menuPos.width,
                    maxHeight: menuPos.maxHeight,
                    ...(menuPos.bottom != null ? { bottom: menuPos.bottom } : { top: menuPos.top }),
                  }
                : { visibility: 'hidden' }
            }
          >
            <div className="flex items-center gap-[6px] px-[10px] py-[8px] border-0 text-[color:var(--chrome-fg-muted)]">
              <Search size={10} aria-hidden="true" />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t('dub.search_languages')}
                aria-label={t('dub.search_languages')}
                name="language-search"
                autoComplete="off"
                spellCheck={false}
                className="flex-1 bg-transparent border-0 text-[color:var(--chrome-fg)] [font-family:var(--font-sans)] text-[0.78rem] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--chrome-accent)]"
              />
            </div>
            <div className="multi-lang-options overflow-y-auto overscroll-contain flex-1 py-[4px]">
              {selectedFiltered.length > 0 && (
                <>
                  <div className="[font-family:var(--font-mono)] text-[0.62rem] font-semibold uppercase [letter-spacing:0.04em] text-[color:var(--chrome-fg-dim)] pt-[6px] px-[10px] pb-[2px]">
                    {t('dub.languages_selected', { count: selected.length })}
                  </div>
                  {selectedFiltered.map((item) => (
                    <button
                      key={item.code}
                      type="button"
                      className="flex items-center gap-[8px] w-full px-[10px] py-[5px] bg-transparent border-0 text-[color:var(--chrome-fg)] [font-family:var(--font-sans)] text-[0.76rem] cursor-pointer text-left hover:bg-[var(--chrome-hover-bg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--chrome-accent)]"
                      style={{ contentVisibility: 'auto', containIntrinsicSize: '30px' }}
                      onClick={() =>
                        single ? addLang(item.lang, item.code) : removeLang(item.code)
                      }
                      aria-label={single ? item.lang : t('common.remove', { term: item.lang })}
                      aria-pressed={single ? true : undefined}
                    >
                      <Check size={10} className="text-[var(--chrome-accent)]" aria-hidden="true" />
                      <LanguageFlag code={item.code} />
                      <span className="min-w-[28px] font-mono text-[0.68rem] font-semibold uppercase text-[var(--chrome-accent)]">
                        {item.code !== item.lang ? item.code : ''}
                      </span>
                      <span className="min-w-0 flex-1 truncate">{item.lang}</span>
                      {!single && (
                        <span className="shrink-0 font-mono text-[0.62rem] text-[var(--chrome-fg-dim)]">
                          {progressByCode[item.code]?.ready || 0}/
                          {progressByCode[item.code]?.total || 0}
                        </span>
                      )}
                      {!single && <X size={10} aria-hidden="true" />}
                    </button>
                  ))}
                </>
              )}
              {popularFiltered.length > 0 && (
                <>
                  <div className="[font-family:var(--font-mono)] text-[0.62rem] font-semibold uppercase [letter-spacing:0.04em] text-[color:var(--chrome-fg-dim)] pt-[6px] px-[10px] pb-[2px]">
                    {t('dub.popular')}
                  </div>
                  {popularFiltered.map((item) => (
                    <button
                      key={item.code}
                      type="button"
                      className="flex items-center gap-[8px] w-full px-[10px] py-[5px] bg-transparent border-0 text-[color:var(--chrome-fg)] [font-family:var(--font-sans)] text-[0.76rem] cursor-pointer text-left [transition:background_0.1s] hover:bg-[var(--chrome-hover-bg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--chrome-accent)]"
                      style={{ contentVisibility: 'auto', containIntrinsicSize: '30px' }}
                      onClick={() => addLang(item.lang, item.code)}
                    >
                      <LanguageFlag code={item.code} />
                      <span className="[font-family:var(--font-mono)] text-[0.68rem] text-[color:var(--chrome-accent)] min-w-[28px] font-semibold">
                        {item.code !== item.lang ? item.code : ''}
                      </span>
                      <span className="min-w-0 truncate">{item.lang}</span>
                    </button>
                  ))}
                </>
              )}
              <div className="[font-family:var(--font-mono)] text-[0.62rem] font-semibold uppercase [letter-spacing:0.04em] text-[color:var(--chrome-fg-dim)] pt-[6px] px-[10px] pb-[2px]">
                {t('dub.all_languages')}
              </div>
              {(single ? filteredLangs : filteredLangs.slice(0, 50)).map((lc) => (
                <button
                  key={lc.code}
                  type="button"
                  className="flex items-center gap-[8px] w-full px-[10px] py-[5px] bg-transparent border-0 text-[color:var(--chrome-fg)] [font-family:var(--font-sans)] text-[0.76rem] cursor-pointer text-left [transition:background_0.1s] hover:bg-[var(--chrome-hover-bg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--chrome-accent)]"
                  style={{ contentVisibility: 'auto', containIntrinsicSize: '30px' }}
                  onClick={() => addLang(lc.label, lc.code)}
                >
                  <LanguageFlag code={lc.code} />
                  <span className="[font-family:var(--font-mono)] text-[0.68rem] text-[color:var(--chrome-accent)] min-w-[28px] font-semibold">
                    {lc.code !== lc.label ? lc.code : ''}
                  </span>
                  <span className="min-w-0 truncate">{lc.label}</span>
                </button>
              ))}
              {!single && filteredLangs.length > 50 && (
                <div className="px-[10px] py-[8px] text-[0.7rem] text-[color:var(--chrome-fg-dim)] text-center">
                  {t('dub.more_to_narrow', { count: filteredLangs.length - 50 })}
                </div>
              )}
              {filteredLangs.length === 0 &&
                popularFiltered.length === 0 &&
                selectedFiltered.length === 0 && (
                  <div className="px-[10px] py-[8px] text-[0.7rem] text-[color:var(--chrome-fg-dim)] text-center">
                    {t('dub.no_matches')}
                  </div>
                )}
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
