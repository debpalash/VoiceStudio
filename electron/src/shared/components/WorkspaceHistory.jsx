/**
 * WorkspaceHistory — the right-side, workspace-scoped generation history
 * (spec: docs/specs/voice-studio-unification.md, P1).
 *
 * Lifts the synth-history rows out of the left Sidebar so history lives next
 * to the work that produced it. For the Voice workspace it shows clone + design
 * generations with an [All][Clone][Design] filter; each row reuses the shared
 * <WaveformPlayer> and the same per-item actions the sidebar had
 * (save-as-profile, lock, export, load-config, delete).
 *
 * Pure presentational + local filter state — all data and handlers are passed
 * in from App.jsx (single source of truth for history + profile mutations).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  History,
  Fingerprint,
  Wand2,
  Film,
  AudioWaveform,
  Save,
  Lock,
  Download as DownloadIcon,
  FolderOpen,
  Play,
  Star,
  Trash2,
} from 'lucide-react';
import WaveformPlayer from './WaveformPlayer';
import { API } from '../api/client';

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'clone', label: 'Clone' },
  { id: 'design', label: 'Design' },
  { id: 'starred', label: 'Starred' },
];

/**
 * Row title for display: drop leading [laughter]/[question-en]-style control
 * tokens — they're synthesis instructions, not content, and they were eating
 * the two visible lines. The untouched original stays in the hover tooltip
 * and in restore/save flows.
 */
const displayTitle = (text) => {
  const stripped = (text || '').replace(/^(\s*\[[^\]]{1,30}\]\s*)+/, '').trim();
  return stripped || text || '';
};

const dubInputType = (item) => {
  try {
    const job = typeof item.job_data === 'string' ? JSON.parse(item.job_data) : item.job_data;
    return job?.input_type === 'audio' ? 'audio' : 'video';
  } catch {
    return 'video';
  }
};

function DubMediaPreview({ item, inputType }) {
  const frameClass =
    'relative flex h-[54px] w-[88px] flex-[0_0_88px] items-center justify-center overflow-hidden rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-hover-bg)] text-[color:var(--chrome-fg-dim)]';

  if (inputType === 'audio') {
    return (
      <div className={frameClass} aria-hidden="true">
        <AudioWaveform size={28} strokeWidth={1.5} data-testid={`dub-audio-waveform-${item.id}`} />
      </div>
    );
  }

  return (
    <div className={frameClass} aria-hidden="true">
      <Film size={20} strokeWidth={1.5} />
      <img
        src={`${API}/dub/thumb/${encodeURIComponent(item.id)}`}
        alt=""
        width="88"
        height="54"
        loading="lazy"
        decoding="async"
        className="absolute inset-0 h-full w-full object-cover"
        data-testid={`dub-thumbnail-${item.id}`}
        onError={(event) => {
          event.currentTarget.hidden = true;
        }}
      />
    </div>
  );
}

/**
 * Defer mounting <WaveformPlayer> (and its audio fetch + waveform decode)
 * until the row scrolls into view — the server returns up to 50 history rows,
 * which would otherwise fire 50 simultaneous fetches on panel mount.
 */
function LazyWaveform({ height = 36, className = '', ...rest }) {
  const ref = useRef(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const node = ref.current;
    if (!node || visible) return;
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setVisible(true);
      },
      { rootMargin: '100px' },
    );
    obs.observe(node);
    return () => obs.disconnect();
  }, [visible]);
  if (visible) return <WaveformPlayer height={height} className={className} {...rest} />;
  return <div ref={ref} className={className} style={{ height }} aria-hidden="true" />;
}

export default function WorkspaceHistory({
  title,
  clearLabel,
  variant = 'voice', // 'voice' (clone/design synth) | 'dub'
  history = [],
  dubHistory = [],
  restoreDubHistory,
  handleSaveHistoryAsProfile,
  handleLockProfile,
  handleNativeExport,
  restoreHistory,
  deleteHistory,
  clearHistory, // clear-all for this workspace's history (#1032)
  toggleStarHistory, // generation takes: keep this take past the retention cap
  playTakeAsOutput, // generation takes: replay a take as the active output
}) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState('all');
  const [expanded, setExpanded] = useState(null); // row id with un-clamped title

  // Clear-all affordance (#1032): the old left Sidebar had one; the workspace
  // UX overhaul (#374) moved history here and dropped it. Confirm + endpoint
  // live in App.jsx (clearWorkspaceHistory) — this only renders the button.
  const clearAllButton = (count) =>
    clearHistory && count > 0 ? (
      <button
        type="button"
        className="history-action-btn danger flex-[0_0_auto]"
        onClick={clearHistory}
        title={clearLabel || t('sidebar.clear_history')}
      >
        <Trash2 size={10} /> {clearLabel || t('sidebar.clear_history')}
      </button>
    ) : null;

  // Voice workspace = clone + design generations (dub lives in its own workspace).
  const items = useMemo(() => {
    const synth = history.filter((h) => h.mode === 'clone' || h.mode === 'design');
    if (filter === 'all') return synth;
    if (filter === 'starred') return synth.filter((h) => !!h.starred);
    return synth.filter((h) => h.mode === filter);
  }, [history, filter]);

  // ── Dub variant: a flat list of dub jobs, no clone/design filter. ──
  if (variant === 'dub') {
    return (
      <aside className="flex-[1_1_0] flex flex-col min-h-0 overflow-hidden">
        <div className="flex-[0_0_auto] flex flex-col gap-[8px] py-[10px] px-[12px]">
          <div className="flex items-center justify-between gap-[6px]">
            <span className="inline-flex items-center gap-[6px] [font-family:var(--chrome-font-mono,var(--font-mono))] text-[0.72rem] font-semibold [letter-spacing:0.04em] uppercase text-[color:var(--chrome-fg-muted)]">
              <History size={13} />{' '}
              {title || t('history.dub_title', { defaultValue: 'Dub history' })}
            </span>
            {clearAllButton(dubHistory.length)}
          </div>
        </div>
        <div className="flex-[1_1_auto] min-h-0 overflow-y-auto flex flex-col gap-[8px] p-[8px]">
          {dubHistory.length === 0 ? (
            <div className="text-[color:var(--chrome-fg-dim)] text-[0.72rem] [line-height:1.5] text-center py-[32px] px-[16px]">
              {t('history.empty_dub', { defaultValue: 'Your dubs will appear here.' })}
            </div>
          ) : (
            dubHistory.map((item) => {
              const inputType = dubInputType(item);
              const MediaIcon = inputType === 'audio' ? AudioWaveform : Film;
              return (
                <div key={`dub-${item.id}`} className="history-item history-item--dub relative">
                  <button
                    type="button"
                    className="absolute inset-0 z-[1] cursor-pointer rounded-[inherit] border-0 bg-transparent focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--chrome-accent)]"
                    aria-label={`${t('sidebar.open')}: ${item.filename}`}
                    onClick={() => restoreDubHistory(item)}
                  />
                  <div className="flex min-w-0 gap-[8px]">
                    <DubMediaPreview item={item} inputType={inputType} />
                    <div className="flex min-w-0 flex-1 flex-col gap-[2px]">
                      <div className="flex items-center justify-between gap-2 min-w-0">
                        <span className="history-kind history-kind--audio">
                          <MediaIcon size={9} aria-hidden="true" /> {t('sidebar.dub_label')}
                        </span>
                        <span className="history-meta">
                          {t('history.dub_meta', {
                            count: item.segments_count,
                            segments: item.segments_count,
                            duration: Math.round(item.duration || 0),
                          })}
                        </span>
                      </div>
                      <div className="history-title">{item.filename}</div>
                      <div className="history-subtitle">
                        {[item.language, item.language_code]
                          .filter((v) => v && v !== 'und' && v !== 'Auto')
                          .join(' · ') || t('dub.auto')}
                      </div>
                    </div>
                  </div>
                  <div className="history-actions relative z-[2]">
                    <button
                      type="button"
                      className="history-action-btn accent"
                      onClick={(e) => {
                        e.stopPropagation();
                        restoreDubHistory(item);
                      }}
                    >
                      <FolderOpen size={10} aria-hidden="true" /> {t('sidebar.open')}
                    </button>
                    <button
                      type="button"
                      className="history-action-btn danger history-action-icon"
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteHistory(item.id, 'dub');
                      }}
                      title={t('common.delete')}
                      aria-label={t('common.delete')}
                    >
                      <Trash2 size={10} aria-hidden="true" />
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </aside>
    );
  }

  return (
    <aside className="flex-[1_1_0] flex flex-col min-h-0 overflow-hidden">
      <div className="flex-[0_0_auto] flex flex-col gap-[8px] py-[10px] px-[12px]">
        <div className="flex items-center justify-between gap-[6px]">
          <span className="inline-flex items-center gap-[6px] [font-family:var(--chrome-font-mono,var(--font-mono))] text-[0.72rem] font-semibold [letter-spacing:0.04em] uppercase text-[color:var(--chrome-fg-muted)]">
            <History size={13} /> {t('history.title', { defaultValue: 'History' })}
          </span>
          {clearAllButton(history.length)}
        </div>
        <div className="flex flex-wrap gap-[4px]">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              className={`flex-[0_0_auto] py-[2px] px-[10px] text-[0.68rem] font-medium rounded-[var(--chrome-radius-pill,999px)] cursor-pointer [transition:background_0.15s_ease,color_0.15s_ease] ${
                filter === f.id
                  ? 'text-[color:var(--color-brand,#d3869b)] bg-[color-mix(in_srgb,var(--color-brand,#d3869b)_12%,transparent)]'
                  : 'bg-transparent text-[color:var(--chrome-fg-muted)] hover:bg-[var(--chrome-hover-bg)] hover:text-[color:var(--chrome-fg)]'
              }`}
              onClick={() => setFilter(f.id)}
            >
              {t(`history.filter_${f.id}`, { defaultValue: f.label })}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-[1_1_auto] min-h-0 overflow-y-auto flex flex-col gap-[8px] p-[8px]">
        {items.length === 0 ? (
          <div className="text-[color:var(--chrome-fg-dim)] text-[0.72rem] [line-height:1.5] text-center py-[32px] px-[16px]">
            {t('history.empty', {
              defaultValue: 'Nothing here yet — your generations will appear on the right.',
            })}
          </div>
        ) : (
          items.map((item) => {
            const accent = item.mode === 'clone' ? '#d3869b' : '#b8bb26';
            const KindIcon = item.mode === 'clone' ? Fingerprint : Wand2;
            return (
              <div key={item.id} className="history-item" style={{ '--row-accent': accent }}>
                <div className="flex items-center justify-between gap-2 min-w-0">
                  <span
                    className="history-kind"
                    style={{ color: accent, background: `${accent}22` }}
                  >
                    <KindIcon size={9} aria-hidden="true" />{' '}
                    {item.mode === 'clone' ? t('history.mode_clone') : t('history.mode_synth')}
                  </span>
                  <span className="history-meta">
                    {item.language && item.language !== 'Auto' ? `${item.language} · ` : ''}
                    {item.generation_time
                      ? t('history.generation_seconds', { duration: item.generation_time })
                      : ''}
                  </span>
                </div>
                <button
                  type="button"
                  className={`history-title history-title--clamp ${expanded === item.id ? 'history-title--expanded' : ''}`}
                  title={item.text}
                  onClick={() => setExpanded((e) => (e === item.id ? null : item.id))}
                  aria-expanded={expanded === item.id}
                >
                  {displayTitle(item.text)}
                </button>
                {item.seed != null && String(item.seed) !== '' ? (
                  <div className="history-subtitle history-subtitle--seed">
                    {t('history.seed', { seed: item.seed })}
                  </div>
                ) : null}
                {item.audio_path ? (
                  <LazyWaveform
                    src={`${API}/audio/${item.audio_path}`}
                    source="history"
                    height={36}
                    compact
                    className="history-audio"
                  />
                ) : null}
                {item.audio_path ? (
                  <div className="history-actions">
                    {toggleStarHistory ? (
                      <button
                        type="button"
                        className={`history-action-btn history-action-icon ${item.starred ? 'accent' : ''}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleStarHistory(item);
                        }}
                        aria-pressed={!!item.starred}
                        data-testid={`take-star-${item.id}`}
                        title={
                          item.starred
                            ? t('history.unstar_take', { defaultValue: 'Unstar — allow cleanup' })
                            : t('history.star_take', { defaultValue: 'Star — keep this take' })
                        }
                        aria-label={
                          item.starred
                            ? t('history.unstar_take', { defaultValue: 'Unstar — allow cleanup' })
                            : t('history.star_take', { defaultValue: 'Star — keep this take' })
                        }
                      >
                        <Star
                          size={10}
                          fill={item.starred ? 'currentColor' : 'none'}
                          aria-hidden="true"
                        />
                      </button>
                    ) : null}
                    {playTakeAsOutput ? (
                      <button
                        type="button"
                        className="history-action-btn accent history-action-icon"
                        onClick={(e) => {
                          e.stopPropagation();
                          playTakeAsOutput(item);
                        }}
                        data-testid={`take-play-${item.id}`}
                        title={t('history.play_take', {
                          defaultValue: 'Load as active output',
                        })}
                        aria-label={t('history.play_take', {
                          defaultValue: 'Load as active output',
                        })}
                      >
                        <Play size={10} aria-hidden="true" />
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="history-action-btn accent"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleSaveHistoryAsProfile(item);
                      }}
                    >
                      <Save size={10} aria-hidden="true" /> {t('sidebar.save_label')}
                    </button>
                    {item.profile_id ? (
                      <button
                        type="button"
                        className="history-action-btn accent history-action-icon"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleLockProfile(item.profile_id, item.id, item.seed);
                        }}
                        title={t('sidebar.lock_identity')}
                        aria-label={t('sidebar.lock_identity')}
                      >
                        <Lock size={10} aria-hidden="true" />
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="history-action-btn history-action-icon"
                      onClick={(e) =>
                        handleNativeExport(e, item.audio_path, item.audio_path, item.mode)
                      }
                      title={t('dub.export')}
                      aria-label={t('dub.export')}
                    >
                      <DownloadIcon size={10} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="history-action-btn history-action-icon"
                      onClick={(e) => {
                        e.stopPropagation();
                        restoreHistory(item);
                      }}
                      title={t('history.load_config')}
                      aria-label={t('history.load_config')}
                    >
                      <FolderOpen size={10} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="history-action-btn danger history-action-icon"
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteHistory(item.id, 'synth');
                      }}
                      title={t('common.delete')}
                      aria-label={t('common.delete')}
                    >
                      <Trash2 size={10} aria-hidden="true" />
                    </button>
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
