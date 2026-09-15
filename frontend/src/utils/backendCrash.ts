import { abortableDelay } from './abortableDelay.ts';
import { deploymentMode, type DeploymentMode } from './deploymentMode.ts';
/**
 * backendCrash — frontend bridge to the desktop shell's crash forensics
 * (#941, src-tauri/src/crash.rs).
 *
 * When the backend PROCESS dies (native CUDA abort, OOM kill, DLL crash) the
 * Rust death watcher persists a crash marker (exit code/signal + stderr tail).
 * This module reads it so:
 *   - api/client.ts can replace the vague "Can't reach the local backend"
 *     with the honest story,
 *   - components/BackendCrashNotice.jsx can offer "View crash details",
 *   - utils/bugReport.js can attach the evidence to the GitHub-issue prefill.
 *
 * Outside the Tauri shell (browser dev, Docker, LAN share) the getters fall
 * back to the BACKEND's own run-sentinel forensics (#1164,
 * backend/core/run_sentinel.py): GET /system/last-run-crash reports a
 * previous run that died without a clean shutdown, adapted here to the same
 * CrashMarker shape — so BackendCrashNotice, the apiFetch crash branch, and
 * the bug-report prefill light up in every deployment, not just desktop.
 */

import i18next from 'i18next';
import {
  CSRF_HEADER_NAME,
  LEGACY_API_KEY_STORAGE_KEY,
  getAdminSession,
  isSameOriginApi,
} from '../api/authSession.ts';

export interface BackendCrashMarker {
  /** Unix seconds when the death was detected. */
  ts: number;
  exit_code: number | null;
  signal: number | null;
  /** Human-readable ExitStatus display ("exit status: 134", …). */
  exit_desc: string;
  backend_version: string;
  /** Seconds the backend had been running when it died. */
  uptime_s: number;
  /** Tail of backend_err.log captured at death time (~40 lines). */
  last_stderr: string;
  /** Whether the user already viewed/dismissed this crash. */
  acknowledged: boolean;
}

function inTauri(): boolean {
  const w = window as unknown as Record<string, unknown> | undefined;
  return typeof window !== 'undefined' && !!(w?.__TAURI__ || w?.__TAURI_INTERNALS__);
}

// ── Browser/Docker fallback: the backend's run-sentinel record (#1164) ─────

/** Shape of GET /system/last-run-crash's `record` (backend/core/run_sentinel.py). */
export interface LastRunCrashRecord {
  detected_at: number;
  started_at: number | null;
  ended_between: [number, number];
  uptime_hint_s: number | null;
  version: string;
  last_activity: { ts: number | null; kind: string; detail: string | null } | null;
  log_tail: string[];
}

/** Adapt a run-sentinel record to the CrashMarker shape the whole crash UI
 * already speaks. A sentinel can't know an exit code (the process died out
 * from under it), so exit_code/signal are null and exit_desc carries the
 * story; describeCrashExit() falls through to exit_desc for exactly this
 * shape. Exported for unit tests. */
export function _adaptLastRunCrash(
  record: LastRunCrashRecord,
  acknowledged: boolean,
): BackendCrashMarker {
  const activity = record.last_activity;
  const activityLine = activity?.kind
    ? [
        `last activity before the death: ${activity.kind}${activity.detail ? ` (${activity.detail})` : ''}`,
        '',
      ]
    : [];
  return {
    ts: Math.round(record.detected_at || 0),
    exit_code: null,
    signal: null,
    exit_desc: 'process ended uncleanly (previous run)',
    backend_version: record.version || '',
    uptime_s: Math.max(0, Math.round(record.uptime_hint_s ?? 0)),
    last_stderr: [...activityLine, ...(Array.isArray(record.log_tail) ? record.log_tail : [])]
      .join('\n')
      .trim(),
    acknowledged,
  };
}

/** True when this marker is the run SENTINEL — startup noticing the previous
 * run never cleared its marker — rather than an observed process death. The
 * sentinel genuinely does not know whether the previous run crashed: the
 * machine sleeping, a force-quit, a stopped WSL VM or a Docker restart all
 * leave the same trace, and those benign causes are the majority case
 * (#1375). */
export function isSentinelMarker(marker: BackendCrashMarker): boolean {
  return (
    marker.exit_code === null &&
    marker.signal === null &&
    marker.exit_desc === 'process ended uncleanly (previous run)'
  );
}

/** True when the marker carries something a maintainer could act on: a log
 * tail, or at least a concrete exit code/signal. A sentinel with neither
 * produces a bug report whose evidence block is EMPTY — the reporter files it
 * in good faith, nobody can answer it, and it sits open (#1375). The
 * one-click report is gated on this; the notice itself is not. */
export function hasCrashEvidence(marker: BackendCrashMarker): boolean {
  if ((marker.last_stderr || '').trim()) return true;
  return marker.exit_code !== null || marker.signal !== null;
}

const HTTP_FALLBACK_TIMEOUT_MS = 2500;

/** Auth context a non-desktop deployment may need (LAN-share PIN, ephemeral
 * admin session) — mirrors apiFetch's injection. We deliberately do NOT call apiFetch:
 * its give-up path calls back into this module, and its retry cascade would
 * stall the very error message this fallback exists to enrich. */
function _fallbackHeaders(apiBase: string): Record<string, string> {
  const headers: Record<string, string> = {};
  try {
    const pin = sessionStorage.getItem('ov_pin');
    if (pin) headers['X-OmniVoice-Pin'] = pin;
  } catch {
    /* noop */
  }
  try {
    localStorage.removeItem(LEGACY_API_KEY_STORAGE_KEY);
  } catch {
    /* noop */
  }
  const session = getAdminSession(apiBase);
  if (session) headers['Authorization'] = `Bearer ${session.token}`;
  if (isSameOriginApi(apiBase)) headers[CSRF_HEADER_NAME] = '1';
  return headers;
}

/** Best-effort fetch of the backend's own crash record. Fast timeout, every
 * error swallowed to null — when the backend is DOWN this fails instantly
 * and the caller's mode-aware message stands; the record becomes fetchable
 * once the backend is back (next dev restart / Docker restart policy). */
async function fetchLastRunCrash(): Promise<BackendCrashMarker | null> {
  try {
    // Dynamic import: api/client.ts statically imports this module, so a
    // static import back would be a cycle. apiUrl is only needed at call time.
    const { API, apiUrl } = await import('../api/client.ts');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), HTTP_FALLBACK_TIMEOUT_MS);
    try {
      const res = await fetch(apiUrl('/system/last-run-crash'), {
        signal: controller.signal,
        headers: _fallbackHeaders(API),
        credentials: 'include',
      });
      if (!res.ok) return null;
      const body = (await res.json()) as {
        record: LastRunCrashRecord | null;
        acknowledged: boolean;
      } | null;
      if (!body?.record) return null;
      return _adaptLastRunCrash(body.record, !!body.acknowledged);
    } finally {
      clearTimeout(timer);
    }
  } catch {
    return null;
  }
}

/** Newest crash marker: the shell's (desktop) or the backend run-sentinel's
 * (browser/dev/Docker), or null when nothing ever crashed / nothing answers. */
export async function getLastBackendCrash(): Promise<BackendCrashMarker | null> {
  if (!inTauri()) return fetchLastRunCrash();
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    return ((await invoke('get_last_backend_crash')) as BackendCrashMarker | null) ?? null;
  } catch {
    return null;
  }
}

/** Newest crash marker only if the user hasn't acknowledged it yet. */
export async function getUnacknowledgedBackendCrash(): Promise<BackendCrashMarker | null> {
  const marker = await getLastBackendCrash();
  return marker && !marker.acknowledged ? marker : null;
}

/** Wait, briefly, for the shell to record a crash marker.
 *
 * #1119: the desktop shell learns the backend died from a ~2 s POLL — it has
 * to notice the child exit and write the marker. Asking for that marker ONCE,
 * at the instant a request fails, races that poll and loses: we find nothing
 * and conclude "no crash". That conclusion then gets stated as fact to the
 * user, which is how #1802/#1805 came to read "it most likely crashed or was
 * killed mid-request" on a backend that had left no evidence of dying at all.
 *
 * Outside the Tauri shell there is no death watcher to wait for — the
 * run-sentinel record only appears after the backend RESTARTS — so one
 * immediate ask is all the information there is, and a browser/Docker user is
 * never stalled to learn nothing more.
 *
 * `getCrash` and `sleep` are injectable seams so the branch logic is
 * unit-testable without a shell or real time.
 */
export async function awaitBackendCrashMarker(
  getCrash: () => Promise<BackendCrashMarker | null> = getUnacknowledgedBackendCrash,
  opts: {
    waitMs?: number;
    intervalMs?: number;
    sleep?: (ms: number) => Promise<void>;
    signal?: AbortSignal | null;
  } = {},
): Promise<{ crash: BackendCrashMarker | null; unavailable: boolean }> {
  const waitMs = opts.waitMs ?? 8_000;
  const intervalMs = opts.intervalMs ?? 1_000;
  const sleep = opts.sleep ?? ((ms: number) => abortableDelay(ms, opts.signal));
  const deadline = Date.now() + waitMs;
  for (;;) {
    opts.signal?.throwIfAborted();
    let crash: BackendCrashMarker | null = null;
    try {
      crash = await getCrash();
    } catch {
      opts.signal?.throwIfAborted();
      // Forensics unavailable — "no marker" would be a lie, so say so and let
      // the caller keep its own wording rather than assert anything.
      return { crash: null, unavailable: true };
    }
    opts.signal?.throwIfAborted();
    if (crash) return { crash, unavailable: false };
    if (!inTauri()) return { crash: null, unavailable: false };
    if (Date.now() >= deadline) return { crash: null, unavailable: false };
    await sleep(intervalMs);
  }
}

/** Mark the newest crash as seen (the marker itself is retained for reports). */
export async function acknowledgeBackendCrash(): Promise<void> {
  if (!inTauri()) {
    // Browser/dev/Docker: watermark the backend's run-sentinel record.
    try {
      const { API, apiUrl } = await import('../api/client.ts');
      await fetch(apiUrl('/system/last-run-crash/ack'), {
        method: 'POST',
        headers: _fallbackHeaders(API),
        credentials: 'include',
      });
    } catch {
      /* backend unreachable — the notice will simply resurface, which is honest */
    }
    return;
  }
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('acknowledge_backend_crash');
  } catch {
    /* shell unavailable — nothing to acknowledge */
  }
}

/** "exit code 3221226505" / "signal 6" / the raw ExitStatus display. */
export function describeCrashExit(
  marker: Pick<BackendCrashMarker, 'exit_code' | 'signal' | 'exit_desc'>,
): string {
  if (marker.exit_code != null) return `exit code ${marker.exit_code}`;
  if (marker.signal != null) return `signal ${marker.signal}`;
  return marker.exit_desc || 'unknown exit';
}

/**
 * Likely-cause line for the crash message, branched on HOW the process died.
 *
 * SIGKILL (signal 9) with no stderr is the operating system's memory killer —
 * on a unified-memory Mac that means system RAM, and the old one-size message
 * blamed "VRAM" on machines that have none (audit finding: OS-OOM kills were
 * misattributed). Everything else keeps the small-GPU VRAM guidance, which is
 * the dominant cause for real GPU aborts.
 */
/**
 * True when the process died of a NATIVE fault — a segfault/illegal
 * instruction/abort — rather than memory pressure or an orderly exit.
 *
 * POSIX reports these as signals. Windows has no signals here: the shell sees
 * the raw NTSTATUS as a (negative, when read as i32) exit code, so the codes
 * have to be matched explicitly. 0xC0000005 is the access violation behind
 * #1275; the others are the same family and would otherwise be read as an
 * ordinary non-zero exit.
 */
const NT_FAULT_EXIT_CODES = new Set([
  -1073741819, // 0xC0000005 STATUS_ACCESS_VIOLATION
  -1073741795, // 0xC000001D STATUS_ILLEGAL_INSTRUCTION
  -1073741674, // 0xC0000096 STATUS_PRIVILEGED_INSTRUCTION
  -1073740791, // 0xC0000409 STATUS_STACK_BUFFER_OVERRUN
  -1073741571, // 0xC00000FD STATUS_STACK_OVERFLOW
]);

// Deliberately only SIGILL (4) and SIGSEGV (11) — the two whose numbers are
// identical on every POSIX platform and whose meaning is unambiguous.
//
// SIGABRT (6) is NOT here on purpose: abort() is how a fatal CUDA error exits,
// including "CUDA error: out of memory" raised asynchronously, so it keeps the
// VRAM guidance. SIGBUS is excluded because its number is platform-dependent
// (7 on Linux, 10 on macOS, where 10 is SIGUSR1 on Linux) and guessing wrong
// would misfile an ordinary signal as a hardware fault.
// SIGKILL (9) is handled above — that is the OS memory killer, not a fault.
const NATIVE_FAULT_SIGNALS = new Set([4, 11]);

export function isNativeFault(marker: Pick<BackendCrashMarker, 'exit_code' | 'signal'>): boolean {
  if (marker.signal != null && NATIVE_FAULT_SIGNALS.has(marker.signal)) return true;
  return marker.exit_code != null && NT_FAULT_EXIT_CODES.has(marker.exit_code);
}

/**
 * True when the captured stderr shows the backend dying while IMPORTING its
 * own dependencies — a broken/incomplete Python environment, not a workload.
 *
 * Reported repeatedly (#1282: `import torchaudio` → `from torch.hub import …`
 * exploding 4 s after launch; #1376: transformers' lazy module raising
 * `ModuleNotFoundError: Could not import module 'GenerationMixin'` 28 s in).
 * Both fell through to the VRAM default, which told users whose venv was
 * half-installed to go flush a TTS model. Nothing about the exit code
 * distinguishes these — the traceback is the only evidence, and it was sitting
 * unread in the marker.
 *
 * Deliberately narrow: an ImportError *plus* one of the packages the app
 * cannot run without. A model file that fails to import mid-request is a
 * different animal, and a generic "ImportError" match would swallow it.
 */
const _IMPORT_FAILURE_MARKERS = [
  'modulenotfounderror',
  'importerror',
  'dll load failed',
  'undefined symbol',
];
const _CORE_DEPENDENCIES = [
  'torch',
  'torchaudio',
  'torchvision',
  'transformers',
  'soundfile',
  'numpy',
];

// How much of the captured tail is treated as "this run". backend_err.log is
// appended across runs and the shell captures its last ~40 lines, so a process
// that died before writing 40 lines of its own carries the PREVIOUS run's
// output above its own (greptile). An import traceback stranded up there must
// not diagnose the crash that happened after it — restricting the match to the
// most recent lines means a stale one only counts when the current run wrote
// almost nothing, which is itself the startup death this classifies.
const _RECENT_TAIL_LINES = 20;

export function isBrokenEnvironmentCrash(
  marker: Partial<Pick<BackendCrashMarker, 'last_stderr'>>,
): boolean {
  const raw = (marker.last_stderr || '').trim();
  if (!raw) return false;
  const tail = raw.split('\n').slice(-_RECENT_TAIL_LINES).join('\n').toLowerCase();
  if (!_IMPORT_FAILURE_MARKERS.some((m) => tail.includes(m))) return false;
  return _CORE_DEPENDENCIES.some((d) => tail.includes(d));
}

export function crashCauseHint(
  marker: Pick<BackendCrashMarker, 'exit_code' | 'signal'> &
    Partial<Pick<BackendCrashMarker, 'last_stderr'>>,
): string {
  // #1223: the backend exits 78 (EX_CONFIG) when it could not bind its port.
  // That is not a crash and has nothing to do with memory — the old message
  // sent a user whose real problem was a leftover process off to shrink their
  // ASR model. Keep in sync with _EXIT_PORT_IN_USE in backend/main.py.
  if (marker.exit_code === 78) {
    return i18next.t('errors.crash_port_in_use', {
      defaultValue:
        'The backend could not start because port 3900 is already in use — another copy of ' +
        'VoiceStudio (or an app that claimed that port) is holding it. Quit the other instance ' +
        'and relaunch; if nothing is visibly running, an orphaned backend from a previous ' +
        'session is still holding the port.',
    });
  }
  if (marker.signal === 9) {
    return i18next.t('errors.crash_oom_kill', {
      defaultValue:
        'It was force-killed (signal 9), which usually means the operating system ran out of ' +
        'memory (RAM) and stopped it. Close memory-heavy apps, pick a smaller ASR model in ' +
        'the engine Weights list in Model Catalogue, or flush the TTS model before transcribing.',
    });
  }
  // Ordered deliberately, between the two explicit-fact branches.
  //
  // AFTER signal 9: an OOM kill is an unambiguous fact about THIS process, and
  // a failed import never produces one — so a stale traceback in the captured
  // tail must never outrank it (greptile).
  //
  // BEFORE the native-fault branch: a dependency that will not load takes the
  // process down as an access violation on Windows (a missing dependent DLL),
  // where "update your GPU driver" is just as wrong as the VRAM advice.
  if (isBrokenEnvironmentCrash(marker)) {
    return i18next.t('errors.crash_broken_env', {
      defaultValue:
        'It died while loading its own Python dependencies, so this is not about memory or ' +
        'your GPU — the environment is incomplete or was left half-updated. Use "Clean & Retry" ' +
        'in Settings → Logs → Backend, which rebuilds it from scratch; that repairs it in ' +
        'place, without touching your voices or projects. If it still fails afterwards, the ' +
        'crash details name the exact package that would not import.',
    });
  }
  // A native crash inside the compute stack — the process was executing bad
  // machine code, not slowly exhausting memory. #1275 (Windows 0xC0000005 on an
  // RTX 2080 SUPER) and #1293 (SIGSEGV on Linux) both landed on the VRAM advice
  // below, which sends the user to flush a model that had nothing to do with it.
  // The real causes are a GPU driver that disagrees with the bundled CUDA
  // runtime, or a truncated/corrupt weight file being memory-mapped.
  if (isNativeFault(marker)) {
    // The crash marker records HOW the process died, not which subsystem was
    // running — a segfault during transcription looks identical to one during
    // synthesis. Naming only the TTS escape hatch sent ASR crashes to a fix
    // that leaves the crashing path untouched (Greptile P1), so both isolated
    // engines are offered and the user picks the one they were using.
    return i18next.t('errors.crash_native_fault', {
      defaultValue:
        'It crashed inside the compute stack rather than running out of memory — that points ' +
        'at a GPU driver that does not match the bundled CUDA runtime, or a model file that ' +
        'downloaded incompletely. Update your GPU driver, then re-download the model from ' +
        'the engine Weights list in Model Catalogue (it repairs a partial download in place). If it keeps happening, ' +
        'switch to a crash-isolated engine in Model Catalogue — "VoiceStudio (subprocess)" ' +
        'for synthesis, "Faster-Whisper (crash-isolated subprocess)" for transcription. Those ' +
        'run the model in a separate process, so a crash like this takes down that process ' +
        'instead of the whole backend.',
    });
  }
  return i18next.t('errors.crash_vram_default', {
    defaultValue:
      'On smaller GPUs the usual cause is running out of VRAM while loading the ASR model on ' +
      'top of the TTS model: flush the TTS model first, or pick a smaller ASR model in ' +
      'the engine Weights list in Model Catalogue.',
  });
}

/** Coarse "12 s" / "3 min" / "2 h" age of a marker, for the honest message. */
export function crashAge(marker: Pick<BackendCrashMarker, 'ts'>, nowMs = Date.now()): string {
  const s = Math.max(0, Math.round(nowMs / 1000 - marker.ts));
  if (s < 90) return `${s} s`;
  const min = Math.round(s / 60);
  if (min < 90) return `${min} min`;
  return `${Math.round(min / 60)} h`;
}

/**
 * The honest error for an SSE/stream that died with NO terminal event (#1062).
 *
 * Every long-running stream the backend serves is contract-bound to emit a
 * terminal event before it closes — even on failure (tests/test_dub_transcribe.py
 * ::test_transcribe_stream_never_closes_without_terminal_event). So a stream that
 * simply goes silent did NOT "probably fail to load a model": the backend
 * PROCESS went away underneath it. On smaller GPUs the usual trigger is running
 * out of VRAM while loading the ASR model on top of a resident TTS model, which
 * aborts the process natively rather than raising a catchable Python error.
 *
 * When the desktop shell recorded a crash marker (#941), say what actually
 * happened — exit code, how long ago, and the VRAM next step — and raise the
 * crash notice so "View crash details" is one click away. With no marker (or
 * outside the Tauri shell) the caller's own message stands.
 *
 * `getCrash` is an injectable seam (same idea as services/endpoint_race's
 * injectable probers) so the branch logic is unit-testable without a shell.
 */
/** Is the backend answering right now? Short timeout — this runs on a failure
 *  path and must not add a visible stall. Any error means "cannot tell". */
async function _probeBackendAlive(): Promise<boolean> {
  try {
    const { API, apiUrl } = await import('../api/client.ts');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2000);
    try {
      const res = await fetch(apiUrl('/system/info'), {
        signal: controller.signal,
        headers: _fallbackHeaders(API),
        credentials: 'include',
      });
      return res.ok;
    } finally {
      clearTimeout(timer);
    }
  } catch {
    return false;
  }
}

export async function streamDropError(
  fallbackMessage: string,
  getCrash: () => Promise<BackendCrashMarker | null> = getUnacknowledgedBackendCrash,
  opts: {
    waitMs?: number;
    intervalMs?: number;
    sleep?: (ms: number) => Promise<void>;
    probeAlive?: () => Promise<boolean>;
    mode?: DeploymentMode;
  } = {},
): Promise<Error> {
  // #1119: the shell learns the backend died from a ~2 s POLL — it must notice
  // the child exit and write the crash marker. Asking for that marker ONCE, at
  // the instant the stream drops, races that poll and loses: we found nothing
  // and fell back to the guess ("Likely ASR backend failed to load") even when
  // the backend had in fact just died. That's the same race #1102 fixed for
  // apiFetch, which this path never got. Give the shell time to catch up before
  // believing there was no crash.
  const { crash, unavailable } = await awaitBackendCrashMarker(getCrash, opts);
  if (unavailable) return new Error(fallbackMessage); // don't mask the caller
  if (!crash) {
    // No crash marker — but "no marker" is not "the backend died and we missed
    // it". Outside the Tauri shell there is no death watcher at all, so this
    // branch is where every browser/Docker user lands, and the caller's
    // fallback used to assert a cause on their behalf ("Likely ASR backend
    // failed to load"). #1242 reported exactly that, in `server` mode, with the
    // backend having answered 20 s earlier — so nothing had crashed and nothing
    // had failed to load.
    //
    // Ask instead of assuming. If the backend is still answering, the process
    // did not go away, which rules the caller's guess out: in a served
    // deployment a stream that dies while the server is healthy is
    // characteristically a reverse proxy or load balancer buffering or timing
    // out the SSE connection.
    const probeAlive = opts.probeAlive ?? _probeBackendAlive;
    if (await probeAlive()) {
      // #2108: the proxy diagnosis below is only possible where a proxy can
      // exist. The desktop webview and `bun run dev` talk to 127.0.0.1
      // directly; a drop there is the local connection dying under a step
      // that went byte-silent for minutes — and the backend kept going (the
      // reporter's transcript was saved 20 min after the UI gave up). Say
      // that, and where to look, instead of handing out nginx advice.
      if ((opts.mode ?? deploymentMode()) !== 'server') {
        return new Error(
          i18next.t('errors.stream_cut_backend_alive_local', {
            defaultValue:
              'The stream ended early, but the backend is still running — so it did not crash. ' +
              'The app lost its connection to the backend mid-job; that happens when a long step ' +
              'goes quiet for minutes, and the backend usually finishes the job anyway. Give it a ' +
              'few minutes and reopen the job from its history, or check the backend log ' +
              '(Settings → Logs → Backend) for what it was doing.',
          }),
        );
      }
      return new Error(
        i18next.t('errors.stream_cut_backend_alive', {
          defaultValue:
            'The stream ended early, but the backend is still running — so it did not crash. ' +
            'In a served or containerised setup this is usually a reverse proxy or load balancer ' +
            'buffering or timing out the connection: disable response buffering for this route ' +
            '(nginx: proxy_buffering off; X-Accel-Buffering: no) and raise its read timeout. ' +
            'Running the desktop app directly, or on localhost without a proxy, will confirm it.',
        }),
      );
    }
    return new Error(fallbackMessage);
  }
  try {
    window.dispatchEvent(new CustomEvent('ov:backend-crashed', { detail: crash }));
  } catch {
    /* no window (tests) — the Error below still tells the story */
  }
  return new Error(
    `The local VoiceStudio backend crashed (${describeCrashExit(crash)}) ${crashAge(crash)} ago, ` +
      'which dropped this stream — it is being restarted automatically. Open the crash notice for ' +
      `the error output. ${crashCauseHint(crash)}`,
  );
}
