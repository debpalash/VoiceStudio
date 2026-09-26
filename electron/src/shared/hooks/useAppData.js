import { useState, useEffect, useLayoutEffect, useRef } from 'react';
import { useAppStore } from '../store';
import { listProfiles } from '../api/profiles';
import { listHistory } from '../api/generate';
import { listProjects } from '../api/projects';
import { listDubHistory } from '../api/dub';
import { listExportHistory } from '../api/exports';
import { modelStatus as apiModelStatus } from '../api/system';
import { useModelStatus } from '../api/hooks';
import useRealtimeEvents from './useRealtimeEvents';
import { mergeDescribedAttrs } from '../utils/voiceInstruct';
import { sanitizeOmniUi } from '../utils/omniUiSchema';
import { loadLatest, retryInitialLoad } from '../utils/initialLoadRetry';
import { queueJsonWrite } from '../utils/coalescedJsonStorage';

/**
 * Encapsulates all data-loading effects, localStorage persistence,
 * real-time WebSocket updates, and model-status pill management.
 */

// Dub steps that describe live, in-process work ('uploading', 'transcribing',
// 'generating', 'stopping') must never be restored across an app restart: the
// task they referred to died with the process, so rehydrating one leaves the
// Dub tab waiting forever on progress that will never arrive (blank pane +
// eternal spinner — the "stuck on dubbing since I updated" reports, and a
// reinstall doesn't clear the webview's localStorage). Only settled states
// come back.
const STABLE_DUB_STEPS = new Set(['idle', 'editing', 'done']);
export const OMNI_UI_KEY = 'omni_ui';

/** Clamp a persisted dubStep to a state that is valid after a cold start.
 *  Stable steps pass through; transient (and unknown/corrupt) values fall
 *  back to 'editing' when the session has segments to show, else 'idle'. */
export function clampRestoredDubStep(savedStep, savedSegments) {
  if (STABLE_DUB_STEPS.has(savedStep)) return savedStep;
  return Array.isArray(savedSegments) && savedSegments.length > 0 ? 'editing' : 'idle';
}

export default function useAppData() {
  const mode = useAppStore((s) => s.mode);
  const setMode = useAppStore((s) => s.setMode);
  const defineMethod = useAppStore((s) => s.defineMethod);
  const setDefineMethod = useAppStore((s) => s.setDefineMethod);
  const uiScale = useAppStore((s) => s.uiScale);
  const setUiScale = useAppStore((s) => s.setUiScale);
  const setText = useAppStore((s) => s.setText);
  const text = useAppStore((s) => s.text);
  const setLanguage = useAppStore((s) => s.setLanguage);
  const language = useAppStore((s) => s.language);
  const setIsSidebarCollapsed = useAppStore((s) => s.setIsSidebarCollapsed);
  const isSidebarCollapsed = useAppStore((s) => s.isSidebarCollapsed);
  const setSidebarTab = useAppStore((s) => s.setSidebarTab);
  const sidebarTab = useAppStore((s) => s.sidebarTab);
  const setVdStates = useAppStore((s) => s.setVdStates);
  const vdStates = useAppStore((s) => s.vdStates);
  const speed = useAppStore((s) => s.speed);
  const setSpeed = useAppStore((s) => s.setSpeed);
  const steps = useAppStore((s) => s.steps);
  const setSteps = useAppStore((s) => s.setSteps);
  const cfg = useAppStore((s) => s.cfg);
  const setCfg = useAppStore((s) => s.setCfg);
  const denoise = useAppStore((s) => s.denoise);
  const setDenoise = useAppStore((s) => s.setDenoise);
  const dubJobId = useAppStore((s) => s.dubJobId);
  const setDubJobId = useAppStore((s) => s.setDubJobId);
  const dubFilename = useAppStore((s) => s.dubFilename);
  const setDubFilename = useAppStore((s) => s.setDubFilename);
  const dubDuration = useAppStore((s) => s.dubDuration);
  const setDubDuration = useAppStore((s) => s.setDubDuration);
  const dubSegments = useAppStore((s) => s.dubSegments);
  const setDubSegments = useAppStore((s) => s.setDubSegments);
  const dubLang = useAppStore((s) => s.dubLang);
  const setDubLang = useAppStore((s) => s.setDubLang);
  const dubLangCode = useAppStore((s) => s.dubLangCode);
  const setDubLangCode = useAppStore((s) => s.setDubLangCode);
  const dubTracks = useAppStore((s) => s.dubTracks);
  const setDubTracks = useAppStore((s) => s.setDubTracks);
  const dubStep = useAppStore((s) => s.dubStep);
  const setDubStep = useAppStore((s) => s.setDubStep);
  const dubTranscript = useAppStore((s) => s.dubTranscript);
  const setDubTranscript = useAppStore((s) => s.setDubTranscript);
  const exportTracks = useAppStore((s) => s.exportTracks);
  const setExportTracks = useAppStore((s) => s.setExportTracks);
  const preserveBg = useAppStore((s) => s.preserveBg);
  const setPreserveBg = useAppStore((s) => s.setPreserveBg);
  const defaultTrack = useAppStore((s) => s.defaultTrack);
  const setDefaultTrack = useAppStore((s) => s.setDefaultTrack);

  const [profiles, setProfiles] = useState([]);
  const [history, setHistory] = useState([]);
  const [dubHistory, setDubHistory] = useState([]);
  const [studioProjects, setStudioProjects] = useState([]);
  const [exportHistory, setExportHistory] = useState([]);
  const [showOverrides, setShowOverrides] = useState(false);
  const [omniUiRestoreComplete, setOmniUiRestoreComplete] = useState(false);
  const omniUiWriteDisposerRef = useRef(null);

  const omniUiSnapshotRef = useRef(null);
  const omniUiSnapshot = {
    uiScale,
    text,
    mode,
    defineMethod,
    vdStates,
    language,
    isSidebarCollapsed,
    sidebarTab,
    dubJobId,
    dubFilename,
    dubDuration,
    dubSegments,
    dubLang,
    dubLangCode,
    dubTracks,
    dubStep,
    dubTranscript,
    exportTracks,
    preserveBg,
    defaultTrack,
    exportHistory,
    speed,
    steps,
    cfg,
    denoise,
    showOverrides,
  };
  // Only expose committed state to the deferred writer. Publishing during
  // render would let a timer or lifecycle flush observe a concurrent render
  // that React later abandons. Layout effects run before the passive effect
  // that registers the provider, without copying any nested document data.
  useLayoutEffect(() => {
    omniUiSnapshotRef.current = omniUiSnapshot;
  });

  // ── Model status (TanStack Query) ──
  // Sysinfo lives in Header (the only consumer) so its 5s poll doesn't
  // re-render the whole App tree.
  const msQuery = useModelStatus();
  const modelStatus = msQuery.data?.status ?? 'idle';
  const modelSubStage = msQuery.data?.sub_stage ?? null;
  const modelDetail = msQuery.data?.detail ?? '';
  const modelError = msQuery.data?.error ?? null;
  const modelProgress = msQuery.data?.progress ?? null;

  // ── Model loading pill ──
  const prevModelStatusRef = useRef(modelStatus);
  useEffect(() => {
    const prev = prevModelStatusRef.current;
    prevModelStatusRef.current = modelStatus;
    const pill = useAppStore.getState();
    if (modelStatus === 'loading') {
      const label = modelDetail || 'Loading model…';
      if (prev !== 'loading' && pill.stage === 'idle') pill.showPill('loading-model', label);
      else if (pill.stage === 'loading-model') pill.setPillLabel(label);
      // Forward real-time percentage from backend
      if (modelProgress !== null && pill.stage === 'loading-model') {
        pill.setPillProgress(modelProgress);
      }
    }
    if (modelStatus === 'ready' && prev === 'loading' && pill.stage === 'loading-model')
      pill.completePill('Model ready');
    if (modelSubStage === 'error' && modelError && pill.stage === 'loading-model')
      pill.errorPill(modelError);
  }, [modelStatus, modelSubStage, modelDetail, modelError, modelProgress]);

  // ── Data loading callbacks ──
  // WS-triggered reloads swallow failures: keeping the previous list is
  // better than blanking the UI, and the warn gives "my voices vanished"
  // reports a cause (#1158). The INITIAL load passes `{ rethrow: true }` so
  // retryInitialLoad can retry — there is no previous list to keep yet.
  // Each loader is last-write-wins by invocation order: a slow in-flight
  // request (the initial retry loop overlaps freely with WS reloads) must
  // not overwrite the fresher list a later reload already applied. Plain
  // per-render closures over stable imports/setters — a useRef-free module
  // would need hooks inside a helper, which rules-of-hooks forbids.
  const loadersRef = useRef({ profiles: 0, history: 0, dub: 0, projects: 0, exports: 0 });
  const makeLoader =
    (key, fetch, set, label) =>
    ({ rethrow } = {}) =>
      loadLatest({
        generations: loadersRef.current,
        key,
        fetch,
        apply: set,
        label,
        rethrow,
      });
  const loadProfiles = makeLoader('profiles', listProfiles, setProfiles, 'voice profiles');
  const loadHistory = makeLoader('history', listHistory, setHistory, 'generation history');
  const loadDubHistory = makeLoader('dub', listDubHistory, setDubHistory, 'dub history');
  const loadProjects = makeLoader('projects', listProjects, setStudioProjects, 'projects');
  const loadExportHistory = makeLoader(
    'exports',
    listExportHistory,
    setExportHistory,
    'export history',
  );

  // ── WebSocket real-time updates ──
  useRealtimeEvents({
    projects: () => loadProjects(),
    profiles: () => loadProfiles(),
    dub_history: () => loadDubHistory(),
    export_history: () => loadExportHistory(),
    generation_history: () => loadHistory(),
  });

  // ── Initial data load with backend retry ──
  useEffect(() => {
    const cancelledRef = { cancelled: false };
    const loadAll = async () => {
      let delay = 1000;
      while (!cancelledRef.cancelled) {
        try {
          await apiModelStatus();
          break;
        } catch (e) {}
        await new Promise((r) => setTimeout(r, delay));
        delay = Math.min(delay * 2, 4000);
      }
      if (cancelledRef.cancelled) return;
      // Initial loads retry until FIRST success (#1158 class): a later
      // (WS-triggered) reload failure keeps the previous list, but the first
      // load has no previous list to keep — one transient failure used to
      // leave the panel empty, which read as "my voices are gone".
      retryInitialLoad(() => loadProfiles({ rethrow: true }), cancelledRef);
      retryInitialLoad(() => loadHistory({ rethrow: true }), cancelledRef);
      retryInitialLoad(() => loadDubHistory({ rethrow: true }), cancelledRef);
      retryInitialLoad(() => loadProjects({ rethrow: true }), cancelledRef);
      retryInitialLoad(() => loadExportHistory({ rethrow: true }), cancelledRef);
    };
    loadAll();
    // Restore local UI state
    try {
      // Whitelist + shape-check every persisted field (audit: the #1067 class
      // was healed per-field; this closes it generically — malformed values
      // are dropped up front instead of throwing mid-restore and silently
      // discarding every field after the bad one).
      const saved = sanitizeOmniUi(JSON.parse(localStorage.getItem(OMNI_UI_KEY) || '{}'));
      if (saved.uiScale) setUiScale(saved.uiScale);
      if (saved.text) setText(saved.text);
      // Legacy shim (voice-studio-unification P4): the old 'clone'/'design'
      // navigation modes are now one 'studio' workspace; the split lives on
      // as the defineMethod ('audio'|'design').
      if (saved.mode === 'clone') {
        setMode('studio');
        setDefineMethod('audio');
      } else if (saved.mode === 'design') {
        setMode('studio');
        setDefineMethod('design');
      } else if (saved.mode === 'queue') {
        // Legacy shim: the batch queue's mode id is 'batch' (the store's Mode
        // union); 'queue' was an App.jsx-only id that nothing could set, but
        // normalize any persisted copy of it rather than strand the restore.
        setMode('batch');
      } else if (saved.mode) setMode(saved.mode);
      if (saved.defineMethod) setDefineMethod(saved.defineMethod);
      // #983: legacy localStorage state had no shape validation at all — a
      // partial/corrupt saved.vdStates crashed DesignMethodPanel on restore.
      // Mirror useProfiles.js's guard: require a plain object, then complete
      // it to the full CATEGORIES shape (missing/unknown keys → 'Auto').
      if (saved.vdStates && typeof saved.vdStates === 'object')
        setVdStates(mergeDescribedAttrs(saved.vdStates));
      if (saved.language) setLanguage(saved.language);
      if (saved.isSidebarCollapsed !== undefined) setIsSidebarCollapsed(saved.isSidebarCollapsed);
      if (saved.sidebarTab) setSidebarTab(saved.sidebarTab);
      if (saved.dubJobId) setDubJobId(saved.dubJobId);
      if (saved.dubFilename) setDubFilename(saved.dubFilename);
      if (saved.dubDuration !== undefined) setDubDuration(saved.dubDuration);
      if (saved.dubSegments)
        setDubSegments(
          saved.dubSegments.map((s) => ({ ...s, text_original: s.text_original || s.text || '' })),
        );
      if (saved.dubLang) setDubLang(saved.dubLang);
      if (saved.dubLangCode) setDubLangCode(saved.dubLangCode);
      if (saved.dubTracks) setDubTracks(saved.dubTracks);
      if (saved.dubStep) setDubStep(clampRestoredDubStep(saved.dubStep, saved.dubSegments));
      if (saved.dubTranscript) setDubTranscript(saved.dubTranscript);
      if (saved.exportTracks) setExportTracks(saved.exportTracks);
      if (saved.preserveBg !== undefined) setPreserveBg(saved.preserveBg);
      if (saved.defaultTrack) setDefaultTrack(saved.defaultTrack);
      if (saved.exportHistory) setExportHistory(saved.exportHistory);
      if (saved.speed) setSpeed(saved.speed);
      if (saved.steps) setSteps(saved.steps);
      if (saved.cfg) setCfg(saved.cfg);
      if (saved.denoise !== undefined) setDenoise(saved.denoise);
      if (saved.showOverrides !== undefined) setShowOverrides(saved.showOverrides);
    } catch (e) {
      // Preserve the existing fail-open recovery behavior: malformed or
      // inaccessible legacy state falls back to the initialized defaults.
    } finally {
      // The initial persistence effect closes over `false` and therefore
      // cannot flush defaults. The restored render becomes the first writer.
      setOmniUiRestoreComplete(true);
    }
    return () => {
      cancelledRef.cancelled = true;
    };
  }, []);

  // ── Persist to localStorage ──
  useEffect(() => {
    if (!omniUiRestoreComplete) return undefined;
    // Replacements must retain the scheduler's original maximum-wait window.
    // React runs a dependency effect's cleanup before every new setup, so the
    // generation disposer is intentionally reserved for a true unmount below.
    omniUiWriteDisposerRef.current = queueJsonWrite(OMNI_UI_KEY, () => omniUiSnapshotRef.current);
    return undefined;
  }, [
    omniUiRestoreComplete,
    uiScale,
    text,
    mode,
    defineMethod,
    vdStates,
    language,
    isSidebarCollapsed,
    sidebarTab,
    dubJobId,
    dubFilename,
    dubDuration,
    dubSegments,
    dubLang,
    dubLangCode,
    dubTracks,
    dubStep,
    dubTranscript,
    exportTracks,
    preserveBg,
    defaultTrack,
    exportHistory,
    speed,
    steps,
    cfg,
    denoise,
    showOverrides,
  ]);

  useEffect(
    () => () => {
      omniUiWriteDisposerRef.current?.();
      omniUiWriteDisposerRef.current = null;
    },
    [],
  );

  return {
    profiles,
    history,
    dubHistory,
    studioProjects,
    exportHistory,
    showOverrides,
    setShowOverrides,
    modelStatus,
    loadProfiles,
    loadHistory,
    loadDubHistory,
    loadProjects,
    loadExportHistory,
  };
}
