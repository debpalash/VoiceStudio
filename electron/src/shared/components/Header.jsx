import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import GpuTarget from './GpuTarget';
import EngineQuickSwitch from './EngineQuickSwitch';
import { engineDisplayName } from '../utils/engineDisplayName';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './ui/tabs';
import { createPortal } from 'react-dom';
import {
  Globe,
  Fingerprint,
  Wand2,
  Film,
  FolderOpen,
  RefreshCw,
  Settings2,
  ChevronRight,
  ChevronDown,
  Zap,
  Building2,
  Library,
  FileText,
  Boxes,
  Trash2,
  Minus,
  Square,
  X,
} from 'lucide-react';
import { Button, Badge } from '../ui';
import NotificationPanel from './NotificationPanel';
import TitleTabs from './TitleTabs';
import VoiceStudioMark from './brand/VoiceStudioMark';
import { useAppStore } from '../store';
import { useSysinfo, useEngines } from '../api/hooks';
import { reloadAfterApplicationPersistence } from '../utils/persistenceLifecycle';

const VIEW_META = {
  launchpad: {
    labelKey: 'header.label_launchpad',
    Icon: Globe,
    accent: '#f3a5b6',
    kickerKey: 'header.kicker_studio',
  },
  studio: {
    labelKey: 'nav.voice',
    Icon: Fingerprint,
    accent: '#d3869b',
    kickerKey: 'header.kicker_studio',
  },
  // Legacy ids — kept so a not-yet-shimmed persisted 'clone'/'design' mode
  // still renders a sensible header (voice-studio-unification P4).
  clone: {
    labelKey: 'header.label_clone',
    Icon: Fingerprint,
    accent: '#d3869b',
    kickerKey: 'header.kicker_studio',
  },
  design: {
    labelKey: 'header.label_design',
    Icon: Wand2,
    accent: '#8ec07c',
    kickerKey: 'header.kicker_studio',
  },
  dub: {
    labelKey: 'header.label_dub',
    Icon: Film,
    accent: '#fe8019',
    kickerKey: 'header.kicker_studio',
  },
  projects: {
    labelKey: 'header.label_projects',
    Icon: FolderOpen,
    accent: '#83a598',
    kickerKey: 'header.kicker_library',
  },
  gallery: {
    labelKey: 'header.label_gallery',
    Icon: Library,
    accent: '#b8bb26',
    kickerKey: 'header.kicker_library',
  },
  transcriptions: {
    labelKey: 'header.label_transcriptions',
    Icon: FileText,
    accent: '#d3869b',
    kickerKey: 'header.kicker_library',
  },
  catalogue: {
    labelKey: 'header.label_catalogue',
    Icon: Boxes,
    accent: '#689d6a',
    kickerKey: 'header.kicker_preferences',
  },
  settings: {
    labelKey: 'header.label_settings',
    Icon: Settings2,
    accent: '#fabd2f',
    kickerKey: 'header.kicker_preferences',
  },
  enterprise: {
    labelKey: 'header.label_enterprise',
    Icon: Building2,
    accent: '#fe8019',
    kickerKey: 'header.kicker_licensing',
  },
};

function WaveBars({ color = '#f3a5b6', active }) {
  const heights = [4, 9, 5, 11, 6, 10, 5, 8];
  return (
    <div
      data-testid="header-wave-bars"
      className={`inline-flex items-center gap-[2px] h-[16px] px-[6px] transition-opacity duration-200 max-[1351px]:hidden ${active ? 'opacity-100' : 'opacity-[0.35]'}`}
      aria-hidden="true"
    >
      {heights.map((h, i) => (
        <span
          key={i}
          className={`inline-block w-[2.5px] rounded-[2px] transition-opacity duration-200 ${active ? '[animation:hqBounce_1.3s_ease-in-out_infinite]' : ''}`}
          style={{
            // Height + color are per-instance; animation-delay is per-bar.
            // These three are genuinely dynamic so stay inline.
            height: h,
            background: color,
            animationDelay: `${i * 0.08}s`,
          }}
        />
      ))}
    </div>
  );
}

async function runWindowAction(action) {
  try {
    const { getCurrentWindow } = await import('@tauri-apps/api/window');
    const appWindow = getCurrentWindow();
    if (action === 'minimize') await appWindow.minimize();
    else if (action === 'maximize') await appWindow.toggleMaximize();
    else if (action === 'close') await appWindow.close();
  } catch {
    console.warn('Window control action failed');
  }
}

export default function Header({
  mode,
  setMode,
  navStyle = 'rail',
  modelStatus,
  doubleClickMaximize,
  activeProjectName,
  onFlushMemory,
}) {
  // Titlebar-tabs mode puts the workspace switcher in this row, where the
  // breadcrumb + wordmark normally sit — the tabs already say where you are,
  // and two answers to that question in one bar is one too many.
  const tabsInTitlebar = navStyle === 'tabs';
  // The macOS platform config enables decorated overlay chrome. Its native
  // traffic lights provide the same window actions as our custom desktop row.
  const isDesktop = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
  const hasMacTrafficLights =
    isDesktop && typeof navigator !== 'undefined' && (navigator.platform || '').startsWith('Mac');
  const showWindowControls = isDesktop && !hasMacTrafficLights;
  const { t } = useTranslation();
  // Sysinfo is subscribed here (not in App via useAppData) so the 5s poll
  // only re-renders the header chrome, not the whole App tree.
  const sysQuery = useSysinfo();
  const sysStats = sysQuery.data ?? null;
  // Default OFF — chrome shouldn't double as a resource monitor. Power users
  // flip this on via Settings → Performance. Idle/Ready/Loading badge +
  // Flush button stay visible regardless (action-relevant).
  const showLiveStats = useAppStore((s) => s.showHeaderLiveStats);
  const [flushing, setFlushing] = useState(false);
  const [flushOpen, setFlushOpen] = useState(false);
  const [engineFamily, setEngineFamily] = useState('tts');
  const { data: engines } = useEngines();
  const [engineLabelIndex, setEngineLabelIndex] = useState(0);
  const [engineLabelPaused, setEngineLabelPaused] = useState(false);
  const activeEngines = ['tts', 'asr', 'llm'].flatMap((family) => {
    const inventory = engines?.[family];
    const active = inventory?.backends?.find((engine) => engine.id === inventory.active);
    return active ? [{ family, name: engineDisplayName(active.display_name) }] : [];
  });
  const displayedEngine = activeEngines[engineLabelIndex % (activeEngines.length || 1)];
  const activeEngineName = displayedEngine?.name;
  const activeEngineShortName = activeEngineName?.split(' (')[0];
  useEffect(() => {
    if (flushOpen || engineLabelPaused || activeEngines.length < 2) return;
    const timer = setInterval(() => setEngineLabelIndex((index) => index + 1), 4000);
    return () => clearInterval(timer);
  }, [flushOpen, engineLabelPaused, activeEngines.length]);
  const [loadedModels, setLoadedModels] = useState([]);
  const [unloading, setUnloading] = useState(null);
  const flushRef = useRef(null);
  const flushBtnRef = useRef(null);
  const [dropdownPos, setDropdownPos] = useState({ top: 0, left: 0 });

  // Dynamically compute dropdown position from button rect
  const computePos = useCallback(() => {
    if (!flushBtnRef.current) return;
    const rect = flushBtnRef.current.getBoundingClientRect();
    const dropW = Math.min(420, window.innerWidth - 16);
    const dropH = Math.min(640, window.innerHeight - 80);
    const pad = 6;

    // Default: below button, right-aligned
    let top = rect.bottom + pad;
    let left = rect.right - dropW;

    // Flip up if too close to bottom
    if (top + dropH > window.innerHeight - 10) {
      top = Math.max(8, rect.top - dropH - pad);
    }
    // Clamp left so it doesn't go off-screen
    if (left < 8) left = 8;
    if (left + dropW > window.innerWidth - 8) left = window.innerWidth - dropW - 8;

    setDropdownPos({ top, left, width: dropW });
  }, []);

  // Recompute on open, resize, and scroll
  useEffect(() => {
    if (!flushOpen) return;
    computePos();
    window.addEventListener('resize', computePos);
    window.addEventListener('scroll', computePos, true);
    return () => {
      window.removeEventListener('resize', computePos);
      window.removeEventListener('scroll', computePos, true);
    };
  }, [flushOpen, computePos]);
  const view = VIEW_META[mode] || VIEW_META.launchpad;
  const ViewIcon = view.Icon;

  // Fetch loaded models when dropdown opens
  useEffect(() => {
    if (!flushOpen) return;
    const fetchModels = async () => {
      try {
        const { apiFetch } = await import('../api/client');
        const res = await apiFetch('/model/loaded');
        const data = await res.json();
        setLoadedModels(data.models || []);
      } catch {}
    };
    fetchModels();
  }, [flushOpen]);

  // Click outside to close (must check both the button wrapper AND the portal dropdown)
  const dropdownRef = useRef(null);
  useEffect(() => {
    if (!flushOpen) return;
    const frame = requestAnimationFrame(() =>
      dropdownRef.current?.querySelector('button')?.focus(),
    );
    return () => cancelAnimationFrame(frame);
  }, [flushOpen]);
  useEffect(() => {
    const show = () => setFlushOpen(true);
    window.addEventListener('engine-quick-switch', show);
    return () => window.removeEventListener('engine-quick-switch', show);
  }, []);
  useEffect(() => {
    if (!flushOpen) return;
    const handler = (e) => {
      const inBtn = flushRef.current && flushRef.current.contains(e.target);
      const inDrop = dropdownRef.current && dropdownRef.current.contains(e.target);
      if (!inBtn && !inDrop) setFlushOpen(false);
    };
    document.addEventListener('mousedown', handler);
    const escape = (event) => {
      if (event.key === 'Escape') {
        setFlushOpen(false);
        flushBtnRef.current?.focus();
      }
    };
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', handler);
      document.removeEventListener('keydown', escape);
    };
  }, [flushOpen]);

  const unloadModel = async (modelId) => {
    setUnloading(modelId);
    try {
      const { apiFetch } = await import('../api/client');
      await apiFetch(`/model/unload/${modelId}`, { method: 'POST' });
      setLoadedModels((prev) => prev.filter((m) => m.id !== modelId));
    } catch {
    } finally {
      setUnloading(null);
    }
  };
  // Dynamic accent color must stay inline — it's driven by the current view.
  const dotStyle = { background: view.accent, boxShadow: `0 0 10px ${view.accent}90` };
  const labelStyle = { color: view.accent };
  return (
    <div
      className={`header-area ${tabsInTitlebar ? 'header-area--tabs' : ''}`}
      data-tauri-drag-region
      onDoubleClick={doubleClickMaximize}
    >
      {tabsInTitlebar ? (
        <div className="header-area__tabs min-w-0">
          <TitleTabs mode={mode} setMode={setMode} />
        </div>
      ) : (
        /* Left: view title + breadcrumb */
        <div
          className={`flex items-center gap-[14px] justify-self-start min-w-0 ${
            hasMacTrafficLights ? 'header-area__left--mac-inset' : ''
          }`}
        >
          <div className="inline-flex items-center gap-[6px] h-[var(--chrome-pill-h)] [font-family:var(--font-sans)] max-[961px]:gap-[5px]">
            <span
              className="w-[7px] h-[7px] rounded-full shrink-0 [animation:hqPulse_2.4s_ease-in-out_infinite] motion-reduce:[animation:none] max-[821px]:hidden"
              style={dotStyle}
            />
            <span className="text-[length:var(--chrome-label-size)] font-semibold tracking-[var(--chrome-label-track)] uppercase text-[var(--chrome-fg-muted)] max-[1501px]:hidden">
              {t(view.kickerKey)}
            </span>
            <ChevronRight size={10} color="#504945" className="mx-[2px] max-[1501px]:hidden" />
            <span
              className="inline-flex items-center gap-1 [font-family:var(--font-sans)] text-[0.72rem] font-semibold tracking-[0.02em] max-[961px]:text-[0.78rem]"
              style={labelStyle}
            >
              <ViewIcon size={12} className="mr-1 align-[-1px]" />
              {t(view.labelKey)}
            </span>
            {activeProjectName ? (
              <>
                <ChevronRight size={10} color="#504945" className="mx-[2px]" />
                <span
                  className="[font-family:var(--font-sans)] text-[0.68rem] font-medium text-[var(--chrome-fg-muted)] max-w-[180px] overflow-hidden text-ellipsis whitespace-nowrap max-[1201px]:hidden"
                  title={activeProjectName}
                >
                  {activeProjectName}
                </span>
              </>
            ) : null}
          </div>
          {import.meta.env.DEV && (
            <Button
              variant="ghost"
              size="sm"
              title={t('common.reload')}
              onClick={() => void reloadAfterApplicationPersistence()}
              leading={<RefreshCw size={9} />}
              className="shrink-0"
            >
              {t('common.reload')}
            </Button>
          )}
        </div>
      )}

      {/* Center: logo — the tabs take this room in titlebar-tabs mode. */}
      {!tabsInTitlebar && (
        <div
          className="flex items-center gap-2 justify-self-center pointer-events-none whitespace-nowrap"
          translate="no"
        >
          <VoiceStudioMark
            data-testid="voice-studio-logo"
            className="size-7 overflow-visible text-[var(--chrome-accent)]"
          />
          <span className="text-[0.92rem] font-semibold text-[var(--chrome-fg)] tracking-[0.02em] [font-family:var(--font-sans)] not-italic">
            Voice<span className="text-[var(--chrome-accent)]">Studio</span>
          </span>
        </div>
      )}

      {/* Right: wave + sys stats. UI scale (S/M/L) lives in the bottom
          LogsFooter bar so all app-wide chrome sits together. */}
      <div className="flex items-center justify-end gap-3 justify-self-end min-w-0 overflow-visible">
        <NotificationPanel onNavigate={setMode} />
        <WaveBars color={view.accent} active={modelStatus === 'loading'} />
        {sysStats && (
          <div className="flex items-center gap-2 [font-family:var(--chrome-font-mono)] text-[10.5px] text-[var(--chrome-fg-dim)] bg-transparent h-[var(--chrome-pill-h)] whitespace-nowrap shrink-0 tabular-nums slashed-zero">
            {showLiveStats && (
              <>
                <span className="max-[1081px]:hidden">
                  <b className="text-[var(--chrome-fg-muted)] font-semibold">RAM</b>{' '}
                  {sysStats.ram.toFixed(1)}/{sysStats.total_ram.toFixed(0)}G
                </span>
                <span className="max-[1081px]:hidden">
                  <b className="text-[var(--chrome-fg-muted)] font-semibold">CPU</b>{' '}
                  {sysStats.cpu.toFixed(0)}%
                </span>
                <span
                  className="[border-left:1px_solid_var(--chrome-border)] pl-[6px]"
                  aria-label={`VRAM usage: ${sysStats.vram.toFixed(1)} gigabytes`}
                >
                  <b
                    className={`font-semibold ${sysStats.gpu_active ? 'text-[var(--chrome-severity-ok)]' : 'text-[var(--chrome-fg-muted)]'}`}
                  >
                    VRAM
                  </b>{' '}
                  {sysStats.vram.toFixed(1)}G
                </span>
              </>
            )}
            {/* Where the next job runs. Renders nothing until at least one
                remote worker is enrolled, so a user who never opts in sees
                no change to the header at all. */}
            <GpuTarget />
            <span className="[border-left:1px_solid_var(--chrome-border)] pl-[6px] flex items-center gap-1">
              <Badge
                tone={
                  modelStatus === 'ready'
                    ? 'success'
                    : modelStatus === 'loading'
                      ? 'warn'
                      : 'neutral'
                }
                size="xs"
                dot
                className={`[border:none]! bg-transparent! p-0! normal-case! tracking-normal! font-semibold! ${modelStatus === 'loading' ? 'ui-badge--pulse' : ''}`}
              >
                {modelStatus === 'ready'
                  ? t('header.status_ready')
                  : modelStatus === 'loading'
                    ? t('header.status_loading')
                    : t('header.status_idle')}
              </Badge>
            </span>
            {onFlushMemory && (
              <div ref={flushRef} style={{ position: 'relative' }}>
                <Button
                  ref={flushBtnRef}
                  variant="subtle"
                  size="sm"
                  title={activeEngineName || t('header.memory_management')}
                  aria-label={t('settings.engines')}
                  aria-haspopup="dialog"
                  aria-expanded={flushOpen}
                  loading={flushing}
                  leading={!flushing && <Zap size={8} />}
                  trailing={<ChevronDown size={8} />}
                  onMouseEnter={() => setEngineLabelPaused(true)}
                  onMouseLeave={() => setEngineLabelPaused(false)}
                  onFocus={() => setEngineLabelPaused(true)}
                  onBlur={() => setEngineLabelPaused(false)}
                  onClick={() => {
                    if (!flushOpen && displayedEngine) setEngineFamily(displayedEngine.family);
                    setFlushOpen((o) => !o);
                  }}
                  className="ml-[2px]"
                >
                  <span
                    key={displayedEngine?.family}
                    className="engine-title-label flex w-[112px] items-center justify-between gap-1 max-[600px]:w-[77px]"
                  >
                    {displayedEngine && (
                      <span className="shrink-0 text-left text-[var(--chrome-fg-dim)]">
                        {t(`models.role_${displayedEngine.family}`)} ·{' '}
                      </span>
                    )}
                    <span className="min-w-0 flex-1 truncate text-right">
                      {activeEngineShortName || t('settings.engines')}
                    </span>
                  </span>
                </Button>
                {flushOpen &&
                  createPortal(
                    <div
                      role="dialog"
                      aria-label={t('settings.engines')}
                      className="fixed overflow-y-auto bg-[var(--color-bg)] border border-transparent rounded-[var(--radius-lg)] shadow-xl z-[9999] p-3"
                      style={{
                        top: dropdownPos.top,
                        left: dropdownPos.left,
                        width: dropdownPos.width,
                        maxHeight: `calc(100vh - ${dropdownPos.top + 8}px)`,
                      }}
                      ref={dropdownRef}
                    >
                      <div className="flex items-center justify-between gap-2 pb-2">
                        <span className="text-sm font-semibold">{t('settings.engines')}</span>
                        <button
                          type="button"
                          aria-label={t('common.close')}
                          onClick={() => {
                            setFlushOpen(false);
                            flushBtnRef.current?.focus();
                          }}
                          className="inline-flex h-8 w-8 items-center justify-center rounded-md border-0 bg-transparent text-[var(--chrome-fg-muted)] cursor-pointer hover:bg-[var(--chrome-hover-bg)] focus-visible:outline-2 focus-visible:outline-[var(--chrome-accent)]"
                        >
                          <X size={16} />
                        </button>
                      </div>
                      <Tabs value={engineFamily} onValueChange={setEngineFamily}>
                        <TabsList
                          aria-label={t('settings.engines')}
                          className="w-full h-auto grid grid-cols-3 bg-[var(--chrome-hover-bg)]"
                        >
                          {['tts', 'asr', 'llm'].map((family) => (
                            <TabsTrigger
                              key={family}
                              value={family}
                              className="min-h-11 cursor-pointer whitespace-normal bg-transparent text-xs text-[var(--chrome-fg-muted)] data-[state=active]:bg-[var(--chrome-accent-bg)] data-[state=active]:text-[var(--chrome-accent)] dark:data-[state=active]:bg-[var(--chrome-accent-bg)] dark:data-[state=active]:text-[var(--chrome-accent)] hover:data-[state=inactive]:bg-[var(--chrome-hover-bg)]"
                            >
                              {family === 'tts'
                                ? t('header.speech')
                                : family === 'asr'
                                  ? t('projects.transcription')
                                  : t('models.role_llm')}
                            </TabsTrigger>
                          ))}
                        </TabsList>
                        <TabsContent value={engineFamily}>
                          <EngineQuickSwitch key={engineFamily} family={engineFamily} embedded />
                        </TabsContent>
                      </Tabs>
                      <button
                        type="button"
                        className="flex items-center gap-1 border-0 bg-transparent p-2 text-xs text-[var(--chrome-fg-muted)] cursor-pointer hover:text-[var(--chrome-fg)]"
                        onClick={() => {
                          setFlushOpen(false);
                          useAppStore.getState().openCatalogue({ family: engineFamily });
                        }}
                      >
                        {t('header.label_catalogue')} <ChevronRight size={12} />
                      </button>
                      <div className="h-px bg-[var(--color-border)] my-2" />
                      <div className="text-[10px] font-semibold text-[var(--color-fg-subtle)] uppercase tracking-[0.5px] pt-[6px] px-[12px] pb-[4px]">
                        {t('header.loaded_models')}
                      </div>
                      {loadedModels.length === 0 ? (
                        <div className="p-[12px] text-[11px] text-[var(--color-fg-muted)] text-center">
                          {t('header.no_models')}
                        </div>
                      ) : (
                        loadedModels.map((m) => (
                          <div
                            key={m.id}
                            className="flex items-center justify-between py-[6px] px-[12px] gap-2 hover:bg-[rgba(255,255,255,0.03)]"
                          >
                            <div className="flex flex-col gap-[1px] min-w-0">
                              <span className="text-[12px] text-[var(--color-fg)] font-medium">
                                {engineDisplayName(m.name)}
                                {/* Resident-but-not-routed engine (e.g. VoiceStudio still in
                                    VRAM after switching to another backend) — say so. */}
                                {m.is_active_engine === false && (
                                  <span className="ml-[6px] text-[10px] font-normal text-[var(--color-fg-subtle)] [font-family:var(--font-mono)]">
                                    {t('header.model_not_active')}
                                  </span>
                                )}
                              </span>
                              <span className="text-[10px] text-[var(--color-fg-subtle)] [font-family:var(--font-mono)]">
                                {m.device} {m.vram_mb > 0 ? `· ${m.vram_mb.toFixed(0)} MB` : ''}
                              </span>
                            </div>
                            {m.unloadable && (
                              <button
                                className="text-[10px] font-semibold text-[var(--color-brand)] bg-[rgba(211,134,155,0.1)] [border:1px_solid_rgba(211,134,155,0.2)] rounded-[var(--radius-pill)] py-[2px] px-[8px] cursor-pointer shrink-0 hover:bg-[rgba(211,134,155,0.2)]"
                                onClick={() => unloadModel(m.id)}
                                disabled={unloading === m.id}
                                aria-label={`Unload ${m.name}`}
                              >
                                {unloading === m.id ? '…' : t('header.unload')}
                              </button>
                            )}
                          </div>
                        ))
                      )}
                      <div className="h-[1px] bg-[var(--color-border)] my-[4px]" />
                      <button
                        className="flex items-center gap-[6px] w-full py-[6px] px-[12px] text-[12px] text-[var(--color-fg)] bg-transparent border-none cursor-pointer text-left hover:bg-[rgba(255,255,255,0.04)]"
                        onClick={async () => {
                          setFlushing(true);
                          setFlushOpen(false);
                          try {
                            await onFlushMemory(false);
                          } finally {
                            setFlushing(false);
                          }
                        }}
                      >
                        <Zap size={10} /> {t('header.flush_caches')}
                      </button>
                      <details>
                        <summary className="cursor-pointer px-3 py-2 text-xs text-[var(--chrome-fg-muted)]">
                          {t('header.memory_management')}
                        </summary>
                        <button
                          className="flex items-center gap-[6px] w-full py-[6px] px-[12px] text-[12px] text-[#fb4934] bg-transparent border-none cursor-pointer text-left hover:bg-[rgba(251,73,52,0.08)]"
                          onClick={async () => {
                            setFlushing(true);
                            setFlushOpen(false);
                            try {
                              await onFlushMemory(true);
                            } finally {
                              setFlushing(false);
                            }
                          }}
                        >
                          <Trash2 size={10} /> {t('header.unload_all_flush')}
                        </button>
                      </details>
                    </div>,
                    document.body,
                  )}
              </div>
            )}
          </div>
        )}
        {showWindowControls && (
          <div className="ml-1 flex h-full shrink-0 items-stretch" data-testid="window-controls">
            <button
              type="button"
              className="flex h-7 w-9 items-center justify-center border-0 bg-transparent text-[var(--chrome-fg-muted)] hover:bg-[var(--chrome-hover-bg)] hover:text-[var(--chrome-fg)]"
              aria-label={t('common.minimize_window')}
              title={t('common.minimize_window')}
              onClick={() => void runWindowAction('minimize')}
            >
              <Minus size={13} />
            </button>
            <button
              type="button"
              className="flex h-7 w-9 items-center justify-center border-0 bg-transparent text-[var(--chrome-fg-muted)] hover:bg-[var(--chrome-hover-bg)] hover:text-[var(--chrome-fg)]"
              aria-label={t('common.maximize_restore_window')}
              title={t('common.maximize_restore_window')}
              onClick={() => void runWindowAction('maximize')}
            >
              <Square size={10} />
            </button>
            <button
              type="button"
              className="flex h-7 w-9 items-center justify-center border-0 bg-transparent text-[var(--chrome-fg-muted)] hover:bg-[#c42b1c] hover:text-white"
              aria-label={t('common.close_window')}
              title={t('common.close_window')}
              onClick={() => void runWindowAction('close')}
            >
              <X size={13} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
