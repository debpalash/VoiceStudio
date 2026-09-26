import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Globe, Search, X } from 'lucide-react';
import { Badge } from '../../ui';

export default function TrackManager({ t, tracks, selection, setSelection, primaryCode }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const triggerRef = useRef(null);
  const inputRef = useRef(null);
  const panelRef = useRef(null);
  const titleId = useId();
  const selectedCount = tracks.filter((track) => selection[track.code] !== false).length;
  const filteredTracks = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return tracks;
    return tracks.filter(
      (track) =>
        track.code.toLowerCase().includes(normalized) ||
        track.label.toLowerCase().includes(normalized),
    );
  }, [query, tracks]);
  const closePanel = useCallback(() => {
    setOpen(false);
    setQuery('');
    triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        closePanel();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = panelRef.current?.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    inputRef.current?.focus();
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [closePanel, open]);

  const setAll = (on) => setSelection(Object.fromEntries(tracks.map((track) => [track.code, on])));
  const setDubsOnly = () =>
    setSelection(Object.fromEntries(tracks.map((track) => [track.code, track.kind === 'dub'])));
  const toggle = (code) =>
    setSelection((previous) => ({ ...previous, [code]: previous[code] === false }));

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="inline-flex items-center gap-[6px] rounded-[var(--chrome-radius-pill)] border border-transparent bg-[var(--chrome-hover-bg)] px-[9px] py-[4px] text-[length:var(--text-xs)] text-[var(--chrome-fg)] cursor-pointer transition-colors hover:bg-[var(--chrome-accent-bg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--chrome-accent)]"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`${t('exportModal.tracks')} ${selectedCount}/${tracks.length}`}
      >
        <Globe size={10} aria-hidden="true" />
        <span className="font-[family-name:var(--chrome-font-mono)] uppercase">
          {t('exportModal.tracks')}
        </span>
        <span className="text-[var(--chrome-fg-muted)]">
          {selectedCount}/{tracks.length}
        </span>
      </button>

      {open &&
        createPortal(
          <div
            className="fixed inset-0 z-[var(--z-overlay)] flex items-center justify-center bg-black/45 p-[16px]"
            onMouseDown={(event) => {
              event.stopPropagation();
              if (event.target === event.currentTarget) closePanel();
            }}
          >
            <div
              ref={panelRef}
              role="dialog"
              aria-modal="true"
              aria-labelledby={titleId}
              className="flex max-h-[min(560px,80vh)] w-full max-w-[420px] flex-col overflow-hidden rounded-[10px] bg-[var(--chrome-bg)] shadow-2xl"
            >
              <div className="flex items-center gap-[8px] bg-[var(--chrome-hover-bg)] px-[12px] py-[10px]">
                <span
                  id={titleId}
                  className="font-mono text-[0.72rem] uppercase text-[var(--chrome-fg)]"
                >
                  {t('exportModal.tracks')} · {selectedCount}/{tracks.length}
                </span>
                <button
                  type="button"
                  className="ml-auto inline-flex rounded-[4px] border-0 bg-transparent p-[3px] text-[var(--chrome-fg-muted)] cursor-pointer hover:text-[var(--chrome-fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--chrome-accent)]"
                  onClick={closePanel}
                  aria-label={t('common.close')}
                >
                  <X size={13} aria-hidden="true" />
                </button>
              </div>
              <div className="flex items-center gap-[6px] px-[12px] pt-[10px] text-[length:var(--text-xs)]">
                <button type="button" className="btn-subtle" onClick={() => setAll(true)}>
                  {t('exportModal.track_all')}
                </button>
                <button type="button" className="btn-subtle" onClick={() => setAll(false)}>
                  {t('exportModal.track_none')}
                </button>
                <button type="button" className="btn-subtle" onClick={setDubsOnly}>
                  {t('exportModal.track_dubs_only')}
                </button>
              </div>
              <label className="mx-[12px] my-[10px] flex items-center gap-[7px] rounded-[6px] bg-[var(--chrome-hover-bg)] px-[8px] py-[6px] text-[var(--chrome-fg-muted)]">
                <Search size={11} aria-hidden="true" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={t('common.search')}
                  aria-label={t('common.search')}
                  name="track-search"
                  autoComplete="off"
                  className="min-w-0 flex-1 rounded-[3px] border-0 bg-transparent text-[0.76rem] text-[var(--chrome-fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--chrome-accent)]"
                />
              </label>
              <div className="min-h-0 overflow-y-auto overscroll-contain px-[8px] pb-[10px]">
                {filteredTracks.map((track) => {
                  const checked = selection[track.code] !== false;
                  return (
                    <label
                      key={track.code}
                      className="flex cursor-pointer items-center gap-[8px] rounded-[6px] px-[8px] py-[6px] text-[0.76rem] text-[var(--chrome-fg)] hover:bg-[var(--chrome-hover-bg)]"
                      style={{ contentVisibility: 'auto', containIntrinsicSize: '32px' }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle(track.code)}
                        className="accent-[var(--color-brand)]"
                      />
                      <span className="min-w-0 truncate">{track.label}</span>
                      {track.kind === 'dub' && track.code === primaryCode && (
                        <Badge tone="brand" size="xs" className="ml-auto">
                          {t('exportModal.primary')}
                        </Badge>
                      )}
                    </label>
                  );
                })}
                {filteredTracks.length === 0 && (
                  <div className="px-[8px] py-[16px] text-center text-[0.72rem] text-[var(--chrome-fg-dim)]">
                    {t('dub.no_matches')}
                  </div>
                )}
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
