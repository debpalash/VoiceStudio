import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { copyText } from '../utils/copyText';
import { normalizeChannel } from '../utils/updateChannel';
import { ArrowDownToLine } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { API, apiFetch } from '../api/client';
import { useTranslation } from 'react-i18next';
import { systemLogs, systemLogsTauri, clearSystemLogs, clearTauriLogs } from '../api/system';
import { useSysinfo, useModelStatus, useSystemInfo } from '../api/hooks';
import { getFrontendLogs, clearFrontendLogs } from '../utils/consoleBuffer';
import { resolveAboutVersion } from '../utils/appVersion';
import { flushApplicationPersistence } from '../utils/persistenceLifecycle';
import { SettingsSection } from '../components/settings/primitives';
import { useAppStore } from '../store';
// Panels — re-hosted as-is; the redesign reorganizes them, not their logic.
import PerformanceDeviceTab from '../components/settings/PerformanceDeviceTab';
import RefinementPanel from '../components/settings/RefinementPanel';
import AecPanel from '../components/settings/AecPanel';
import VoicePanel from '../components/settings/VoicePanel';
import AppearancePanel from '../components/settings/AppearancePanel';
import StoragePanel from '../components/settings/StoragePanel';
import StorageTab from '../components/settings/StorageTab';
import UsageTab from '../components/settings/UsageTab';
import StorageUsagePanel from '../components/settings/StorageUsagePanel';
import HFMirrorPanel from '../components/settings/HFMirrorPanel';
import SharingPanel from '../components/settings/SharingPanel';
import RemoteBackendPanel from '../components/settings/RemoteBackendPanel';
import WorkersPanel from '../components/settings/WorkersPanel';
import MCPBindingsPanel from '../components/settings/MCPBindingsPanel';
import OpenApiPanel from '../components/settings/OpenApiPanel';
import PronunciationPanel from '../components/settings/PronunciationPanel';
import PermissionsPanel from '../components/settings/PermissionsPanel';
import DictationDemo from '../components/DictationDemo';
import UpdatesPanel from '../components/UpdatesPanel';
import GeneralTab from '../components/settings/GeneralTab';
// Engine selection + the model store moved to the Model Catalogue workspace
// (pages/ModelCatalogue.jsx); these categories now signpost it.
import VoicePreviewsPanel from '../components/settings/VoicePreviewsPanel';
import HotkeyTab from '../components/settings/HotkeyTab';
import TranslationTab from '../components/settings/TranslationTab';
import NetworkTab from '../components/settings/NetworkTab';
import AudioToolsPanel from '../components/settings/AudioToolsPanel';
import ApiKeysPanel from '../components/settings/ApiKeysPanel';
import LLMProvidersPanel from '../components/settings/LLMProvidersPanel';
import LLMSkillsPanel from '../components/settings/LLMSkillsPanel';
import AboutTab from '../components/settings/AboutTab';
import PrivacyTab from '../components/settings/PrivacyTab';
import LogsTab from '../components/settings/LogsTab';
import SettingsSidebar from '../components/settings/SettingsSidebar';
import SettingsSearch from '../components/settings/SettingsSearch';
import RestartBadge from '../components/settings/RestartBadge';
import {
  CATEGORY_BY_ID,
  matchCategories,
  resolveCategoryId,
} from '../components/settings/settingsCategories';
import { isTauri, askConfirm } from '../components/settings/native';

// Persist the last-opened category so re-opening Settings lands where you left.
const LS_CATEGORY = 'omnivoice.settings.category';

/**
 * Settings — a sidebar-nav + content-pane hub (macOS System Settings / VS Code
 * style). This is a thin orchestrator: it owns the active-category state, the
 * search filter, and the shared data/handlers that LogsTab / AboutTab / Updates
 * need, then delegates each category's UI to its panel(s) via renderCategory().
 *
 * The IA (groups → categories) lives in settingsCategories.jsx; the sidebar and
 * search box are extracted components. See that registry for the full mapping.
 */
export default function Settings() {
  const { t } = useTranslation();

  // One-shot deep-link (e.g. footer version badge → Updates). Legacy tab ids are
  // mapped to the new category ids by resolveCategoryId().
  const pendingSettingsTab = useAppStore((s) => s.pendingSettingsTab);
  const setPendingSettingsTab = useAppStore((s) => s.setPendingSettingsTab);

  const [active, setActiveRaw] = useState(() => {
    if (pendingSettingsTab) return resolveCategoryId(pendingSettingsTab);
    try {
      return resolveCategoryId(localStorage.getItem(LS_CATEGORY));
    } catch {
      return resolveCategoryId(null);
    }
  });
  const [query, setQuery] = useState('');

  const setActive = useCallback((id) => {
    const next = resolveCategoryId(id);
    setActiveRaw(next);
    try {
      localStorage.setItem(LS_CATEGORY, next);
    } catch {
      /* private mode / quota — non-fatal */
    }
  }, []);

  // Consume a one-shot deep-link tab even when Settings is already mounted.
  useEffect(() => {
    if (pendingSettingsTab) {
      setActive(pendingSettingsTab);
      setPendingSettingsTab(null);
    }
  }, [pendingSettingsTab, setPendingSettingsTab, setActive]);

  // Search → filtered category ids. Label-aware so it matches translated names;
  // `t` also lets each category's keywordKeys match in the active UI language.
  const visibleIds = useMemo(
    () => matchCategories(query, (c) => t(c.labelKey, { defaultValue: c.defaultLabel }), t),
    [query, t],
  );
  const visibleSet = useMemo(() => new Set(visibleIds), [visibleIds]);

  // Bonus: typing a query that matches a *setting* (or another category) jumps
  // selection to the first match when the current category falls out of view.
  useEffect(() => {
    if (!query.trim()) return;
    if (visibleIds.length > 0 && !visibleSet.has(active)) {
      setActiveRaw(visibleIds[0]);
    }
  }, [query, visibleIds, visibleSet, active]);

  // ── Sidebar keyboard loop ──────────────────────────────────────────────────
  // ⌘K / Ctrl+K focuses the filter from anywhere on the page; the search box
  // hands focus down into the category list (Enter / ↓) and the list hands it
  // back (↑ past the top). Same binding on every platform — only the keycap
  // hint differs.
  const searchRef = useRef(null);
  const focusSearch = useCallback(() => {
    const el = searchRef.current;
    if (!el) return;
    el.focus();
    el.select?.();
  }, []);
  const focusActiveCategory = useCallback(() => {
    // The rail is a roving-tabindex list: the active item is its only tab stop,
    // so focusing it is "enter the list" regardless of which item that is.
    document.querySelector('[data-testid^="settings-nav-"][aria-current="page"]')?.focus?.();
  }, []);

  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key !== 'k' && e.key !== 'K') return;
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
      e.preventDefault();
      focusSearch();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [focusSearch]);

  // ── Shared data (TanStack Query — shared cache with App.jsx) ───────────────
  const { data: hw } = useSysinfo();
  const { data: status } = useModelStatus();
  const { data: info } = useSystemInfo();
  const updateChannel = useAppStore((s) => s.updateChannel);

  const [appVersion, setAppVersion] = useState(null);
  const [tauriVersion, setTauriVersion] = useState(null);
  const [updateState, setUpdateState] = useState('idle');

  useEffect(() => {
    if (!isTauri()) return;
    (async () => {
      try {
        const app = await import('@tauri-apps/api/app');
        setAppVersion(await app.getVersion());
        if (app.getTauriVersion) setTauriVersion(await app.getTauriVersion());
      } catch {
        /* web preview */
      }
    })();
  }, []);

  // ── Diagnostics (About) ────────────────────────────────────────────────────
  const [selfCheck, setSelfCheck] = useState(null);
  const [selfCheckRunning, setSelfCheckRunning] = useState(false);
  const runSelfCheck = useCallback(async () => {
    setSelfCheckRunning(true);
    try {
      const r = await apiFetch(`${API}/system/diagnose`);
      setSelfCheck(await r.json());
    } catch (e) {
      toast.error(t('about.self_check_failed', { message: e?.message || e }));
    } finally {
      setSelfCheckRunning(false);
    }
  }, [t]);

  const [bundleBuilding, setBundleBuilding] = useState(false);
  const saveDiagnosticBundle = useCallback(async () => {
    setBundleBuilding(true);
    try {
      const r = await apiFetch(`${API}/system/diagnostic-bundle`, { method: 'POST' });
      const j = await r.json();
      toast.success(t('about.bundle_saved', { filename: j.filename }));
      try {
        const { exportReveal } = await import('../api/exports');
        await exportReveal({ path: j.path });
      } catch {
        /* reveal is best-effort — the toast already names the file */
      }
    } catch (e) {
      toast.error(t('about.bundle_failed', { message: e?.message || e }));
    } finally {
      setBundleBuilding(false);
    }
  }, [t]);

  const copyDiagnostics = useCallback(async () => {
    const nav = typeof navigator !== 'undefined' ? navigator : {};
    const ua = nav.userAgent || '—';
    const lang = nav.language || '—';
    const tz = (() => {
      try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone;
      } catch {
        return '—';
      }
    })();
    const fmtGB = (v) => (typeof v === 'number' ? `${v.toFixed(2)} GB` : '—');
    const lines = [
      '### VoiceStudio diagnostics',
      '',
      `- **App version:** ${resolveAboutVersion(appVersion, info)}`,
      `- **Tauri runtime:** ${tauriVersion || (isTauri() ? '—' : 'web preview')}`,
      `- **Platform:** ${info?.platform || '—'}`,
      `- **Architecture:** ${nav.userAgentData?.platform || nav.platform || '—'}`,
      `- **Locale / timezone:** ${lang} / ${tz}`,
      `- **Python:** ${info?.python || '—'}`,
      `- **Compute device:** ${info?.device || '—'}`,
      `- **GPU active:** ${hw?.gpu_active ? 'yes' : 'no'}`,
      `- **RAM:** ${fmtGB(hw?.ram)} used / ${fmtGB(hw?.total_ram)} total`,
      `- **VRAM (allocated):** ${fmtGB(hw?.vram)}`,
      `- **Backend status:** ${status?.status || 'unknown'}`,
      `- **Active model:** ${status?.repo_id || info?.model_checkpoint || '—'}`,
      `- **ASR model:** ${info?.asr_model || '—'}`,
      `- **Translator:** ${info?.translate_provider || '—'}`,
      `- **HF token set:** ${info?.has_hf_token ? 'yes' : 'no'}`,
      `- **Data directory:** ${info?.data_dir || '—'}`,
      `- **Outputs directory:** ${info?.outputs_dir || '—'}`,
      `- **Crash log:** ${info?.crash_log_path || '—'}`,
      `- **Update channel:** ${updateChannel}`,
      `- **Update endpoint:** ${
        updateChannel === 'preview'
          ? 'https://github.com/debpalash/VoiceStudio/releases/download/preview/latest.json'
          : 'https://github.com/debpalash/VoiceStudio/releases/latest/download/latest.json'
      }`,
      `- **User agent:** ${ua}`,
    ];
    try {
      await copyText(lines.join('\n'));
      toast.success(t('settings.diagnostics_copied'));
    } catch (e) {
      toast.error(t('settings.copy_failed', { message: e?.message || e }));
    }
  }, [appVersion, tauriVersion, info, status, hw, updateChannel, t]);

  const checkForUpdates = useCallback(async () => {
    if (!isTauri()) {
      toast(t('settings.updater_desktop'), { icon: 'ℹ️' });
      return;
    }
    setUpdateState('checking');
    try {
      const [{ invoke }, { relaunch }, { ask }] = await Promise.all([
        import('@tauri-apps/api/core'),
        import('@tauri-apps/plugin-process'),
        import('@tauri-apps/plugin-dialog'),
      ]);
      const channel = normalizeChannel(updateChannel);
      const update = await invoke('check_update', { channel });
      if (!update) {
        setUpdateState('uptodate');
        toast.success(t('settings.latest_version'));
        return;
      }
      const proceed = await ask(
        t('settings.updater_available_body', {
          version: update.version,
          notes: update.notes || t('settings.updater_notes_fallback'),
        }),
        { title: t('settings.updater_available_title'), kind: 'info' },
      );
      if (!proceed) {
        setUpdateState('idle');
        return;
      }
      setUpdateState('downloading');
      const tid = toast.loading(t('settings.updater_downloading', { version: update.version }));
      await invoke('install_update', { channel });
      toast.success(t('settings.updater_installed'), { id: tid });
      try {
        await flushApplicationPersistence();
      } catch (error) {
        console.warn('[persistence] installed-update flush failed', error);
      }
      try {
        await relaunch();
      } catch (error) {
        setUpdateState('error');
        toast.error(t('update.failed'));
        console.warn('Installed update could not relaunch:', error);
      }
    } catch (e) {
      setUpdateState('error');
      toast.error(t('settings.update_check_failed', { message: e?.message || e }));
    }
  }, [updateChannel, t]);

  // ── Logs ────────────────────────────────────────────────────────────────────
  const [logSource, setLogSource] = useState('backend');
  const [logs, setLogs] = useState([]);
  const [logMeta, setLogMeta] = useState({ path: '', exists: false });
  const [loadingLogs, setLoadingLogs] = useState(false);

  const refreshLogs = useCallback(async () => {
    setLoadingLogs(true);
    try {
      if (logSource === 'backend') {
        const r = await systemLogs(400);
        setLogs(r.lines || []);
        setLogMeta({ path: r.path || '', exists: !!r.exists });
      } else if (logSource === 'tauri') {
        const r = await systemLogsTauri(400);
        setLogs(r.lines || []);
        setLogMeta({ path: r.path || '—', exists: !!r.exists, candidates: r.candidates });
      } else {
        const entries = getFrontendLogs();
        const lines = entries.map((e) => {
          const ts = new Date(e.t).toISOString().slice(11, 23);
          return `[${ts}] [${e.level}] ${e.msg}\n`;
        });
        setLogs(lines);
        setLogMeta({
          path: t('logs.frontend_buffer', { defaultValue: 'in-memory (last 500)' }),
          exists: true,
        });
      }
    } catch (e) {
      toast.error(t('settings.logs_load_failed', { message: e.message }));
    } finally {
      setLoadingLogs(false);
    }
  }, [logSource, t]);

  useEffect(() => {
    if (active === 'logs') refreshLogs();
  }, [active, logSource, refreshLogs]);

  const onClearLogs = async () => {
    if (logSource === 'frontend') {
      if (
        !(await askConfirm(
          t('settings.clear_frontend_confirm'),
          t('settings.clear_frontend_title'),
        ))
      )
        return;
      clearFrontendLogs();
      toast.success(t('settings.frontend_logs_cleared'));
      setLogs([]);
      return;
    }
    if (logSource === 'tauri') {
      if (!(await askConfirm(t('settings.clear_tauri_confirm'), t('settings.clear_tauri_title'))))
        return;
      try {
        const r = await clearTauriLogs();
        if (!r?.cleared?.length) {
          toast(t('settings.nothing_to_clear'), { icon: 'ℹ️' });
        } else {
          toast.success(t('settings.cleared_tauri', { count: r.cleared.length }));
          setLogs([]);
        }
      } catch (e) {
        toast.error(t('settings.clear_tauri_failed', { message: e.message }));
      }
      return;
    }
    if (!(await askConfirm(t('settings.clear_backend_confirm'), t('settings.clear_backend_title'))))
      return;
    try {
      await clearSystemLogs();
      toast.success(t('settings.backend_logs_cleared'));
      setLogs([]);
    } catch (e) {
      toast.error(t('settings.clear_backend_failed'));
    }
  };

  const renderCategory = (id) => {
    switch (id) {
      case 'appearance':
        return (
          <>
            <AppearancePanel />
            <GeneralTab />
          </>
        );
      case 'dictation':
        return (
          <>
            <VoicePanel />
            <DictationDemo />
            <HotkeyTab />
            <RefinementPanel />
            <AecPanel />
          </>
        );
      case 'pronunciation':
        return <PronunciationPanel />;
      case 'translation':
        return <TranslationTab />;
      case 'performance':
        return <PerformanceDeviceTab />;
      case 'usage':
        return (
          <>
            <UsageTab />
          </>
        );
      case 'storage':
        // Everything disk-shaped in one place: usage, the models directory
        // (restart-bound), data/output paths, and the voice-preview cache
        // toggle. Browsing / installing / removing weights is the Model
        // Catalogue.
        return (
          <>
            <StorageUsagePanel />
            <StoragePanel />
            <StorageTab />
            <VoicePreviewsPanel />
          </>
        );
      case 'permissions':
        return <PermissionsPanel />;
      case 'network':
        // The HF mirror is a network choice (which host downloads come from).
        return (
          <>
            <NetworkTab />
            <HFMirrorPanel />
          </>
        );
      case 'audio-tools':
        return <AudioToolsPanel />;
      case 'sharing':
        return (
          <>
            <SharingPanel />
            <RemoteBackendPanel />
            <MCPBindingsPanel />
          </>
        );
      case 'workers':
        return <WorkersPanel />;
      case 'openapi':
        return <OpenApiPanel />;
      case 'credentials':
        return <ApiKeysPanel />;
      case 'llm-providers':
        return <LLMProvidersPanel />;
      case 'llm-skills':
        return <LLMSkillsPanel />;
      case 'updates':
        return (
          <SettingsSection icon={ArrowDownToLine} title={t('settings.updates')}>
            <UpdatesPanel />
          </SettingsSection>
        );
      case 'privacy':
        return <PrivacyTab info={info} />;
      case 'logs':
        return (
          <LogsTab
            logSource={logSource}
            setLogSource={setLogSource}
            logs={logs}
            logMeta={logMeta}
            loadingLogs={loadingLogs}
            refreshLogs={refreshLogs}
            onClearLogs={onClearLogs}
          />
        );
      case 'about':
        return (
          <AboutTab
            appVersion={appVersion}
            tauriVersion={tauriVersion}
            info={info}
            checkForUpdates={checkForUpdates}
            updateState={updateState}
            selfCheck={selfCheck}
            selfCheckRunning={selfCheckRunning}
            runSelfCheck={runSelfCheck}
            bundleBuilding={bundleBuilding}
            saveDiagnosticBundle={saveDiagnosticBundle}
            copyDiagnostics={copyDiagnostics}
          />
        );
      default:
        return null;
    }
  };

  const cat = CATEGORY_BY_ID[active] || CATEGORY_BY_ID.appearance;
  const CatIcon = cat.icon;

  return (
    // Query the scaled shell's actual content width. Viewport media queries fire
    // at the wrong logical width when WebKitGTK/Tauri zoom is active.
    <div className="h-full min-h-0 w-full [container-type:inline-size] [container-name:settings-shell]">
      <div className="flex h-full min-h-0 w-full box-border flex-1 flex-col overflow-y-auto bg-[var(--chrome-bg)] p-[var(--space-5)_var(--space-7)_var(--space-7)] font-sans @min-[760px]/settings-shell:grid @min-[760px]/settings-shell:overflow-hidden @min-[760px]/settings-shell:[grid-template-columns:var(--settings-rail)_minmax(0,1fr)] @min-[760px]/settings-shell:[grid-template-rows:minmax(0,1fr)] @min-[760px]/settings-shell:gap-[var(--space-5)]">
        <aside className="mb-[var(--space-4)] @min-[760px]/settings-shell:mb-0 @min-[760px]/settings-shell:flex @min-[760px]/settings-shell:h-full @min-[760px]/settings-shell:min-h-0 @min-[760px]/settings-shell:flex-col @min-[760px]/settings-shell:overflow-hidden">
          <SettingsSearch
            value={query}
            onChange={setQuery}
            inputRef={searchRef}
            onEnterList={focusActiveCategory}
          />
          <SettingsSidebar
            visibleIds={visibleSet}
            active={active}
            onSelect={setActive}
            query={query}
            onClearSearch={() => setQuery('')}
            onFocusSearch={focusSearch}
          />
        </aside>

        <div className="min-w-0 w-full max-w-[1100px] mx-auto flex-auto self-start [container-type:inline-size] [container-name:settings] @min-[760px]/settings-shell:flex @min-[760px]/settings-shell:h-full @min-[760px]/settings-shell:min-h-0 @min-[760px]/settings-shell:flex-col @min-[760px]/settings-shell:overflow-hidden @min-[760px]/settings-shell:pr-[var(--space-2)]">
          <header className="z-10 mb-[var(--space-5)] flex shrink-0 items-center gap-[var(--space-3)] border-b border-[color-mix(in_srgb,var(--chrome-fg)_7%,transparent)] bg-[var(--chrome-bg)] pb-[var(--space-4)]">
            {CatIcon && (
              <span
                className="inline-flex h-[32px] w-[32px] shrink-0 items-center justify-center rounded-[var(--chrome-radius-pill)] border border-transparent bg-[color-mix(in_srgb,var(--chrome-accent)_12%,var(--chrome-bg))] text-[color:var(--chrome-accent)]"
                aria-hidden="true"
              >
                <CatIcon size={16} />
              </span>
            )}
            <h1 className="m-0 flex-auto [font-family:var(--font-sans)] text-[length:var(--text-xl)] font-bold tracking-[-0.02em] text-[color:var(--chrome-fg)]">
              {t(cat.labelKey, { defaultValue: cat.defaultLabel })}
            </h1>
            {cat.restart && <RestartBadge />}
          </header>

          <div className="[&>*:first-child]:mt-0 @min-[760px]/settings-shell:min-h-0 @min-[760px]/settings-shell:flex-1 @min-[760px]/settings-shell:overflow-y-auto @min-[760px]/settings-shell:overscroll-contain">
            {renderCategory(active)}
          </div>
        </div>
      </div>
    </div>
  );
}
