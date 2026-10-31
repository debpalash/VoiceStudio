/**
 * backendOutput — read the backend's own words about why it failed.
 *
 * The desktop shell owns the backend process, so the shell is the only place
 * that can say WHY a launch died instead of repeating the last line it saw.
 * Two readings matter, and both were missing:
 *
 * 1. The backend prints its startup verdict on stderr:
 *    `FATAL: backend startup failed during 'ml_imports': <reason>`.
 *    The access log that uvicorn writes to stdout can be the LAST line of a
 *    failed launch (every /health poll during startup prints one), so quoting
 *    "the last line" reported `503 Service Unavailable` to a user whose real
 *    problem was a native extension that would not load (#3058). The verdict
 *    is on stderr and is the line that names the cause.
 *
 * 2. A backend that died while IMPORTING its own dependencies is a broken
 *    Python environment, not a crash to retry. The traceback is the only
 *    evidence, and it was sitting unread in the captured output.
 *
 * This module imports nothing, so the Electron main process and the renderer
 * can both use it without pulling renderer-only code into the main bundle.
 */

/** The backend's startup verdict, as printed by backend/main.py. */
export interface BackendStartupFailure {
  /** The startup phase that failed (`ml_imports`, `api_routes`, ...). */
  step: string;
  /** The exception the phase raised, verbatim. */
  reason: string;
}

const STARTUP_FAILURE = /^FATAL: backend startup failed during '([^']*)':\s*(.*)$/;

/**
 * The backend's own startup verdict, when it printed one.
 *
 * The LAST matching line wins: a backend that failed, was restarted and
 * failed again reports the reason it died most recently.
 */
export function backendStartupFailure(lines: readonly string[]): BackendStartupFailure | undefined {
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const match = STARTUP_FAILURE.exec((lines[index] ?? '').trim());
    if (match) return { step: match[1] ?? '', reason: (match[2] ?? '').trim() };
  }
  return undefined;
}

/**
 * Shapes that mean "a Python module or a native extension would not load".
 *
 * `could not find module` is ctypes' wording when a DLL cannot be loaded
 * because the file itself or one of its dependencies is missing — on Windows
 * that is `FileNotFoundError: Could not find module '…libtorchaudio.pyd' (or
 * one of its dependencies)`, the failure of #3058. `the specified module could
 * not be found` is PyTorch's own loader reporting the same thing for the
 * libraries it loads itself (WinError 126).
 */
export const IMPORT_FAILURE_MARKERS = [
  'modulenotfounderror',
  'importerror',
  'dll load failed',
  'undefined symbol',
  'could not find module',
  'the specified module could not be found',
] as const;

/** Packages the backend cannot run without. */
export const CORE_DEPENDENCIES = [
  'torch',
  'torchaudio',
  'torchvision',
  'transformers',
  'soundfile',
  'numpy',
] as const;

/**
 * How much of the captured output is treated as "this run".
 *
 * The captured tail can hold a previous run's output above the current one,
 * so an import traceback stranded up there must not diagnose a crash that
 * happened after it. Restricting the match to the most recent lines means a
 * stale one only counts when the current run wrote almost nothing — which is
 * itself the startup death this classifies.
 */
export const RECENT_OUTPUT_LINES = 20;

/**
 * True when the output shows the backend dying while importing its own
 * dependencies — a broken/incomplete Python environment, not a workload.
 *
 * Deliberately narrow: an ImportError *plus* one of the packages the app
 * cannot run without. A model file that fails to import mid-request is a
 * different animal, and a generic "ImportError" match would swallow it.
 */
export function isBrokenEnvironmentOutput(lines: readonly string[]): boolean {
  const text = lines.join('\n').trim();
  if (!text) return false;
  // A file Windows refused to load is intact on disk: reinstalling the
  // runtime downloads gigabytes and is blocked again (#3209).
  if (windowsLoadBlock(lines)) return false;
  const tail = text
    .split('\n')
    .slice(-RECENT_OUTPUT_LINES)
    .join('\n')
    .toLowerCase();
  if (!IMPORT_FAILURE_MARKERS.some((marker) => tail.includes(marker))) return false;
  return CORE_DEPENDENCIES.some((dependency) => tail.includes(dependency));
}

/** Who refused to load a runtime file, by the Windows error text Python quotes. */
export type WindowsLoadBlock = 'application_control' | 'group_policy' | 'antivirus';

/**
 * Windows refused to load one of the runtime's files (#3209, #3224).
 *
 * Python quotes the Windows error verbatim: `ImportError: DLL load failed
 * while importing _sqlite3: An Application Control policy has blocked this
 * file.` (ERROR 4551, Smart App Control or an organization's App Control /
 * AppLocker policy), `This program is blocked by group policy` (ERROR 1260)
 * and `…contains a virus or potentially unwanted software` (ERROR 225, an
 * antivirus blocking the load). None is a damaged environment.
 */
export function windowsLoadBlock(lines: readonly string[]): WindowsLoadBlock | null {
  const tail = lines.slice(-RECENT_OUTPUT_LINES).join('\n');
  if (/an application control policy has blocked this file/i.test(tail)) return 'application_control';
  // ERROR 1260 is AppLocker / Software Restriction Policies, never Smart App Control.
  if (/this program is blocked by group policy/i.test(tail)) return 'group_policy';
  if (/contains a virus or potentially unwanted software/i.test(tail)) return 'antivirus';
  return null;
}

/**
 * The evidence quoted when a managed backend stayed silent until the startup
 * budget ran out: the tail of the backend's own log file, or an explicit note
 * that the log could not be read. `path` and the `tail` lines are expected to
 * be scrubbed and bounded already (backend.ts does that when it reads the log);
 * this only formats them, so the module keeps importing nothing and stays safe
 * for the renderer bundle.
 */
export function backendLogEvidence(path: string, tail: readonly string[]): string {
  const log = `The backend's own log (${path})`;
  return tail.length > 0
    ? `It printed no output. ${log} ends with:\n${tail.join('\n')}`
    : `It printed no output. ${log} is missing, empty, or unreadable.`;
}
