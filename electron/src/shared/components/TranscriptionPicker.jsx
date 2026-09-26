import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Mic, Search, Clock, Languages } from 'lucide-react';
import { Dialog, Input } from '../ui';
import { toMillis } from '../utils/relativeTime';
import { loadTranscriptions, TRANSCRIPTION_EVENT } from '../utils/transcriptionsStore';

/**
 * A controlled modal that lets the user seed long-form work from a past
 * dictation (#23). It does NOT know about Audiobook vs Stories — it only emits
 * the picked entry via `onPick`. Reads localStorage directly; all text via t().
 *
 * @param {{ open: boolean, onClose: () => void, onPick: (entry: object) => void }} props
 */
export default function TranscriptionPicker({ open, onClose, onPick }) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState([]);
  const [search, setSearch] = useState('');

  // Read on open; subscribe to live additions only while open.
  useEffect(() => {
    if (!open) return undefined;
    setSearch('');
    setEntries(loadTranscriptions());
    const handler = () => setEntries(loadTranscriptions());
    window.addEventListener(TRANSCRIPTION_EVENT, handler);
    return () => window.removeEventListener(TRANSCRIPTION_EVENT, handler);
  }, [open]);

  // Relative time, host-locale absolute fallback; null on unparseable timestamp.
  // toMillis keeps this unit-safe (ISO strings today; seconds/ms tolerated).
  const formatTime = (iso) => {
    const ms = toMillis(iso);
    if (ms == null) return null;
    const d = new Date(ms);
    const diff = Date.now() - ms;
    if (diff < 60000) return t('transcriptions.just_now');
    if (diff < 3600000) return t('transcriptions.m_ago', { count: Math.floor(diff / 60000) });
    if (diff < 86400000) return t('transcriptions.h_ago', { count: Math.floor(diff / 3600000) });
    return d.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  // Per-row display normalization; hide empty-text rows entirely.
  const rows = useMemo(
    () =>
      entries
        .map((e, idx) => ({
          entry: e,
          key: e?.id ?? idx,
          text: String(e?.text ?? ''),
          language: e?.language && e.language !== 'unknown' ? e.language : null,
          duration: typeof e?.duration_s === 'number' && e.duration_s > 0 ? e.duration_s : null,
          time: formatTime(e?.timestamp),
        }))
        .filter((r) => r.text.trim()),
    [entries],
  ); // eslint-disable-line react-hooks/exhaustive-deps

  const q = search.trim().toLowerCase();
  const visible = q
    ? rows.filter(
        (r) => r.text.toLowerCase().includes(q) || (r.language || '').toLowerCase().includes(q),
      )
    : rows;

  return (
    <Dialog open={open} onClose={onClose} title={t('transcriptionPicker.title')} size="md">
      {rows.length > 0 && (
        <div className="txn-picker__search flex items-center gap-[6px] mb-[10px] text-[var(--chrome-fg-muted,#999)]">
          <Search size={13} />
          <Input
            size="sm"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('transcriptionPicker.search_placeholder')}
            aria-label={t('transcriptionPicker.search_placeholder')}
          />
        </div>
      )}

      {visible.length === 0 ? (
        <div className="txn-picker__empty flex flex-col items-center gap-[8px] px-[12px] py-[28px] text-[var(--chrome-fg-muted,#999)] text-center">
          <Mic size={24} />
          <p>
            {rows.length === 0
              ? t('transcriptionPicker.empty')
              : t('transcriptionPicker.empty_search')}
          </p>
        </div>
      ) : (
        <div
          className="txn-picker__list flex flex-col gap-[6px] max-h-[50vh] overflow-y-auto"
          role="list"
        >
          {visible.map((r) => (
            <button
              type="button"
              key={r.key}
              className="txn-picker__row flex flex-col gap-[4px] w-full text-left px-[10px] py-[8px] cursor-pointer [border:1px_solid_var(--chrome-border,rgba(255,255,255,0.12))] rounded-[6px] bg-transparent text-[var(--chrome-fg,#eee)]"
              onClick={() => {
                onPick(r.entry);
                onClose();
              }}
            >
              <span className="txn-picker__text text-[length:var(--text-sm,0.85rem)] leading-[1.35]">
                {r.text.length > 120 ? `${r.text.slice(0, 120)}…` : r.text}
              </span>
              <span className="txn-picker__meta flex gap-[10px] flex-wrap text-[0.7rem] text-[var(--chrome-fg-muted,#999)]">
                {r.time && (
                  <span>
                    <Clock size={10} /> {r.time}
                  </span>
                )}
                {r.language && (
                  <span>
                    <Languages size={10} /> {r.language}
                  </span>
                )}
                {r.duration && <span>{r.duration.toFixed(1)}s</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </Dialog>
  );
}
