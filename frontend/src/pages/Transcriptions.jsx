/**
 * TranscriptionsPage — history of dictation transcriptions.
 *
 * Stores transcriptions in localStorage and displays them in a searchable,
 * timestamped list. Each entry can be copied, deleted, or re-used.
 *
 * Reactivity: addTranscription() dispatches a custom window event so the
 * page updates in realtime without requiring a shared store.
 */
import React, { useState, useCallback, useMemo, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Mic, Copy, Trash2, Search, Clock, Languages, FileText, Download } from 'lucide-react';
import { Button } from '../ui';
import EngineQuickSwitch from '../components/EngineQuickSwitch';
import { toast } from 'react-hot-toast';
import { toMillis } from '../utils/relativeTime';
import { useEffectiveDictationShortcut } from '../hooks/useEffectiveDictationShortcut';
import { requestDictationCapture } from '../utils/dictationCapture';
import {
  loadTranscriptions,
  TRANSCRIPTIONS_KEY,
  TRANSCRIPTION_EVENT,
} from '../utils/transcriptionsStore';

function saveTranscriptions(list) {
  localStorage.setItem(TRANSCRIPTIONS_KEY, JSON.stringify(list));
}

/** A segment's "12.0s – 15.5s" label, tolerant of missing timings (#1798).
 *
 * Not every ASR path produces a timed segment: an OpenAI-compatible backend
 * answering in `json`/`text` format has no timings at all, and
 * `services/asr_backend.py` records that honestly as `end: None` rather than
 * inventing a number. Calling `.toFixed()` on it threw during render and took
 * the whole Transcriptions view down, so a transcript that merely lacked
 * timings became one the user could not read at all. Render whichever half is
 * known, and nothing when neither is. */
export function segTimeRange(seg) {
  const known = (v) => typeof v === 'number' && Number.isFinite(v);
  const start = known(seg?.start) ? `${seg.start.toFixed(1)}s` : null;
  const end = known(seg?.end) ? `${seg.end.toFixed(1)}s` : null;
  if (start && end) return `${start} – ${end}`;
  return start || end || '';
}

export function addTranscription(entry) {
  const list = loadTranscriptions();
  const newEntry = {
    id: Date.now(),
    text: entry.text || '',
    language: entry.language || 'unknown',
    duration_s: entry.duration_s || 0,
    segments: entry.segments || [],
    timestamp: new Date().toISOString(),
  };
  list.unshift(newEntry);
  // Keep last 200
  if (list.length > 200) list.length = 200;
  saveTranscriptions(list);
  // Fire custom event for reactive updates
  window.dispatchEvent(new CustomEvent(TRANSCRIPTION_EVENT, { detail: newEntry }));
}

export default function TranscriptionsPage() {
  const { t } = useTranslation();
  const [transcriptions, setTranscriptions] = useState(loadTranscriptions);
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState(null);
  const { info: shortcut } = useEffectiveDictationShortcut();
  const emptyDescription = t('transcriptions.empty_desc', { shortcut: shortcut.display });
  const normalizedSearch = search.trim();

  const startCapture = useCallback(async () => {
    try {
      await requestDictationCapture('start');
    } catch (error) {
      console.warn('Could not start dictation:', error);
      toast.error(t('transcriptions.capture_failed'));
    }
  }, [t]);

  // Listen for new transcriptions added from CaptureButton
  useEffect(() => {
    const handler = () => {
      setTranscriptions(loadTranscriptions());
    };
    window.addEventListener(TRANSCRIPTION_EVENT, handler);
    return () => window.removeEventListener(TRANSCRIPTION_EVENT, handler);
  }, []);

  const filtered = useMemo(() => {
    if (!normalizedSearch) return transcriptions;
    const q = normalizedSearch.toLowerCase();
    return transcriptions.filter(
      (t) => t.text.toLowerCase().includes(q) || (t.language || '').toLowerCase().includes(q),
    );
  }, [transcriptions, normalizedSearch]);

  const selected = useMemo(
    () => transcriptions.find((t) => t.id === selectedId),
    [transcriptions, selectedId],
  );

  const copyText = useCallback(
    (text) => {
      copyText(text).then(
        () => toast.success(t('transcriptions.copied')),
        () => toast.error(t('transcriptions.copy_failed')),
      );
    },
    [t],
  );

  const deleteEntry = useCallback(
    (id) => {
      const next = transcriptions.filter((t) => t.id !== id);
      setTranscriptions(next);
      saveTranscriptions(next);
      if (selectedId === id) setSelectedId(null);
    },
    [transcriptions, selectedId],
  );

  const clearAll = useCallback(() => {
    setTranscriptions([]);
    saveTranscriptions([]);
    setSelectedId(null);
  }, []);

  const exportAll = useCallback(() => {
    const text = transcriptions
      .map((t) => `[${new Date(t.timestamp).toLocaleString()}] (${t.language})\n${t.text}\n`)
      .join('\n---\n\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `transcriptions_${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success(t('transcriptions.exported'));
  }, [transcriptions]);

  // toMillis keeps this unit-safe (ISO strings today; seconds/ms tolerated)
  // and guards unparseable stamps, which used to render "Invalid Date".
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

  return (
    <div
      className="txn-page flex flex-col h-full px-[24px] py-[20px] gap-[16px] font-sans"
      role="region"
      aria-label={t('transcriptions.title')}
    >
      {/* Header */}
      <div className="txn-header flex flex-wrap items-center justify-between gap-[12px]">
        <div className="txn-header__left flex items-center gap-[12px]">
          <h1 className="txn-header__title flex items-center gap-[8px] text-[var(--text-xl)] font-semibold text-fg">
            <FileText size={20} />
            {t('transcriptions.title')}
          </h1>
          <span className="txn-header__count rounded-[var(--radius-pill)] [border:1px_solid_var(--color-border)] bg-bg-elev-1 px-[10px] py-[2px] text-[var(--text-xs)] text-fg-subtle">
            {t('transcriptions.entries', { count: transcriptions.length })}
          </span>
        </div>
        <div className="txn-header__right flex items-center gap-[6px]">
          <EngineQuickSwitch family="asr" />
          <Button size="sm" variant="primary" onClick={startCapture}>
            <Mic size={13} /> {t('transcriptions.capture')}
          </Button>
          <div className="txn-search relative flex items-center">
            <Search
              size={13}
              className="txn-search__icon absolute left-[10px] pointer-events-none text-fg-subtle"
            />
            <input
              className="w-[220px] bg-bg-elev-1 [border:1px_solid_var(--color-border)] rounded-[var(--radius-md)] text-fg [font-size:var(--text-sm)] [font-family:var(--font-sans)] [padding:6px_10px_6px_30px] [transition:border-color_0.15s] focus:border-brand focus:outline-none placeholder:text-fg-subtle"
              placeholder={t('transcriptions.search_placeholder')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label={t('transcriptions.search_placeholder')}
            />
          </div>
          {transcriptions.length > 0 && (
            <>
              <Button
                size="sm"
                variant="ghost"
                onClick={exportAll}
                title={t('transcriptions.export_title')}
              >
                <Download size={13} /> {t('transcriptions.export')}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={clearAll}
                title={t('transcriptions.clear_title')}
              >
                <Trash2 size={13} /> {t('transcriptions.clear')}
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="txn-content grid flex-1 grid-cols-[1fr_1fr] gap-[12px] min-h-0">
        {/* List */}
        <div className="txn-list flex flex-col gap-[4px] overflow-y-auto pr-[4px]" role="list">
          {filtered.length === 0 ? (
            <div className="txn-empty flex h-full flex-col items-center justify-center gap-[8px] px-[20px] py-[40px] text-center text-fg-muted">
              <Mic size={32} className="txn-empty__icon opacity-30" />
              <p className="txn-empty__title m-0 text-[var(--text-sm)] font-medium text-fg">
                {normalizedSearch
                  ? t('transcriptions.empty_search_title')
                  : t('transcriptions.empty_title')}
              </p>
              <p className="txn-empty__desc m-0 max-w-[280px] text-[var(--text-xs)] leading-[1.6] text-fg-muted">
                {normalizedSearch ? t('transcriptions.empty_search_desc') : emptyDescription}
              </p>
              {!normalizedSearch && (
                <Button size="sm" variant="primary" onClick={startCapture}>
                  <Mic size={13} /> {t('transcriptions.capture')}
                </Button>
              )}
            </div>
          ) : (
            filtered.map((t) => (
              <div
                key={t.id}
                role="listitem"
                className={`py-[10px] px-[12px] bg-bg-elev-1 [border:1px_solid_var(--color-border)] rounded-[var(--radius-lg)] cursor-pointer [transition:border-color_0.15s,box-shadow_0.15s] hover:[border-color:var(--color-border-strong)] ${
                  selectedId === t.id
                    ? '[border-color:var(--color-brand)] [box-shadow:0_0_0_1px_var(--color-brand-glow)]'
                    : ''
                }`}
                onClick={() => setSelectedId(t.id)}
              >
                <div className="txn-item__text mb-[6px] text-[var(--text-sm)] leading-[1.5] text-fg [word-break:break-word]">
                  {t.text.length > 120 ? t.text.slice(0, 120) + '…' : t.text}
                </div>
                <div className="txn-item__meta flex items-center gap-[10px] text-[10px] text-fg-subtle">
                  <span className="txn-item__time flex items-center gap-[3px]">
                    <Clock size={10} /> {formatTime(t.timestamp)}
                  </span>
                  {t.language && t.language !== 'unknown' && (
                    <span className="txn-item__lang flex items-center gap-[3px]">
                      <Languages size={10} /> {t.language}
                    </span>
                  )}
                  {t.duration_s > 0 && (
                    <span className="txn-item__dur flex items-center gap-[3px]">
                      {t.duration_s.toFixed(1)}s
                    </span>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        {/* Detail panel */}
        {selected && (
          <div className="txn-detail flex flex-col [border:1px_solid_var(--color-border)] bg-bg-elev-1 rounded-lg overflow-hidden">
            <div className="txn-detail__header flex items-center justify-between px-[14px] py-[10px] [border-bottom:1px_solid_var(--color-border)]">
              <span className="txn-detail__time text-[var(--text-xs)] text-fg-muted">
                {new Date(selected.timestamp).toLocaleString()}
              </span>
              <div className="txn-detail__actions flex gap-[4px]">
                <Button size="sm" variant="ghost" onClick={() => copyText(selected.text)}>
                  <Copy size={12} /> {t('transcriptions.copy')}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => deleteEntry(selected.id)}>
                  <Trash2 size={12} /> {t('transcriptions.delete')}
                </Button>
              </div>
            </div>
            <div className="txn-detail__body flex-1 p-[14px] overflow-y-auto">
              <p className="txn-detail__text m-0 text-[var(--text-sm)] leading-[1.7] whitespace-pre-wrap text-fg">
                {selected.text}
              </p>
            </div>
            {selected.segments && selected.segments.length > 0 && (
              <div className="txn-detail__segments [border-top:1px_solid_var(--color-border)] px-[14px] py-[10px] max-h-[200px] overflow-y-auto">
                <div className="[font-size:var(--text-xs)] [font-weight:var(--weight-semibold)] text-fg-muted m-0 mb-[6px] uppercase tracking-[0.5px]">
                  {t('transcriptions.segments_title')}
                </div>
                {selected.segments.map((seg, i) => (
                  <div
                    key={i}
                    className="txn-detail__seg flex gap-[8px] py-[3px] text-[var(--text-xs)]"
                  >
                    <span className="txn-detail__seg-time shrink-0 font-mono text-fg-subtle min-w-[80px]">
                      {segTimeRange(seg)}
                    </span>
                    <span className="txn-detail__seg-text text-fg">{seg.text}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
