/**
 * First-run bootstrap splash — the "installing" act of the first-run journey.
 *
 * Two data sources drive this UI:
 *   1. `bootstrap_status` Tauri command (polled every 1 s) — coarse stage.
 *   2. `bootstrap-log` + `bootstrap-progress` Tauri events — live stdout
 *      from `uv sync`, ffmpeg byte counts, etc. The log panel shows the
 *      last N lines so users can see *something* happening during the 5–10
 *      min dependency install.
 *
 * Built on standard shadcn primitives + Tailwind utilities (themed by the
 * palette tokens), sharing the breathing-waveform / rise keyframes in
 * firstrun.css so setup → install → model wizard reads as one experience.
 */
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
import {
  Brush,
  Check,
  ChevronDown,
  ChevronRight,
  Clipboard,
  FolderOpen,
  Globe,
  Lightbulb,
  Wrench,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { copyText } from '../utils/copyText';
import { useTranslation } from 'react-i18next';
import i18n, { LANGUAGES } from '../i18n';
import { useAppStore } from '../store';
import { getApiBase } from '../utils/apiBase';
import { startSplashWatchdog, startHealthRecoveryPoll } from '../utils/splashWatchdog';
import { flushApplicationPersistence } from '../utils/persistenceLifecycle';
import { Button, Progress, Select } from '../ui';
import UiScaleControl from './UiScaleControl';

// First-run only: keep the setup screen out of the main bundle so every
// regular launch pays nothing for it.
const FirstRunSetup = lazy(() => import('./FirstRunSetup'));

const getSystemLanguage = () => {
  if (typeof navigator === 'undefined') return 'en';
  const navLang = navigator.language || (navigator.languages && navigator.languages[0]) || 'en';
  if (navLang.toLowerCase().includes('tw') || navLang.toLowerCase().includes('hk')) return 'zh-TW';
  const match = [
    'zh-CN',
    'es',
    'fr',
    'de',
    'ja',
    'pt',
    'it',
    'ru',
    'ko',
    'hi',
    'tr',
    'pl',
    'nl',
    'sv',
    'th',
    'vi',
    'id',
    'uk',
    'ar',
  ].find((code) => navLang.startsWith(code.split('-')[0]));
  return match || 'en';
};

// Vite injects package.json version at build time.
const APP_VERSION = __APP_VERSION__ || '0.0.0';

const STAGE_LABEL = {
  checking: 'Checking environment…',
  downloading_uv: 'Downloading uv (Python package manager)…',
  creating_venv: 'Creating Python virtual environment…',
  installing_deps: 'Installing dependencies — first run, 5–10 min.',
  starting_backend: 'Starting backend…',
  ready: 'Ready',
  failed: 'Setup failed',
  ipc_lost: 'Startup issue detected',
};

/** Race a promise against a timeout. Used for IPC calls made from the
 *  recovery panel (#879): the whole point of that state is that IPC may be
 *  hung, so every invoke gets a bounded wait + a manual fallback. */
function withTimeout(promise, ms) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error('ipc timeout')), ms)),
  ]);
}

/** Platform-default log directory, computed client-side (no IPC available in
 *  the recovery state). Mirrors src-tauri/src/backend.rs `backend_log_path()`.
 *  The Windows form uses %LOCALAPPDATA% literally — Explorer expands it. */
function defaultLogDirForPlatform() {
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent || '' : '';
  if (ua.includes('Windows')) return '%LOCALAPPDATA%\\OmniVoice\\Logs';
  if (ua.includes('Mac')) return '~/Library/Logs/OmniVoice';
  return '~/.local/state/OmniVoice';
}

/** WebView2 profile cache path shown in the manual-repair fallback (#879). */
const WEBVIEW_CACHE_PATH_WIN =
  '%LOCALAPPDATA%\\com.debpalash.omnivoice-studio\\EBWebView\\Default\\Cache';

/** True on Windows. Deliberately reads the user agent, NOT a Tauri plugin —
 *  in the recovery state IPC is presumed dead, so OS detection must not
 *  round-trip through it. */
function isWindowsUA() {
  return typeof navigator !== 'undefined' && (navigator.userAgent || '').includes('Windows');
}

const STEPS = [
  'checking',
  'downloading_uv',
  'creating_venv',
  'installing_deps',
  'starting_backend',
];

// Stages that only occur when there is actual first-run/repair work to do.
// `awaiting_setup` renders its own screen (FirstRunSetup) rather than the
// step list below, but it still counts as "install work observed" (#1894):
// reaching it means Rust found no venv and is about to do real work, so the
// journey chrome should already be armed by the time the step list appears.
const INSTALL_STAGES = ['downloading_uv', 'creating_venv', 'installing_deps', 'awaiting_setup'];

// How many log lines the <pre> shows. `logs` state holds exactly this tail and
// nothing more, so the per-event array copy stays bounded no matter how long a
// bootstrap runs.
//
// The FULL run lives in `allLogsRef` instead (#1847). Four consumers need every
// line — the Activity counter, the Copy button, the actionable failure hints and
// the unrecoverable-retry gate — and capping the state they read meant a cold
// install silently destroyed its own early output. A ref rather than state
// because appending to it is O(1); `totalLines` is what makes a new line
// re-render. It lives for exactly one bootstrap: both retry paths clear it, and
// App.jsx unmounts the splash the moment the stage flips to 'ready'.
const VISIBLE_LOG_LINES = 200;

/** Scan logs + error message for known failure patterns and return i18n keys
 *  for actionable hints (resolved with `t(...)` at render — English defaults
 *  live in locales/en.json under `bootstrap.hint_*`).
 *
 *  Exported (#1177) so BackendStartFailureNotice — which surfaces the SAME
 *  `BootstrapStage::Failed { message }` after the splash is gone — offers the
 *  same actionable next steps instead of re-deriving a second, drifting set. */
export function detectHints(message, logs = []) {
  const hints = [];
  const all = (message || '') + '\n' + logs.map((l) => l.line).join('\n');
  if (/README\.md/i.test(all)) hints.push('bootstrap.hint_readme');
  // python-build-standalone download failure (issue #57, #60): user's network
  // can't reach the github.com release. We auto-retry with a system-Python
  // fallback in bootstrap.rs, but if that also fails the user needs an actionable next step.
  if (/python-build-standalone|managed-python download failed/i.test(all)) {
    hints.push('bootstrap.hint_python_mirror');
  }
  if (/uv.*download|uv.*install/i.test(all) && /timeout|connection/i.test(all))
    hints.push('bootstrap.hint_uv_timeout');
  if (/uv sync failed/i.test(all)) hints.push('bootstrap.hint_uv_sync');
  if (/hatchling|build_editable/i.test(all)) hints.push('bootstrap.hint_build_backend');
  if (/ffmpeg/i.test(all) && /download|timeout/i.test(all)) hints.push('bootstrap.hint_ffmpeg');
  // #1223: Windows' WSAEADDRINUSE text is "only one usage of each socket
  // address is normally permitted" — it contains neither "port ... in use" nor
  // "address ... in use", and the OS translates it into the user's locale
  // (the report that surfaced this was in Russian). Match the locale-
  // independent errnos too: 10048 (Windows), 48 (macOS/BSD), 98 (Linux), and
  // the backend's own EX_CONFIG exit code for this case.
  if (
    /port.*in use|address.*in use|errno 10048|errno 48|errno 98|only one usage of each socket|exit code 78/i.test(
      all,
    )
  )
    hints.push('bootstrap.hint_port');
  if (/no error output/i.test(all)) hints.push('bootstrap.hint_silent_crash');
  if (/seems stuck at|never reported ready/i.test(all)) hints.push('bootstrap.hint_stuck');
  if (/blocking GitHub|couldn't download Python|python-build-standalone|dns error/i.test(all))
    hints.push('bootstrap.hint_github_blocked');
  // Intel-Mac backend unsupported (#889): PyTorch ships no macOS x86_64
  // wheels, so bootstrap.rs pre-fails with this message before any sync.
  if (/Intel Macs can't run the local AI backend/i.test(all)) {
    return ['bootstrap.hint_intel_mac']; // retrying can never help — show only this
  }
  if (hints.length === 0) hints.push('bootstrap.hint_default');
  return hints;
}

/** Failures no retry can ever fix — offering a Retry button for these is the
 *  dead end #1112 reported ("clicking the buttons does nothing"): the bootstrap
 *  re-fails identically every time. Today that's the Intel Mac (#889): PyTorch
 *  ships no macOS x86_64 wheels, so the dependency set can never resolve there.
 *  Keyed off the same hint the matcher produces, so the two can't drift.
 *  Pure + exported for tests. */
export function isUnrecoverableFailure(message, logs = []) {
  return detectHints(message, logs).includes('bootstrap.hint_intel_mac');
}

function formatEta(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '';
  if (seconds < 60) return '<1m';
  return `${Math.round(seconds / 60)}m`;
}

function formatBytes(n) {
  if (!n || n < 0) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

/** Whisper waveform — same speech-cadence silhouette as the setup screen.
 *  Memoized like its FirstRunSetup/SetupWizard twins: the splash re-renders
 *  every poll tick and the silhouette never changes. */
function Waveform({ bars = 96 }) {
  const heights = useMemo(
    () =>
      Array.from({ length: bars }, (_, i) => {
        const t = i / bars;
        const v = Math.abs(
          Math.sin(t * Math.PI * 7.3) * 0.55 +
            Math.sin(t * Math.PI * 2.1 + 1.2) * 0.3 +
            Math.sin(t * Math.PI * 17.0 + 0.4) * 0.15,
        );
        return 0.18 + v * 0.82;
      }),
    [bars],
  );
  return (
    <div className="fr-wave" aria-hidden="true">
      {heights.map((h, i) => (
        <span
          key={i}
          className="fr-wave__bar"
          style={{ '--h': h, '--d': `${(i * 73) % 1400}ms` }}
        />
      ))}
    </div>
  );
}

/** Three-stage breadcrumb — setup done, installing active. */
function JourneyRail({ t }) {
  const stages = [
    [t('firstrun.stage_setup', 'Setup'), 'done'],
    [t('firstrun.installing_title', 'Installing'), 'active'],
    [t('firstrun.stage_models', 'Models & engines'), 'pending'],
  ];
  return (
    <nav
      className="flex flex-wrap items-center gap-x-5 gap-y-2"
      aria-label={t('bootstrap.title', 'VoiceStudio')}
    >
      {stages.map(([label, state]) => (
        <span
          key={label}
          className={cn(
            'inline-flex items-center gap-1.5 font-mono text-[0.62rem] font-semibold uppercase tracking-[0.14em]',
            state === 'active'
              ? 'text-fg'
              : state === 'done'
                ? 'text-fg-muted'
                : 'text-fg-subtle/60',
          )}
        >
          <span
            className={cn(
              'h-1.5 w-1.5 rounded-full',
              state === 'active'
                ? 'bg-primary shadow-[0_0_6px_1px_var(--color-brand-glow)]'
                : state === 'done'
                  ? 'bg-success'
                  : 'bg-fg-subtle/40',
            )}
            aria-hidden="true"
          />
          {label}
        </span>
      ))}
    </nav>
  );
}

/**
 * Recovery panel for the stuck-startup state (#879): the Tauri IPC layer is
 * silent AND the backend never answered /health within the recovery window.
 * Explains what happened and offers actionable exits instead of an infinite
 * spinner. The "Repair and restart" affordance is Windows-only (it clears the
 * cache-only directories under the WebView2 `EBWebView` profile — a
 * Windows-specific artifact) and only
 * exists inside this error-recovery state, never as default-mode UI.
 */
function IpcLostRecovery({ t }) {
  const [showLogHint, setShowLogHint] = useState(false);
  const [repairing, setRepairing] = useState(false);
  const [repairFailed, setRepairFailed] = useState(false);

  const handleOpenLogs = async () => {
    try {
      // Best effort over IPC (it may be partially alive); bounded so a hung
      // invoke can't make the button feel dead.
      const { invoke } = await import('@tauri-apps/api/core');
      const tail = await withTimeout(invoke('read_log_tail', { source: 'backend' }), 3000);
      if (!tail?.path) throw new Error('no log path');
      const { revealItemInDir } = await import('@tauri-apps/plugin-opener');
      await withTimeout(revealItemInDir(tail.path), 3000);
    } catch {
      // IPC is dead (the expected case here) — show where the logs live.
      setShowLogHint(true);
    }
  };

  const handleRepairRestart = async () => {
    if (repairing) return;
    if (!confirm(t('bootstrap.ipc_lost_repair_confirm'))) return;
    setRepairing(true);
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      try {
        await flushApplicationPersistence();
      } catch (error) {
        // Persistence may be the broken subsystem that led here. Keep the
        // explicit recovery action available, matching the native exit timeout.
        console.warn('[persistence] cache-repair flush failed', error);
      }
      // The command returns once the persistence-gated restart is queued; its
      // native three-second deadline handles a webview that cannot acknowledge.
      // This outer timeout catches an IPC bridge too broken to queue it at all.
      await withTimeout(invoke('clear_webview_cache_and_relaunch'), 8000);
    } catch (e) {
      if (e?.message !== 'ipc timeout') console.error('repair failed', e);
      setRepairFailed(true);
      setRepairing(false);
    }
  };

  return (
    <section className="fr-rise flex flex-col gap-2.5" style={{ '--rise': 1 }}>
      <h2 className="m-0 font-mono text-[0.62rem] font-semibold uppercase tracking-[0.18em] text-fg-muted">
        {t('bootstrap.ipc_lost_title', "The app can't finish starting")}
      </h2>
      <p className="m-0 text-sm leading-relaxed text-fg-muted">{t('bootstrap.ipc_lost_body')}</p>
      {showLogHint && (
        <ErrorBox>
          {t('bootstrap.ipc_lost_log_hint', { path: defaultLogDirForPlatform() })}
        </ErrorBox>
      )}
      {repairFailed && (
        <ErrorBox>
          {t('bootstrap.ipc_lost_repair_failed', { path: WEBVIEW_CACHE_PATH_WIN })}
        </ErrorBox>
      )}
      <div className="flex items-center justify-end gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={handleOpenLogs}
          leading={<FolderOpen size={12} />}
        >
          {t('bootstrap.ipc_lost_open_logs', 'Open logs')}
        </Button>
        {isWindowsUA() && (
          <Button
            variant="primary"
            onClick={handleRepairRestart}
            disabled={repairing}
            leading={<Wrench size={12} />}
          >
            {repairing
              ? t('bootstrap.ipc_lost_repairing', 'Repairing…')
              : t('bootstrap.ipc_lost_repair', 'Repair and restart')}
          </Button>
        )}
      </div>
    </section>
  );
}

/** Mono error block. */
/** When the backend last produced ANY line of bootstrap output.
 *
 * #1791: the stall watchdog in `useBootstrapStage` keys on `bootstrap_status`,
 * which sits on a single stage for the whole of a slow start — a cold `import
 * torch` off a mapped network drive can hold `starting_backend` for many
 * minutes. The narration that proves it is alive ("Loading ML runtime
 * (PyTorch)…") arrives on the separate `bootstrap-log` event stream instead,
 * so the watchdog never saw it and called a live launch stuck. A splash that
 * is still printing new lines is by definition not the info-less spinner the
 * watchdog exists to break.
 *
 * Module-scoped rather than a ref because the two halves live in different
 * components — `BootstrapSplash` owns the event subscription, `useBootstrapStage`
 * owns the watchdog — and they are always mounted as a pair.
 */
let lastBootstrapLogTs = 0;

export function noteBootstrapLogActivity(ts = Date.now()) {
  lastBootstrapLogTs = ts;
}

function ErrorBox({ children }) {
  return (
    <pre className="m-0 overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-danger/10 px-3 py-2 font-mono text-[0.66rem] leading-relaxed text-danger shadow-[inset_2px_0_0_var(--color-danger)]">
      {children}
    </pre>
  );
}

export function BootstrapSplash({ stage, message, attempt = 0 }) {
  const { t } = useTranslation();
  const locale = useAppStore((s) => s.locale);
  const setLocale = useAppStore((s) => s.setLocale);

  const handleLocaleChange = (id) => {
    setLocale(id);
    i18n.changeLanguage(id);
  };

  const systemLang = getSystemLanguage();
  const [showSuggestion, setShowSuggestion] = useState(false);

  useEffect(() => {
    const dismissed = localStorage.getItem('dismissed_lang_suggestion');
    if (systemLang !== locale && systemLang !== 'en' && !dismissed) {
      setShowSuggestion(true);
    }
  }, [locale, systemLang]);

  const acceptSuggestion = () => {
    handleLocaleChange(systemLang);
    setShowSuggestion(false);
  };

  const dismissSuggestion = () => {
    localStorage.setItem('dismissed_lang_suggestion', 'true');
    setShowSuggestion(false);
  };

  // ── Hooks FIRST, derived values AFTER ──────────────────────────────────
  // Every useState/useRef must be declared before any plain `const` that
  // reads its result. When a derived const (e.g. `isUnrecoverable`, which
  // reads `logs`) sits *between* two hook calls, the production minifier
  // (esbuild) merges the declarations into one comma-list and can hoist the
  // derived const ahead of the `useState` binding it depends on — emitting
  // `isUnrecoverable = isFailed && f(message, logs), [logs] = useState([])`.
  // That is a temporal-dead-zone access ("Cannot access 'logs' before
  // initialization"): it throws during render, React unmounts to an empty
  // #root, and the whole app is a black screen. Dev/unminified builds
  // short-circuit on `isFailed` so they never trip it — it only bites the
  // minified release bundle (shipped in v0.3.22). Keeping all hooks above all
  // derived reads makes the class impossible regardless of how the minifier
  // reorders. (Guarded by tests/frontend that no derived const is interleaved
  // among hooks.)
  const [logs, setLogs] = useState([]);
  const [logsOpen, setLogsOpen] = useState(true);
  const [copied, setCopied] = useState(false);
  const [progress, setProgress] = useState(null);
  const [region, setRegionState] = useState('auto');
  const [retrying, setRetrying] = useState(false);
  // Stages actually seen during the CURRENT bootstrap attempt (see the
  // tracking effect below). Drives "done" ticks and journey visibility off
  // observed reality instead of list position (#1894).
  const [polledStages, setPolledStages] = useState(() => new Set([stage]));
  // Every line of this bootstrap. `logs` above is only the rendered tail.
  const [totalLines, setTotalLines] = useState(0);
  const allLogsRef = useRef([]);
  // Backfill and the live listener can overlap by a few lines at handover.
  // Dedup guards that seam and nothing else — see the listener.
  const handoverDoneRef = useRef(false);
  const logRef = useRef(null);
  const prevAttemptRef = useRef(attempt); // last attempt id Rust reported
  const prevProgRef = useRef(null); // {bytes, t} — last progress event
  const rateRef = useRef(0); // EMA bytes/sec across events

  // The union of what we polled and what actually logged.
  //
  // Neither source alone is complete. `bootstrap_status` is sampled ~1/s, so
  // a stage that starts and finishes between samples is never polled — on a
  // fast disk `creating_venv` routinely does. Stage-tagged `bootstrap-log`
  // lines close that gap: the Rust side emits them as the work happens, so a
  // line tagged with a stage is proof that stage ran, whether or not the
  // poll ever saw it.
  //
  // Lines are scoped to the current attempt so a retry cannot inherit the
  // previous one's evidence. The attempt is the id Rust stamps on both the
  // status reply and every log line (#1900) — an exact match, rather than a
  // clock comparison against a boundary this side had to guess at. (The
  // VISIBLE log is left alone: clearing it on a restart nobody asked for
  // would destroy the user's context, cf. #1847.)
  const observedStages = useMemo(() => {
    const seen = new Set(polledStages);
    for (const entry of allLogsRef.current) {
      if (entry?.stage && entry.attempt === attempt) seen.add(entry.stage);
    }
    return seen;
    // totalLines is the render signal for allLogsRef, which is a ref and so
    // cannot itself be a dependency. Reading the full run rather than the
    // rendered tail is the point: a stage whose only evidence scrolled out of
    // the capped tail would otherwise read as never having run (#1847).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [polledStages, totalLines, attempt]);

  const label = t(`bootstrap.${stage}`, STAGE_LABEL[stage]);
  const stepIndex = Math.max(0, STEPS.indexOf(stage));
  const isFailed = stage === 'failed';
  // Retrying an Intel-Mac install can never succeed — don't offer the dead end.
  // Reads the full run, not the rendered tail: an Intel-Mac marker printed
  // early in a long install used to scroll out of the capped array, and the
  // dead-end detection went with it. Only evaluated once `isFailed`, by which
  // point no more lines are arriving.
  const isUnrecoverable = isFailed && isUnrecoverableFailure(message, allLogsRef.current);

  const resetLogs = () => {
    allLogsRef.current = [];
    handoverDoneRef.current = false;
    setTotalLines(0);
    setLogs([]);
  };

  // True once any genuine install stage has been observed this session. On a
  // warm start the Rust stage jumps straight from `checking` to
  // `starting_backend` — nothing here ever fires — so the first-run install
  // chrome (journey rail, "Installing" heading, step list) stays suppressed
  // instead of fabricating completed work (#1894).
  const installWorkSeen = INSTALL_STAGES.some((s) => observedStages.has(s));

  // Clear the splash for a retry the user just asked for. The attempt
  // BOUNDARY is no longer this side's business (#1900) — Rust bumps its
  // attempt id inside `respawn_backend`, before the stage moves, and the
  // reset effect below follows that. What is left here is presentation: empty
  // the visible log and the polled-stage set so the user watches a fresh run
  // start, instead of the failed one's output sitting under a spinner.
  const beginAttempt = () => {
    resetLogs();
    setPolledStages(new Set());
  };

  const handleRetry = async () => {
    if (retrying) return;
    setRetrying(true);
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      beginAttempt();
      await invoke('retry_bootstrap');
    } catch (e) {
      console.error('retry failed', e);
    } finally {
      setRetrying(false);
    }
  };

  const handleCleanRetry = async () => {
    if (retrying) return;
    if (!confirm(t('bootstrap.clean_retry_confirm'))) return;
    setRetrying(true);
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      beginAttempt();
      await invoke('clean_and_retry_bootstrap');
    } catch (e) {
      console.error('clean retry failed', e);
    } finally {
      setRetrying(false);
    }
  };

  // Record `stage` as observed the moment it's seen, and reset when a new
  // attempt begins.
  //
  // Sticky WITHIN an attempt: the functional updater bails out (same Set
  // reference) once a stage is recorded, so it never un-observes and never
  // loops. But NOT across attempts — a Retry restarts the bootstrap from
  // `checking`, and what the previous attempt did says nothing about what
  // this one will do. Without the reset, stages the new attempt skips would
  // still render as completed, which is the very fabrication this change
  // exists to remove.
  //
  // A new attempt is a fact Rust reports, not something this side infers from
  // a sampled stage (#1900). Every real restart bumps the id — both retry
  // commands via `respawn_backend`, and the supervisor's own venv rebuild — so
  // the id changing IS the boundary, whether or not the poll ever sampled the
  // restart stage. The heuristics this replaces (arriving at
  // `checking`/`awaiting_setup`, leaving `failed`) could miss a Rust-initiated
  // restart by up to a poll interval and discard that attempt's earliest
  // evidence. Equality on an id cannot.
  useEffect(() => {
    if (prevAttemptRef.current !== attempt) {
      prevAttemptRef.current = attempt;
      setPolledStages(new Set([stage]));
      return;
    }
    setPolledStages((p) => (p.has(stage) ? p : new Set(p).add(stage)));
  }, [stage, attempt]);

  // Load persisted region on mount.
  useEffect(() => {
    if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return;
    (async () => {
      try {
        const { invoke } = await import('@tauri-apps/api/core');
        const r = await invoke('get_region');
        if (r) setRegionState(r);
      } catch {
        /* older build without region support */
      }
    })();
  }, []);

  const handleRegionChange = async (newRegion) => {
    setRegionState(newRegion);
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('set_region', { region: newRegion });
    } catch {
      /* silent */
    }
  };

  // Subscribe to live log + progress events from the Rust bootstrap.
  // Also backfill any logs emitted before the webview finished loading.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (!('__TAURI_INTERNALS__' in window)) return;
    let unlistenLog = null;
    let unlistenProgress = null;
    let cancelled = false;

    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        const { invoke } = await import('@tauri-apps/api/core');
        if (cancelled) return;

        // Backfill: fetch all log lines buffered on the Rust side before
        // the webview was ready to receive events.
        try {
          const buffered = await invoke('get_bootstrap_logs');
          if (!cancelled && Array.isArray(buffered) && buffered.length > 0) {
            const entries = buffered.map(({ attempt: a, stage: s, line }) => ({
              attempt: a ?? 0,
              stage: s,
              line,
              t: Date.now(),
            }));
            allLogsRef.current = entries;
            setTotalLines(entries.length);
            setLogs(entries.slice(-VISIBLE_LOG_LINES));
          }
        } catch {
          /* command may not exist in older builds */
        }

        // Subscribe to live events for anything new from here on.
        unlistenLog = await listen('bootstrap-log', (e) => {
          const { attempt: a, stage: s, line } = e.payload || {};
          if (!line) return;
          noteBootstrapLogActivity();
          // Dedup ONLY across the backfill→live seam. The overlap it exists
          // for can only happen on the first live event; running it for the
          // whole bootstrap silently dropped legitimate repeats, and installer
          // output repeats itself constantly. Telling a true repeat from a
          // replayed one needs a sequence number from the Rust side, so the
          // window is narrowed to where the ambiguity actually is instead of
          // guessing for the rest of the run.
          if (!handoverDoneRef.current) {
            const seam = allLogsRef.current.slice(-5);
            // The attempt is part of the identity (#1900, CodeRabbit): the
            // same stage and the same text from a DIFFERENT attempt is not a
            // replay of a buffered line, it is this attempt's own evidence.
            // Matching on stage+line alone dropped it, which can remove the
            // only proof for a stage the poll never sampled.
            if (seam.some((l) => l.attempt === (a ?? 0) && l.stage === s && l.line === line))
              return;
            handoverDoneRef.current = true;
          }
          const entry = { attempt: a ?? 0, stage: s, line, t: Date.now() };
          allLogsRef.current.push(entry);
          setTotalLines(allLogsRef.current.length);
          setLogs((prev) => {
            const next = prev.concat([entry]);
            return next.length > VISIBLE_LOG_LINES
              ? next.slice(next.length - VISIBLE_LOG_LINES)
              : next;
          });
        });
        unlistenProgress = await listen('bootstrap-progress', (e) => {
          const payload = e.payload || null;
          // EMA byte-rate from successive events → ETA for the long stretch.
          if (payload?.bytes_done != null) {
            const now = Date.now();
            const prev = prevProgRef.current;
            if (prev && payload.bytes_done > prev.bytes && now > prev.t) {
              const inst = (payload.bytes_done - prev.bytes) / ((now - prev.t) / 1000);
              rateRef.current = rateRef.current ? rateRef.current * 0.7 + inst * 0.3 : inst;
            }
            prevProgRef.current = { bytes: payload.bytes_done, t: now };
          }
          setProgress(payload);
        });
      } catch {
        /* not in Tauri or listen unavailable — silent */
      }
    })();
    return () => {
      cancelled = true;
      if (unlistenLog) unlistenLog();
      if (unlistenProgress) unlistenProgress();
    };
  }, []);

  // Auto-scroll the log panel to the latest line whenever it opens or
  // new lines arrive.
  useEffect(() => {
    if (logsOpen && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs, logsOpen]);

  // Auto-expand logs on failure so users can see + copy the full output.
  useEffect(() => {
    if (isFailed) setLogsOpen(true);
  }, [isFailed]);

  // Serializes the WHOLE run, not the visible tail (#1847). A user filing a
  // bootstrap bug is asked for this output, and the early lines — which stage
  // failed first, which mirror was reached — are the ones that scrolled out.
  const handleCopyLogs = () => {
    const all = allLogsRef.current;
    const logText =
      all.length === 0
        ? 'No log output captured.'
        : all.map((l) => `[${l.stage}] ${l.line}`).join('\n');
    const full =
      isFailed && message ? `ERROR: ${message}\n\n--- Bootstrap Logs ---\n${logText}` : logText;
    copyText(full)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      })
      .catch(() => {});
  };

  const stageProgress = progress && progress.stage === stage ? progress : null;
  const pctFromBytes = stageProgress?.percent != null ? stageProgress.percent : null;
  // Overall journey progress: completed steps + byte-progress within the
  // current step when the backend reports it.
  const overallPct = Math.min(
    100,
    ((stepIndex + (pctFromBytes != null ? pctFromBytes / 100 : 0.4)) / STEPS.length) * 100,
  );

  // First run with nothing installed: Rust parks in `awaiting_setup` and the
  // install-plan screen takes over. complete_setup advances the stage, and
  // the regular progress UI below resumes automatically on the next poll.
  // (Checked after every hook above so the setup → install transition keeps
  // the hook order stable.)
  if (stage === 'awaiting_setup') {
    return (
      <Suspense fallback={<div className="absolute inset-0 z-[9999] bg-bg" />}>
        <FirstRunSetup />
      </Suspense>
    );
  }

  return (
    <div className="absolute inset-0 z-[9999] flex flex-col items-center overflow-hidden bg-bg px-6 pt-12 font-sans text-fg">
      <div className="flex w-full max-w-[760px] flex-1 flex-col gap-4 overflow-y-auto pb-6">
        {/* ── Masthead: same identity as the setup screen ─────────────────── */}
        <header
          className="fr-rise flex flex-col gap-3 pb-1"
          style={{ '--rise': 0 }}
          data-tauri-drag-region
        >
          <Waveform />
          {/* Suppressed until real install work is observed — otherwise a
              warm start (or a repair sync) shows "Setup done / Installing
              active" for work that never happened (#1894). */}
          {installWorkSeen && <JourneyRail t={t} />}
          <div className="mt-2 flex flex-wrap items-end justify-between gap-6">
            <div className="min-w-0">
              {/* Version rides beside the app name — same masthead across all
                  three first-run acts (setup → install → models & engines), so a
                  screenshot from any of them identifies the build. */}
              <div className="flex flex-wrap items-baseline gap-2.5">
                <h1 className="m-0 font-serif text-[clamp(1.6rem,3vw,2.2rem)] font-semibold leading-tight tracking-tight">
                  {t('bootstrap.title', 'VoiceStudio')}
                </h1>
                <span className="font-mono text-[0.62rem] tracking-[0.14em] text-fg-subtle">
                  v{APP_VERSION}
                </span>
              </div>
              <p className="mt-1.5 text-sm leading-snug text-fg-muted" aria-live="polite">
                {label}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <UiScaleControl />
              <Select
                size="sm"
                value={locale}
                onChange={(e) => handleLocaleChange(e.target.value)}
                aria-label={t('firstrun.language', 'Language')}
              >
                {LANGUAGES.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.label}
                  </option>
                ))}
              </Select>
              <Select
                size="sm"
                value={region}
                onChange={(e) => handleRegionChange(e.target.value)}
                aria-label={t('firstrun.region_label', 'Download region')}
              >
                <option value="auto">🌐 {t('bootstrap.auto_detect', 'Auto-detect')}</option>
                <option value="global">🌐 {t('bootstrap.region_global')}</option>
                <option value="china">🇨🇳 {t('bootstrap.region_china')}</option>
                <option value="russia">🇷🇺 {t('bootstrap.region_russia')}</option>
                <option value="restricted">🌍 {t('bootstrap.region_restricted')}</option>
              </Select>
            </div>
          </div>
        </header>

        {showSuggestion && (
          <div
            className="fr-rise flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-primary/[0.08] px-3 py-2 text-sm"
            style={{ '--rise': 1 }}
          >
            <span className="inline-flex items-center gap-1.5">
              <Globe size={12} />{' '}
              {t('bootstrap.suggest_lang', {
                lang: LANGUAGES.find((l) => l.code === systemLang)?.label || systemLang,
              })}
            </span>
            <div className="flex items-center gap-1.5">
              <Button variant="ghost" size="sm" onClick={acceptSuggestion}>
                {t('common.yes', 'Yes')}
              </Button>
              <Button variant="ghost" size="sm" onClick={dismissSuggestion}>
                {t('common.no', 'No')}
              </Button>
            </div>
          </div>
        )}

        {stage === 'ipc_lost' ? (
          <IpcLostRecovery t={t} />
        ) : isFailed ? (
          <section className="fr-rise flex flex-col gap-2.5" style={{ '--rise': 1 }}>
            <h2 className="m-0 font-mono text-[0.62rem] font-semibold uppercase tracking-[0.18em] text-fg-muted">
              {t('bootstrap.failed', 'Setup failed')}
            </h2>
            <ErrorBox>{message || t('bootstrap.unknown_error')}</ErrorBox>
            <div className="text-sm leading-relaxed">
              <span className="inline-flex items-center gap-1.5 font-semibold">
                <Lightbulb size={12} /> {t('bootstrap.what_to_try', 'What to try:')}
              </span>
              <ul className="mt-1.5 flex list-disc flex-col gap-1.5 pl-5 text-fg-muted">
                {detectHints(message, allLogsRef.current).map((key) => (
                  <li key={key}>{t(key)}</li>
                ))}
              </ul>
            </div>
            <div className="flex items-center justify-end gap-2">
              {/* #1112: some failures can NEVER be retried away — an Intel Mac
                  has no PyTorch wheels, so every retry re-fails identically and
                  the buttons just look broken ("clicking them does nothing").
                  Say so plainly and don't offer the dead end. */}
              {isUnrecoverable ? (
                <span className="text-sm text-fg-muted">
                  {t(
                    'bootstrap.unrecoverable',
                    'Retrying cannot fix this — see the guidance above.',
                  )}
                </span>
              ) : (
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={handleCleanRetry}
                    disabled={retrying}
                    leading={<Brush size={12} />}
                  >
                    {t('bootstrap.clean_retry', 'Clean & Retry')}
                  </Button>
                  <Button variant="primary" onClick={handleRetry} disabled={retrying}>
                    {retrying
                      ? t('bootstrap.retrying', 'Retrying…')
                      : t('bootstrap.retry', 'Retry')}
                  </Button>
                </>
              )}
            </div>
          </section>
        ) : (
          <section className="fr-rise flex flex-col gap-2.5" style={{ '--rise': 1 }}>
            {/* Heading, step list and resume note only make sense once real
                install work has actually been observed — otherwise a warm
                start or a repair sync narrates a first-run install that
                never happened (#1894). The live stage label in the masthead
                and the progress meter below stay visible either way. */}
            {installWorkSeen && (
              <h2 className="m-0 font-mono text-[0.62rem] font-semibold uppercase tracking-[0.18em] text-fg-muted">
                {t('firstrun.installing_title', 'Installing')}
              </h2>
            )}
            {/* Overall journey meter. */}
            <Progress
              value={overallPct}
              tone="brand"
              size="md"
              aria-valuenow={Math.round(overallPct)}
            />
            {installWorkSeen && (
              <ol className="m-0 mt-1 flex list-none flex-col gap-2 p-0">
                {STEPS.map((s, i) => {
                  const activeStep = i === stepIndex;
                  // Done only if this stage was actually observed AND it isn't
                  // the one currently in progress — list POSITION alone lies on
                  // a warm start or a repair sync, where earlier stages in the
                  // fixed STEPS order are skipped by Rust entirely (#1894).
                  const done = !activeStep && observedStages.has(s);
                  return (
                    <li
                      key={s}
                      className={cn(
                        'flex min-w-0 items-center gap-2 text-sm',
                        !done && !activeStep && 'opacity-45',
                      )}
                    >
                      <span
                        className={cn(
                          'h-1.5 w-1.5 shrink-0 rounded-full',
                          done
                            ? 'bg-success shadow-[0_0_5px_1px_color-mix(in_srgb,var(--color-success)_50%,transparent)]'
                            : activeStep
                              ? 'bg-primary shadow-[0_0_6px_1px_var(--color-brand-glow)] fr-pulse'
                              : 'bg-fg-subtle/40',
                        )}
                        aria-hidden="true"
                      />
                      <span className={cn(activeStep && 'font-semibold', done && 'text-fg-muted')}>
                        {t(`bootstrap.${s}`, STAGE_LABEL[s])}
                      </span>
                      {activeStep && stageProgress && (
                        <span className="ml-auto whitespace-nowrap font-mono text-[0.64rem] tabular-nums text-fg-muted">
                          {formatBytes(stageProgress.bytes_done)}
                          {stageProgress.bytes_total > 0
                            ? ` / ${formatBytes(stageProgress.bytes_total)}`
                            : ''}
                          {pctFromBytes != null ? ` (${pctFromBytes}%)` : ''}
                          {stageProgress.bytes_total > 0 &&
                            rateRef.current > 0 &&
                            stageProgress.bytes_done < stageProgress.bytes_total &&
                            ` · ${t('firstrun.eta_left', {
                              eta: formatEta(
                                (stageProgress.bytes_total - stageProgress.bytes_done) /
                                  rateRef.current,
                              ),
                              defaultValue: '~{{eta}} left',
                            })}`}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ol>
            )}
            {installWorkSeen && (
              <p className="m-0 text-xs text-fg-subtle">
                {t(
                  'firstrun.resume_note',
                  'Interrupted downloads resume automatically — closing the app is safe.',
                )}
              </p>
            )}
          </section>
        )}

        {/* ── Live log — always reachable, quiet by design ────────────────── */}
        <section className="fr-rise flex flex-col gap-2.5" style={{ '--rise': 2 }}>
          <h2 className="m-0 flex items-center font-mono text-[0.62rem] font-semibold uppercase tracking-[0.18em] text-fg-muted">
            {t('firstrun.activity_title', 'Activity')}
            <span className="ml-auto tracking-[0.08em] text-fg-subtle">
              {totalLines > 0 && t('bootstrap.lines', { count: totalLines })}
            </span>
          </h2>
          <div className="flex items-center gap-1.5">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setLogsOpen((v) => !v)}
              leading={logsOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            >
              {logsOpen
                ? t('bootstrap.hide_logs', 'Hide logs')
                : t('bootstrap.show_logs', 'Show logs')}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleCopyLogs}
              leading={copied ? <Check size={12} /> : <Clipboard size={12} />}
            >
              {copied ? t('bootstrap.copied', 'Copied!') : t('bootstrap.copy', 'Copy')}
            </Button>
          </div>
          {logsOpen && (
            <pre
              className="m-0 max-h-[220px] overflow-y-auto whitespace-pre-wrap break-words rounded-md bg-black/30 px-3 py-2 font-mono text-[0.64rem] leading-relaxed text-fg-muted"
              ref={logRef}
            >
              {logs.length === 0
                ? t('bootstrap.waiting_output', 'Waiting for output…')
                : logs.map((l) => `[${l.stage}] ${l.line}`).join('\n')}
            </pre>
          )}
        </section>
      </div>
    </div>
  );
}

/**
 * Hook: polls the Rust `bootstrap_status` command every pollMs ms. Returns
 * the current stage (string) + message. In a non-Tauri context (dev web),
 * returns 'ready' immediately so the splash never mounts.
 */
export function useBootstrapStage(pollMs = 1000) {
  // `attempt` is Rust's bootstrap-attempt id (#1900). 0 means "none reported",
  // which is where the non-Tauri and dev-web short-circuits below settle.
  const [state, setState] = useState({ stage: 'checking', message: null, attempt: 0 });

  useEffect(() => {
    if (typeof window === 'undefined') {
      setState({ stage: 'ready', message: null, attempt: 0 });
      return;
    }
    if (!('__TAURI_INTERNALS__' in window)) {
      setState({ stage: 'ready', message: null, attempt: 0 });
      return;
    }
    if (import.meta.env.DEV) {
      setState({ stage: 'ready', message: null, attempt: 0 });
      return;
    }

    let cancelled = false;
    let timer = null;
    let misses = 0;
    // #1156: `failed` is terminal for the IPC poll loop, and by then the
    // successful IPC reply has disarmed the #879 watchdog — so nothing was
    // left to notice the backend coming back (supervisor restart, finished
    // repair) and the "Setup failed" card trapped the user until a manual
    // reload. On entering `failed`, poll /health over plain HTTP and
    // auto-dismiss to the app the moment the backend answers.
    let failedRecovery = null;
    const startFailedRecovery = () => {
      if (failedRecovery || cancelled) return;
      failedRecovery = startHealthRecoveryPoll({
        healthUrl: `${getApiBase()}/health`,
        onHealthy: () => {
          if (cancelled) return;
          setState({ stage: 'ready', message: null, attempt: 0 });
        },
      });
    };
    // IPC watchdog (#879): the poll loop below rides entirely on Tauri IPC.
    // After an unclean shutdown, a corrupted WebView cache can break BOTH the
    // IPC custom protocol and its postMessage fallback — `invoke()` then hangs
    // without ever resolving OR rejecting, so neither the stall watchdog
    // (#474) nor the miss counter below can fire, and the splash would spin
    // forever even with a healthy backend. This watchdog is IPC-independent:
    // if no `bootstrap_status` response arrives at all, it polls /health over
    // plain HTTP and either proceeds to the app ('ready') or flips to the
    // 'ipc_lost' recovery panel. Started synchronously, before the dynamic
    // import — in a corrupted-webview world even that import may stall.
    let httpForcedReady = false;
    const watchdog = startSplashWatchdog({
      healthUrl: `${getApiBase()}/health`,
      onReadyViaHttp: () => {
        if (cancelled) return;
        httpForcedReady = true;
        setState({ stage: 'ready', message: null, attempt: 0 });
      },
      onStuck: () => {
        if (cancelled || httpForcedReady) return;
        setState({ stage: 'ipc_lost', message: null, attempt: 0 });
      },
    });
    // Stall watchdog (#474): if the backend hangs in a non-terminal stage and
    // never reports `ready` (e.g. a failed Python-backend spawn on a from-source
    // build), the poll loop would otherwise spin forever and trap the user on a
    // buttonless splash. Track when the (stage,message) last changed; if it
    // stays put past the stage's budget, flip to `failed` so the existing
    // hints + Retry + logs surface instead of an info-less infinite spinner.
    // installing_deps legitimately runs 5–10 min, so it gets a long leash; any
    // (stage,message) change resets the clock so a live install never trips it.
    let lastChangeTs = Date.now();
    let lastKey = '';
    // `awaiting_setup` is gated on a HUMAN, not on work. Rust parks there
    // deliberately ("nothing downloads or installs in this stage —
    // complete_setup is the only way out of it") and waits for the install
    // plan: mode, storage locations, region, mirrors. That is a screen built
    // for deliberation, and a person reading it routinely takes longer than
    // any machine stage — so a stall budget there fires on a perfectly healthy
    // first run, replaces the setup screen with "Setup failed", and stops the
    // IPC poll. Retry re-enters the bootstrap, which parks at awaiting_setup
    // again, and it fails again on the same clock: an unescapable loop on the
    // very first thing a new user sees (#1376). No budget for a stage only a
    // person can leave.
    const stallBudgetMs = (stage) => {
      if (stage === 'awaiting_setup') return Infinity;
      if (stage === 'installing_deps') return 20 * 60 * 1000;
      // Rust owns the backend launch and waits up to five minutes so slow
      // torch/CUDA imports can finish. Keep the splash alive beyond that
      // window; otherwise it reports a false failure at two minutes while
      // the supervised backend is still healthy and making progress (#1749).
      return stage === 'starting_backend' ? 6 * 60 * 1000 : 120 * 1000;
    };
    const invoke = async () => {
      try {
        const { invoke: tauriInvoke } = await import('@tauri-apps/api/core');
        return tauriInvoke;
      } catch {
        return null;
      }
    };
    (async () => {
      const tauriInvoke = await invoke();
      if (!tauriInvoke) {
        watchdog.cancel();
        setState({ stage: 'ready', message: null, attempt: 0 });
        return;
      }
      const tick = async () => {
        if (cancelled) return;
        try {
          const res = await tauriInvoke('bootstrap_status');
          if (cancelled) return;
          // IPC answered — the normal path owns the transition; disarm the
          // HTTP watchdog for good (#879). But if the watchdog already
          // force-transitioned to the app via HTTP health, a late-thawing
          // IPC response must not yank the user back to the splash.
          watchdog.markIpcAlive();
          if (httpForcedReady) return;
          misses = 0;
          const stage = res.stage || 'ready';
          const message = res.message || null;
          // Rust's bootstrap-attempt id (#1900). A build that predates the
          // field reports nothing, which reads as 0 — never a live attempt.
          const attempt = typeof res.attempt === 'number' ? res.attempt : 0;
          // Reset the stall clock whenever something actually changes.
          const key = `${stage}|${message || ''}`;
          if (key !== lastKey) {
            lastKey = key;
            lastChangeTs = Date.now();
          }
          // Rust returns { stage: 'ready' } or { stage: 'failed', message: '…' } etc.
          if (stage !== 'ready' && stage !== 'failed') {
            const lastActivity = Math.max(lastChangeTs, lastBootstrapLogTs);
            if (Date.now() - lastActivity > stallBudgetMs(stage)) {
              // Stuck — surface it as a failure so Retry/logs/hints appear.
              setState({
                attempt,
                stage: 'failed',
                message:
                  (message ? message + '\n\n' : '') +
                  `Setup seems stuck at "${stage}" — the backend never reported ready. ` +
                  `Check the log below, then Retry. If you're running from source, make sure ` +
                  `\`uv sync\` completed and uv/Python are on your PATH.`,
              });
              startFailedRecovery();
              return; // stop IPC polling — only /health recovery remains
            }
            setState({ attempt, stage, message });
            timer = setTimeout(tick, pollMs);
          } else {
            setState({ attempt, stage, message });
            if (stage === 'failed') startFailedRecovery();
          }
        } catch {
          // A transient IPC hiccup (e.g. the very first poll racing webview
          // init) must NOT permanently declare 'ready' — that kills the poll
          // loop and silently skips the awaiting_setup / progress screens.
          // Retry a few times before conceding.
          misses += 1;
          if (cancelled) return;
          if (misses < 5) {
            timer = setTimeout(tick, pollMs);
          } else {
            // Conceding 'ready' after repeated fast rejections — stop the
            // HTTP watchdog too, so it can't flip to 'ipc_lost' underneath
            // the already-mounted main UI (#879).
            watchdog.cancel();
            setState({ stage: 'ready', message: null, attempt: 0 });
          }
        }
      };
      tick();
    })();
    return () => {
      cancelled = true;
      watchdog.cancel();
      if (failedRecovery) failedRecovery.cancel();
      if (timer) clearTimeout(timer);
    };
  }, [pollMs]);

  return state;
}
