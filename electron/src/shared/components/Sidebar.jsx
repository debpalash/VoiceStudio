import React, { useState, useMemo, useRef, useEffect } from 'react';
import {
  FolderOpen,
  History,
  DownloadCloud,
  Film,
  Save,
  ChevronDown,
  ChevronUp,
  Fingerprint,
  Wand2,
  Lock,
  Unlock,
  Trash2,
  Check,
  Clock,
  Play,
  Loader,
  Download as DownloadIcon,
  Volume2,
  Search,
  Pencil,
  X,
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import { API } from '../api/client';
import { clearDubHistory } from '../api/dub';
import { clearHistory as clearGenHistory } from '../api/generate';
import { Button } from '../ui';
import WaveformPlayer from './WaveformPlayer';
import { useAppStore } from '../store';
import { useTranslation } from 'react-i18next';
import { askConfirm } from '../utils/dialog';
import { absoluteTime, timeAgo } from '../utils/relativeTime';

const SIDEBAR_TABS = [
  { id: 'projects', icon: FolderOpen, accent: '#b8bb26' },
  { id: 'history', icon: History, accent: '#d3869b' },
  { id: 'downloads', icon: DownloadCloud, accent: '#8ec07c' },
];

// Collapsed-mode restore/open tile (history + export rows). Base box layout;
// the per-kind text color is appended at each call site.
const SIDEBAR_TILE =
  'sidebar-tile w-[36px] h-[36px] shrink-0 flex justify-center items-center rounded-[6px] cursor-pointer bg-[rgba(255,255,255,0.05)] [border:1px_solid_transparent]';

/**
 * Mounts the WaveSurfer-backed player only once its row scrolls into view.
 * The history list can hold ~50 items; eagerly mounting a WaveformPlayer per
 * row would spin up that many WaveSurfer instances, each fetching + decoding
 * its audio file, in the always-mounted sidebar.
 */
function LazyWaveformPlayer({ height = 36, className = '', ...rest }) {
  const holderRef = useRef(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (visible) return;
    const el = holderRef.current;
    if (!el || typeof IntersectionObserver === 'undefined') {
      setVisible(true);
      return;
    }
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setVisible(true);
      },
      { rootMargin: '120px' },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [visible]);
  if (visible) return <WaveformPlayer height={height} className={className} {...rest} />;
  return <div ref={holderRef} className={className} style={{ height }} aria-hidden="true" />;
}

export default function Sidebar(props) {
  const {
    availableTabs = ['projects', 'history', 'downloads'],
    isSidebarProjectsCollapsed,
    setIsSidebarProjectsCollapsed,
    sidebarTab,
    setSidebarTab,
    studioProjects,
    profiles,
    history,
    dubHistory,
    exportHistory,
    dubVideoFile,
    selectedProfile,
    previewLoading,
    saveProject,
    loadProject,
    deleteProject,
    renameProject,
    handleSelectProfile,
    handleDeleteProfile,
    handleOpenVoiceProfile,
    handleUnlockProfile,
    handleLockProfile,
    handlePreviewVoice,
    onOpenVoicePreview,
    restoreHistory,
    restoreDubHistory,
    handleSaveHistoryAsProfile,
    handleNativeExport,
    revealInFolder,
    deleteHistory,
    loadHistory,
    loadDubHistory,
  } = props;

  // Phase 2.2 — read UI + dub state straight from the store.
  const mode = useAppStore((s) => s.mode);
  // Voice ('studio') workspace's define method — scopes which profiles show
  // ('audio' = reference clones, 'design' = designed voices).
  const defineMethod = useAppStore((s) => s.defineMethod);
  const isSidebarCollapsed = useAppStore((s) => s.isSidebarCollapsed);
  const dubStep = useAppStore((s) => s.dubStep);
  const activeProjectId = useAppStore((s) => s.activeProjectId);

  const { t } = useTranslation();
  const [sbQuery, setSbQuery] = useState('');
  // Inline project rename. The Dub landing's own projects panel was retired
  // when that surface became history-only, which left the rename endpoint with
  // no caller anywhere in the UI (#1952). Projects now live only in this rail,
  // so the affordance belongs here.
  const [editingId, setEditingId] = useState(null);
  const [draftName, setDraftName] = useState('');
  const startRename = (proj) => {
    setEditingId(proj.id);
    setDraftName(proj.name || '');
  };
  const cancelRename = () => {
    setEditingId(null);
    setDraftName('');
  };
  const commitRename = (proj) => {
    const next = draftName.trim();
    if (next && next !== proj.name) renameProject?.(proj.id, next);
    cancelRename();
  };
  const qLower = sbQuery.trim().toLowerCase();
  const matchesSearch = (s) => !qLower || (s || '').toLowerCase().includes(qLower);
  const filteredProjects = useMemo(
    () => studioProjects.filter((p) => matchesSearch(p.name) || matchesSearch(p.video_path)),
    [studioProjects, qLower],
  );
  const filteredProfiles = useMemo(
    () => profiles.filter((p) => matchesSearch(p.name) || matchesSearch(p.instruct)),
    [profiles, qLower],
  );
  const filteredHistory = useMemo(
    () =>
      history.filter(
        (i) => matchesSearch(i.text) || matchesSearch(i.language) || matchesSearch(String(i.seed)),
      ),
    [history, qLower],
  );
  const filteredDubHistory = useMemo(
    () =>
      dubHistory.filter(
        (i) =>
          matchesSearch(i.filename) || matchesSearch(i.language) || matchesSearch(i.language_code),
      ),
    [dubHistory, qLower],
  );
  const filteredExport = useMemo(
    () =>
      exportHistory.filter((i) => matchesSearch(i.filename) || matchesSearch(i.destination_path)),
    [exportHistory, qLower],
  );

  const handleClearHistory = async () => {
    if (
      !(await askConfirm(t('sidebar.clear_confirm', { count: history.length + dubHistory.length })))
    )
      return;
    await clearGenHistory();
    await clearDubHistory();
    await loadHistory();
    await loadDubHistory();
    toast.success(t('sidebar.history_cleared'));
  };

  const tabCount = {
    projects:
      mode === 'dub'
        ? studioProjects.length
        : defineMethod === 'audio'
          ? profiles.filter((p) => !p.instruct).length
          : profiles.filter((p) => !!p.instruct).length,
    history: history.length + dubHistory.length,
    downloads: exportHistory.length,
  };
  const tabLabel = {
    projects: t('sidebar.tab_drive'),
    history: t('sidebar.tab_history'),
    downloads: t('sidebar.tab_exports'),
  };

  return (
    <div
      className={`glass-panel history-panel sidebar flex flex-col ${isSidebarCollapsed ? 'is-collapsed' : ''}`}
    >
      {/* Tab bar — only tabs relevant to the current view */}
      <div
        className={`sidebar__tabs flex gap-[var(--space-2)] px-[var(--space-2)] [border-bottom:1px_solid_var(--chrome-border)] bg-[var(--chrome-bg)] shrink-0 justify-center ${isSidebarCollapsed ? 'flex-col py-[var(--space-3)] items-center' : 'py-[var(--space-1)]'}`}
      >
        {SIDEBAR_TABS.filter((t) => availableTabs.includes(t.id)).map(
          ({ id, icon: Icon, accent }) => (
            <button
              key={id}
              onClick={() => setSidebarTab(id)}
              className={`sidebar__tab ${sidebarTab === id ? 'is-active' : ''}`}
              style={{ '--sidebar-tab-accent': accent }}
              title={`${tabLabel[id]} (${tabCount[id]})`}
            >
              <Icon size={isSidebarCollapsed ? 18 : 13} />
              {tabCount[id] > 0 && (
                <span className="sidebar__tab-badge absolute -top-[2px] -right-[2px] font-mono text-[8px] font-bold min-w-[14px] h-[14px] leading-[14px] text-center px-[3px] py-0 rounded-[99px] bg-[color-mix(in_srgb,var(--sidebar-tab-accent)_25%,transparent)] text-[var(--sidebar-tab-accent)]">
                  {tabCount[id]}
                </span>
              )}
            </button>
          ),
        )}
      </div>

      {!isSidebarCollapsed && (
        <div className="sidebar__search px-[4px] pt-[3px] pb-[2px] shrink-0 relative">
          <Search
            size={10}
            className="sidebar__search-icon absolute left-[16px] top-1/2 -translate-y-1/2 text-[var(--color-fg-subtle)] pointer-events-none"
          />
          <input
            className="input-base sidebar__search-input"
            placeholder="Search…"
            value={sbQuery}
            onChange={(e) => setSbQuery(e.target.value)}
          />
          {sbQuery && (
            <Button
              variant="ghost"
              iconSize="sm"
              onClick={() => setSbQuery('')}
              title="Clear"
              className="sidebar__search-clear"
            >
              <X size={10} />
            </Button>
          )}
        </div>
      )}

      <div
        className={`sidebar__scroll flex-1 overflow-y-auto px-[4px] flex flex-col ${isSidebarCollapsed ? 'py-[8px] items-center gap-[8px]' : 'py-[3px] items-stretch gap-0'}`}
      >
        {/* ── PROJECTS TAB ── */}
        {sidebarTab === 'projects' && (
          <>
            {mode === 'dub' &&
              (dubStep !== 'idle' || dubVideoFile) &&
              (isSidebarCollapsed ? (
                <Button
                  variant="subtle"
                  iconSize="md"
                  onClick={saveProject}
                  title={activeProjectId ? 'Save Dub Project' : 'Save as New Dub Project'}
                  className={`sidebar__save-btn ${activeProjectId ? 'is-active-project' : ''}`}
                >
                  <Save size={14} />
                </Button>
              ) : (
                <Button
                  variant="subtle"
                  block
                  onClick={saveProject}
                  leading={<Save size={13} />}
                  className={`sidebar__save-btn sidebar__save-btn--full ${activeProjectId ? 'is-active-project' : ''}`}
                >
                  {activeProjectId ? t('sidebar.save_project') : t('sidebar.save_new_project')}
                </Button>
              ))}

            {!isSidebarCollapsed && (
              <div
                className="sidebar__section-title font-mono text-[length:var(--chrome-label-size)] font-semibold tracking-[var(--chrome-label-track)] uppercase text-[color:var(--chrome-fg-muted)] mb-[var(--space-2)] flex justify-between items-center cursor-pointer py-[4px] px-0"
                onClick={() => setIsSidebarProjectsCollapsed(!isSidebarProjectsCollapsed)}
              >
                <span>
                  {mode === 'dub'
                    ? t('sidebar.dub_projects')
                    : defineMethod === 'audio'
                      ? t('sidebar.voice_clones')
                      : t('sidebar.designed_voices')}
                </span>
                {isSidebarProjectsCollapsed ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
              </div>
            )}

            {!isSidebarProjectsCollapsed && !isSidebarCollapsed && (
              <>
                {mode === 'dub' && (
                  <>
                    {filteredProjects.length === 0 ? (
                      <EmptyState
                        icon={Film}
                        title={t('sidebar.no_dub_projects')}
                        hint={t('sidebar.no_dub_hint')}
                      />
                    ) : (
                      filteredProjects.map((proj) => (
                        <div
                          key={proj.id}
                          className={`history-item history-item--dub ${activeProjectId === proj.id ? 'project-active' : ''}`}
                          onClick={() => loadProject(proj.id)}
                        >
                          <div className="flex items-center justify-between gap-2 min-w-0">
                            <span className="history-kind history-kind--audio">
                              <Film size={9} /> {t('sidebar.dub_label')}
                            </span>
                            <span className="history-meta" title={absoluteTime(proj.updated_at)}>
                              {timeAgo(proj.updated_at)}
                            </span>
                          </div>
                          {editingId === proj.id ? (
                            <input
                              className="input-base wv__rename-input"
                              autoFocus
                              value={draftName}
                              aria-label={t('sidebar.rename')}
                              onClick={(e) => e.stopPropagation()}
                              onChange={(e) => setDraftName(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') {
                                  e.stopPropagation();
                                  commitRename(proj);
                                } else if (e.key === 'Escape') {
                                  e.stopPropagation();
                                  cancelRename();
                                }
                              }}
                              onBlur={() => commitRename(proj)}
                            />
                          ) : (
                            <div className="history-title">{proj.name}</div>
                          )}
                          <div className="history-subtitle">
                            {proj.duration ? `${Math.round(proj.duration)}s` : 'audio'}
                            {(() => {
                              const basename = proj.video_path
                                ? proj.video_path.split(/[\\/]/).pop()
                                : '';
                              // Skip echoing the filename when it already matches the project name.
                              return basename && basename !== proj.name ? ` · ${basename}` : '';
                            })()}
                          </div>
                          <div className="history-actions">
                            {editingId === proj.id ? (
                              <>
                                <button
                                  className="history-action-btn accent"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    commitRename(proj);
                                  }}
                                >
                                  <Check size={10} /> {t('common.save')}
                                </button>
                                <button
                                  className="history-action-btn history-action-icon"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    cancelRename();
                                  }}
                                  title={t('common.cancel')}
                                >
                                  <X size={10} />
                                </button>
                              </>
                            ) : (
                              <>
                                <button
                                  className="history-action-btn accent"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    loadProject(proj.id);
                                  }}
                                >
                                  <FolderOpen size={10} /> {t('sidebar.open')}
                                </button>
                                {renameProject && (
                                  <button
                                    className="history-action-btn history-action-icon"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      startRename(proj);
                                    }}
                                    title={t('sidebar.rename')}
                                  >
                                    <Pencil size={10} />
                                  </button>
                                )}
                                <button
                                  className="history-action-btn danger history-action-icon"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    deleteProject(proj.id);
                                  }}
                                  title={t('common.delete')}
                                >
                                  <Trash2 size={10} />
                                </button>
                              </>
                            )}
                          </div>
                        </div>
                      ))
                    )}
                  </>
                )}

                {mode === 'studio' && (
                  <>
                    {filteredProfiles.filter((p) =>
                      defineMethod === 'audio' ? !p.instruct : !!p.instruct,
                    ).length === 0 ? (
                      <EmptyState
                        icon={defineMethod === 'audio' ? Fingerprint : Wand2}
                        title={`${defineMethod === 'audio' ? t('sidebar.no_clones') : t('sidebar.no_designs')}`}
                        hint={
                          defineMethod === 'audio'
                            ? t('sidebar.no_clones_hint')
                            : t('sidebar.no_designs_hint')
                        }
                      />
                    ) : (
                      (defineMethod === 'audio'
                        ? filteredProfiles.filter((p) => !p.instruct)
                        : filteredProfiles.filter((p) => !!p.instruct)
                      ).map((proj) => {
                        const accent = proj.is_locked
                          ? '#b8bb26'
                          : defineMethod === 'audio'
                            ? '#d3869b'
                            : '#8ec07c';
                        const KindIcon = proj.is_locked
                          ? Lock
                          : defineMethod === 'audio'
                            ? Fingerprint
                            : Wand2;
                        return (
                          <div
                            key={proj.id}
                            className={`history-item ${selectedProfile === proj.id ? 'project-active' : ''}`}
                            style={{ '--row-accent': accent }}
                            onClick={() => handleSelectProfile(proj)}
                          >
                            <div className="flex items-center justify-between gap-2 min-w-0">
                              <span
                                className="history-kind"
                                style={{ color: accent, background: `${accent}22` }}
                              >
                                <KindIcon size={9} />{' '}
                                {proj.is_locked
                                  ? t('sidebar.locked')
                                  : defineMethod === 'audio'
                                    ? t('sidebar.clone_label')
                                    : t('sidebar.design_label')}
                              </span>
                              {proj.is_locked ? (
                                <span className="history-meta history-meta--locked">
                                  {t('sidebar.consistent')}
                                </span>
                              ) : null}
                            </div>
                            <div className="history-title">{proj.name}</div>
                            {proj.instruct ? (
                              <div className="history-subtitle history-subtitle--italic">
                                {proj.instruct}
                              </div>
                            ) : null}

                            <div className="history-actions">
                              <button
                                className="history-action-btn history-action-icon"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handlePreviewVoice(proj, e);
                                }}
                                title="Preview"
                              >
                                {previewLoading === proj.id ? (
                                  <Loader className="spinner" size={10} />
                                ) : (
                                  <Play size={10} />
                                )}
                              </button>
                              {handleOpenVoiceProfile && (
                                <button
                                  className="history-action-btn"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleOpenVoiceProfile(proj.id);
                                  }}
                                  title="Open full profile"
                                >
                                  Open
                                </button>
                              )}
                              <button
                                className="history-action-btn"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleSelectProfile(proj);
                                }}
                              >
                                <Check size={10} /> {t('sidebar.select')}
                              </button>
                              {onOpenVoicePreview && (
                                <button
                                  className="history-action-btn accent"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    onOpenVoicePreview(proj.id);
                                  }}
                                  title="Open interactive voice preview"
                                >
                                  <Volume2 size={10} /> {t('sidebar.try_voice')}
                                </button>
                              )}
                              {proj.is_locked ? (
                                <button
                                  className="history-action-btn accent history-action-icon"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleUnlockProfile(proj.id);
                                  }}
                                  title="Unlock"
                                >
                                  <Unlock size={10} />
                                </button>
                              ) : null}
                              <button
                                className="history-action-btn danger history-action-icon"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleDeleteProfile(proj.id);
                                }}
                                title="Delete"
                              >
                                <Trash2 size={10} />
                              </button>
                            </div>
                          </div>
                        );
                      })
                    )}
                  </>
                )}
              </>
            )}

            {isSidebarCollapsed &&
              mode === 'dub' &&
              filteredProjects.map((proj) => (
                <IconTile
                  key={proj.id}
                  title={`Load: ${proj.name}`}
                  onClick={() => loadProject(proj.id)}
                  active={activeProjectId === proj.id}
                  rotSeed={proj.id}
                >
                  <Film size={18} />
                </IconTile>
              ))}

            {isSidebarCollapsed &&
              mode === 'studio' &&
              (defineMethod === 'audio'
                ? filteredProfiles.filter((p) => !p.instruct)
                : filteredProfiles.filter((p) => !!p.instruct)
              ).map((proj) => (
                <IconTile
                  key={proj.id}
                  title={`Select: ${proj.name}`}
                  onClick={() => handleSelectProfile(proj)}
                  active={selectedProfile === proj.id}
                  rotSeed={proj.id}
                >
                  {defineMethod === 'audio' ? <Fingerprint size={18} /> : <Wand2 size={18} />}
                  {proj.is_locked && (
                    <Lock
                      size={8}
                      className="sidebar__icon-tile__lock absolute bottom-[2px] right-[2px] text-[#b8bb26]"
                    />
                  )}
                </IconTile>
              ))}
          </>
        )}

        {/* ── HISTORY TAB ── */}
        {sidebarTab === 'history' && (
          <>
            {!isSidebarCollapsed && (
              <div className="sidebar__subtitle text-[length:var(--text-sm)] text-[color:var(--text-secondary)] mb-[var(--space-4)]">
                {t('sidebar.history_subtitle')}
              </div>
            )}
            {history.length + dubHistory.length === 0 ? (
              <EmptyState
                icon={History}
                title={t('sidebar.no_history')}
                hint={t('sidebar.no_history_hint')}
              />
            ) : (
              <>
                {!isSidebarCollapsed &&
                  filteredDubHistory.map((item) => (
                    <div
                      key={`dub-${item.id}`}
                      className="history-item history-item--dub"
                      onClick={() => restoreDubHistory(item)}
                    >
                      <div className="flex items-center justify-between gap-2 min-w-0">
                        <span className="history-kind history-kind--audio">
                          <Film size={9} /> {t('sidebar.dub_label')}
                        </span>
                        <span className="history-meta">
                          {item.segments_count} segs · {Math.round(item.duration || 0)}s
                        </span>
                      </div>
                      <div className="history-title">{item.filename}</div>
                      <div className="history-subtitle">
                        {[item.language, item.language_code]
                          .filter((v) => v && v !== 'und' && v !== 'Auto')
                          .join(' · ') || 'Auto'}
                      </div>
                      <div className="history-actions">
                        <button
                          className="history-action-btn accent"
                          onClick={(e) => {
                            e.stopPropagation();
                            restoreDubHistory(item);
                          }}
                        >
                          <FolderOpen size={10} /> {t('sidebar.open')}
                        </button>
                        <button
                          className="history-action-btn danger history-action-icon"
                          onClick={(e) => {
                            e.stopPropagation();
                            deleteHistory(item.id, 'dub');
                          }}
                          title="Delete"
                        >
                          <Trash2 size={10} />
                        </button>
                      </div>
                    </div>
                  ))}

                {!isSidebarCollapsed &&
                  filteredHistory.map((item) => {
                    const accent = item.mode === 'clone' ? '#d3869b' : '#b8bb26';
                    const KindIcon = item.mode === 'clone' ? Fingerprint : Wand2;
                    return (
                      <div
                        key={item.id}
                        className="history-item"
                        style={{ '--row-accent': accent }}
                      >
                        <div className="flex items-center justify-between gap-2 min-w-0">
                          <span
                            className="history-kind"
                            style={{ color: accent, background: `${accent}22` }}
                          >
                            <KindIcon size={9} /> {item.mode || 'synth'}
                          </span>
                          <span className="history-meta">
                            {item.language && item.language !== 'Auto' ? `${item.language} · ` : ''}
                            {item.generation_time ? `${item.generation_time}s` : ''}
                          </span>
                        </div>
                        <div className="history-title history-title--clamp" title={item.text}>
                          {item.text}
                        </div>
                        {item.seed != null && String(item.seed) !== '' ? (
                          <div className="history-subtitle history-subtitle--seed">
                            seed {item.seed}
                          </div>
                        ) : null}
                        {item.audio_path ? (
                          <LazyWaveformPlayer
                            src={`${API}/audio/${item.audio_path}`}
                            source="history"
                            height={36}
                            compact
                            className="history-audio"
                          />
                        ) : null}
                        {item.audio_path ? (
                          <div className="history-actions">
                            <button
                              className="history-action-btn accent"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleSaveHistoryAsProfile(item);
                              }}
                            >
                              <Save size={10} /> {t('sidebar.save_label')}
                            </button>
                            {item.profile_id ? (
                              <button
                                className="history-action-btn accent history-action-icon"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleLockProfile(item.profile_id, item.id, item.seed);
                                }}
                                title={t('sidebar.lock_identity')}
                              >
                                <Lock size={10} />
                              </button>
                            ) : null}
                            <button
                              className="history-action-btn history-action-icon"
                              onClick={(e) =>
                                handleNativeExport(e, item.audio_path, item.audio_path, item.mode)
                              }
                              title="Export"
                            >
                              <DownloadIcon size={10} />
                            </button>
                            <button
                              className="history-action-btn history-action-icon"
                              onClick={(e) => {
                                e.stopPropagation();
                                restoreHistory(item);
                              }}
                              title="Load config"
                            >
                              <FolderOpen size={10} />
                            </button>
                            <button
                              className="history-action-btn danger history-action-icon"
                              onClick={(e) => {
                                e.stopPropagation();
                                deleteHistory(item.id, 'synth');
                              }}
                              title="Delete"
                            >
                              <Trash2 size={10} />
                            </button>
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
              </>
            )}

            {isSidebarCollapsed &&
              filteredDubHistory.map((item) => (
                <div
                  key={`dub-${item.id}`}
                  title={`Dub: ${item.filename}`}
                  onClick={() => restoreDubHistory(item)}
                  className={`${SIDEBAR_TILE} text-[#83a598]`}
                >
                  <Film size={18} />
                </div>
              ))}

            {isSidebarCollapsed &&
              filteredHistory.map((item) => (
                <div
                  key={item.id}
                  title={`${item.mode || 'history'}: ${item.text}`}
                  onClick={() => restoreHistory(item)}
                  className={`${SIDEBAR_TILE} ${item.mode === 'clone' ? 'text-[#d3869b]' : 'text-[#b8bb26]'}`}
                >
                  {item.mode === 'clone' ? <Fingerprint size={18} /> : <Wand2 size={18} />}
                </div>
              ))}

            {history.length + dubHistory.length > 0 && !isSidebarCollapsed && (
              <Button
                variant="ghost"
                size="sm"
                block
                onClick={handleClearHistory}
                leading={<Trash2 size={10} />}
                className="sidebar__clear"
              >
                {t('sidebar.clear_history')}
              </Button>
            )}
          </>
        )}

        {/* ── DOWNLOADS TAB ── */}
        {sidebarTab === 'downloads' && (
          <>
            {!isSidebarCollapsed && (
              <div className="sidebar__subtitle text-[length:var(--text-sm)] text-[color:var(--text-secondary)] mb-[var(--space-4)]">
                {t('sidebar.recent_exports')}
              </div>
            )}
            {exportHistory.length === 0 ? (
              <EmptyState
                icon={DownloadCloud}
                title={t('sidebar.no_exports')}
                hint={t('sidebar.no_exports_hint')}
              />
            ) : (
              <>
                {!isSidebarCollapsed &&
                  filteredExport.map((item) => {
                    const pathParts = item.destination_path.split(/[\\/]/);
                    const parentFolder =
                      pathParts.length > 1 ? pathParts[pathParts.length - 2] : '…';
                    const accent = item.mode === 'audio' ? '#83a598' : '#8ec07c';
                    const KindIcon = item.mode === 'audio' ? Volume2 : Film;
                    return (
                      <div
                        key={item.id}
                        className="history-item"
                        style={{ '--row-accent': accent }}
                        onClick={() => revealInFolder(item.destination_path)}
                      >
                        <div className="flex items-center justify-between gap-2 min-w-0">
                          <span
                            className="history-kind"
                            style={{ color: accent, background: `${accent}22` }}
                          >
                            <KindIcon size={9} /> {item.mode}
                          </span>
                          <span className="history-meta">{timeAgo(item.created_at)}</span>
                        </div>
                        <div className="history-title">{item.filename}</div>
                        <div className="history-subtitle">
                          {t('sidebar.in_folder', { folder: parentFolder })}
                        </div>
                        <div className="history-actions">
                          <button
                            className="history-action-btn accent"
                            onClick={(e) => {
                              e.stopPropagation();
                              revealInFolder(item.destination_path);
                            }}
                          >
                            <FolderOpen size={10} /> {t('sidebar.show_in_folder')}
                          </button>
                        </div>
                      </div>
                    );
                  })}

                {isSidebarCollapsed &&
                  filteredExport.map((item) => (
                    <div
                      key={item.id}
                      title={`Exported: ${item.filename}\nClick to open folder`}
                      onClick={() => revealInFolder(item.destination_path)}
                      className={`${SIDEBAR_TILE} ${item.mode === 'audio' ? 'text-[#83a598]' : 'text-[#8ec07c]'}`}
                    >
                      <FolderOpen size={18} />
                    </div>
                  ))}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/**
 * EmptyState — shared "nothing here yet" card across the three sidebar tabs.
 */
function EmptyState({ icon: Icon, title, hint }) {
  return (
    <div className="sidebar__empty text-[var(--chrome-fg-muted)] text-center px-[12px] py-[24px] font-sans">
      <Icon
        size={28}
        className="sidebar__empty-icon opacity-30 mb-[var(--space-4)] text-[var(--chrome-fg-dim)]"
      />
      <p className="sidebar__empty-title text-[0.82rem] m-0 mb-[var(--space-2)] text-[var(--chrome-fg)] font-medium tracking-[0.02em]">
        {title}
      </p>
      <p className="sidebar__empty-sub text-[0.7rem] m-0 text-[var(--chrome-fg-dim)] leading-[1.5]">
        {hint}
      </p>
    </div>
  );
}

/**
 * IconTile — hand-drawn sticker-style tile used in the collapsed-sidebar grid.
 * Deterministic rotation based on the id's last char keeps tiles wonky but stable.
 */
function IconTile({ children, title, onClick, active, rotSeed }) {
  const tilt = ((parseInt((rotSeed || '0').slice(-1), 36) % 5) - 2) * 0.8;
  return (
    <div
      title={title}
      onClick={onClick}
      className={`sidebar__icon-tile w-[36px] h-[36px] shrink-0 flex justify-center items-center rounded-[var(--chrome-radius-pill)] cursor-pointer relative bg-transparent [border:1px_solid_transparent] text-[var(--chrome-fg-muted)] [transition:background_var(--dur-fast)_var(--ease-out),border-color_var(--dur-fast)_var(--ease-out),color_var(--dur-fast)_var(--ease-out)] ${active ? 'is-active' : ''}`}
      style={{ transform: `rotate(${tilt}deg)` }}
    >
      {children}
    </div>
  );
}
