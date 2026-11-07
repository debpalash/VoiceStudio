/**
 * One classifier for install and startup failures.
 *
 * Reporters rarely come back with more detail, so a failure has to identify
 * itself: a stable code, the fix for that code, and the facts a maintainer
 * needs. A code is only given with evidence for it; `UNCLASSIFIED` is a valid
 * answer and the raw message is always kept beside it.
 *
 * Adding a code: add it here, give it guidance, add the matching
 * `### CODE` section to docs/install/troubleshooting.md and the mirror entries
 * in backend/core/error_docs_map.py and errorDocsMap.ts, then add a real report
 * to installFailure.corpus.test.ts. installFailure.test.ts fails on any gap.
 */

import { windowsLoadBlock, withoutBackendLogEvidence } from './backendOutput';
import { isNativeFaultExit, normalizeExitCode, normalizeSignal } from './nativeExit';

export const INSTALL_FAILURE_CODES = [
  'WINDOWS_APP_CONTROL_BLOCKED',
  'ANTIVIRUS_BLOCKED',
  'EXEC_REFUSED',
  'GATEKEEPER_QUARANTINE',
  'EXEC_NOEXEC_MOUNT',
  'RUNTIME_DOWNLOAD_STALLED',
  'RUNTIME_DOWNLOAD_INTERRUPTED',
  'DISK_FULL',
  'RUNTIME_FOLDER_DENIED',
  'RUNTIME_FILES_LOCKED',
  'RUNTIME_IN_SYNCED_FOLDER',
  'SYSTEM_LIBRARY_PROBLEM',
  'UNSUPPORTED_PLATFORM',
  'SOURCE_ENV_INCOMPLETE',
  'BROKEN_VENV',
  'NATIVE_ACCESS_VIOLATION',
  'NATIVE_ILLEGAL_INSTRUCTION',
  'NATIVE_CRASH',
  'STARTUP_TIMEOUT_SILENT',
  'STARTUP_TIMEOUT_OUTPUT',
  'BACKEND_STARTUP_FAILED',
  'PORT_IN_USE',
  'ROCM_NOT_CONFIGURED',
  'UNCLASSIFIED',
] as const;

export type InstallFailureCode = (typeof INSTALL_FAILURE_CODES)[number];

export type SmartAppControlState = 'on' | 'evaluation' | 'off' | 'unknown';

/** Everything a failure report states about itself. Every fact may be absent. */
export interface InstallFailure {
  code: InstallFailureCode;
  stage: string;
  setupPhase?: string;
  elapsedSeconds?: number;
  /** Basename only. */
  program?: string;
  programLocation?: 'runtime' | 'bundled' | 'custom' | 'other';
  errorCode?: string;
  exitCode?: number;
  signal?: string;
  smartAppControl?: SmartAppControlState;
  /** Basenames from recent Windows CodeIntegrity block events. */
  blockedFiles?: string[];
  signature?: string;
  antivirus?: string[];
  quarantined?: boolean;
  noexecMount?: boolean;
  downloadHost?: string;
  downloadedBytes?: number;
  stalledSeconds?: number;
  proxyConfigured?: boolean;
  region?: string;
  diskFreeGb?: number;
  pathFlags?: string[];
}

export interface FailureEvidence {
  stage?: string;
  message?: string;
  setupIssue?: string;
  exitCode?: number | null;
  signal?: number | string | null;
  logLines?: readonly string[];
  stalled?: boolean;
  smartAppControl?: SmartAppControlState;
  blockedFiles?: readonly string[];
  quarantined?: boolean;
  noexecMount?: boolean;
}

/** Cloud-sync clients whose placeholders / locks break a Python environment. */
const SYNCED_FOLDER =
  /(^|[\\/])(onedrive(\s+-\s+[^\\/]+)?|dropbox|icloud\s*drive|google\s*drive|nextcloud)([\\/]|$)/i;

/** True when the text names a path inside a cloud-synced folder. */
export function inSyncedFolder(text: string): boolean {
  return SYNCED_FOLDER.test(text);
}

const SPAWN_REFUSED = /\bspawn\s+(UNKNOWN|EPERM|EACCES)\b/;
const COULD_NOT_START = /\bCould not start\b.*\b(UNKNOWN|EPERM|EACCES)\b/s;
const LOAD_REFUSED = /Windows refused to load one of the engine's files/;

/** The OS refused to run the engine, as opposed to the engine running and failing. */
export function launchRefused(message: string | undefined): boolean {
  return Boolean(message && (SPAWN_REFUSED.test(message) || COULD_NOT_START.test(message)));
}

/** The program named by `spawnFailureMessage`, if the message came from there. */
export function refusedProgram(message: string | undefined): string | undefined {
  return message ? /\bCould not start (.+?): spawn\b/s.exec(message)?.[1] : undefined;
}

const SETUP_ISSUE_CODES: Record<string, InstallFailureCode> = {
  space: 'DISK_FULL',
  access: 'RUNTIME_FOLDER_DENIED',
  files_locked: 'RUNTIME_FILES_LOCKED',
  missing_system_library: 'SYSTEM_LIBRARY_PROBLEM',
  outdated_system_library: 'SYSTEM_LIBRARY_PROBLEM',
  unsupported_platform: 'UNSUPPORTED_PLATFORM',
  wrong_architecture: 'UNSUPPORTED_PLATFORM',
};

const DOWNLOAD_INTERRUPTED =
  /peer closed connection|close_notify|error decoding response body|error sending request|failed to download|failed to fetch|connection (?:reset|closed|refused|timed out)|operation timed out|network (?:is )?unreachable|dns error/i;

const ILLEGAL_INSTRUCTION = 0xc000001d;
const ACCESS_VIOLATION = 0xc0000005;

function nativeCode(evidence: FailureEvidence, message: string): InstallFailureCode | null {
  const exitText = /exit code (-?\d+)/.exec(message)?.[1];
  const signalText = /signal (SIG[A-Z]+)/.exec(message)?.[1];
  const exitCode = evidence.exitCode ?? (exitText ? Number(exitText) : null);
  const signal = evidence.signal ?? signalText ?? null;
  if (!isNativeFaultExit({ exitCode, signal })) return null;
  const nt = normalizeExitCode(exitCode);
  const posix = normalizeSignal(signal);
  if (nt === ILLEGAL_INSTRUCTION || posix === 4) return 'NATIVE_ILLEGAL_INSTRUCTION';
  if (nt === ACCESS_VIOLATION || posix === 11) return 'NATIVE_ACCESS_VIOLATION';
  return 'NATIVE_CRASH';
}

/** The most specific code the evidence supports, or `UNCLASSIFIED`. */
export function classifyInstallFailure(evidence: FailureEvidence): InstallFailureCode {
  // Quoted log lines are evidence, not the cause (#3724).
  const message = withoutBackendLogEvidence(evidence.message ?? '');
  const lines = evidence.logLines ?? [];
  if (evidence.stage === 'port_in_use') return 'PORT_IN_USE';
  const issue = evidence.setupIssue ? SETUP_ISSUE_CODES[evidence.setupIssue] : undefined;
  if (issue) return issue;

  // A refused DLL is named by the main process, or quoted by Python in the log.
  const block = windowsLoadBlock(lines);
  if (block === 'antivirus' || (LOAD_REFUSED.test(message) && /Your antivirus/.test(message)))
    return 'ANTIVIRUS_BLOCKED';
  if (block || LOAD_REFUSED.test(message)) return 'WINDOWS_APP_CONTROL_BLOCKED';

  if (launchRefused(message)) {
    if (
      /Smart App Control/.test(message) ||
      evidence.smartAppControl === 'on' ||
      evidence.smartAppControl === 'evaluation' ||
      evidence.blockedFiles?.length
    )
      return 'WINDOWS_APP_CONTROL_BLOCKED';
    if (evidence.quarantined) return 'GATEKEEPER_QUARANTINE';
    if (evidence.noexecMount) return 'EXEC_NOEXEC_MOUNT';
    return 'EXEC_REFUSED';
  }

  if (inSyncedFolder(message)) return 'RUNTIME_IN_SYNCED_FOLDER';
  const native = nativeCode(evidence, message);
  if (native) return native;

  if (/Python environment\b.*\b(is missing|is incomplete|missing or incomplete)\b/is.test(message))
    return /setup:api/.test(message) ? 'SOURCE_ENV_INCOMPLETE' : 'BROKEN_VENV';
  if (/\bENOSPC\b|no space left on device/i.test(message)) return 'DISK_FULL';
  if (/\b(EPERM|EACCES|permission denied|access is denied)\b/i.test(message))
    return 'RUNTIME_FOLDER_DENIED';

  if (/did not answer on port \d+ within \d+ s/.test(message))
    return /printed no output/.test(message) ? 'STARTUP_TIMEOUT_SILENT' : 'STARTUP_TIMEOUT_OUTPUT';
  if (/\bBackend failed to start during\b|FATAL: backend startup/.test(message))
    return 'BACKEND_STARTUP_FAILED';
  if (/\bROCm\b.*\b(not configured|not installed|missing)\b/i.test(message))
    return 'ROCM_NOT_CONFIGURED';

  // Setup only: a dropped connection in a running engine's log is not an install failure.
  if (evidence.stage === 'setup_required' || evidence.stage === 'installing') {
    if (evidence.stalled) return 'RUNTIME_DOWNLOAD_STALLED';
    if (
      DOWNLOAD_INTERRUPTED.test(message) ||
      DOWNLOAD_INTERRUPTED.test(lines.slice(-20).join('\n'))
    )
      return 'RUNTIME_DOWNLOAD_INTERRUPTED';
  }
  return 'UNCLASSIFIED';
}

/** The one named reason a local engine start failed. */
export type BackendCause =
  | 'smart_app_control'
  | 'app_control_policy'
  | 'antivirus'
  | 'spawn_blocked'
  | 'gatekeeper'
  | 'noexec'
  | 'download'
  | 'native_crash'
  | 'illegal_instruction'
  | 'missing_env'
  | 'permission'
  | 'synced_folder'
  | 'slow_start';

export type BackendHintKey =
  | 'backend.hint_spawn_blocked'
  | 'backend.hint_smart_app_control'
  | 'backend.hint_smart_app_control_local'
  | 'backend.hint_app_control_policy'
  | 'backend.hint_antivirus'
  | 'backend.hint_gatekeeper'
  | 'backend.hint_noexec'
  | 'backend.hint_download'
  | 'backend.hint_illegal_instruction'
  | 'backend.hint_slow_start'
  | 'backend.hint_synced_folder';

export interface FailureGuidance {
  cause: BackendCause | null;
  hints: BackendHintKey[];
}

const NO_GUIDANCE: FailureGuidance = { cause: null, hints: [] };

/**
 * What the failure screen says for each code. `null` causes keep the screen's
 * existing text: those codes already have their own wording (setup issues,
 * port conflicts) or have no single fix to name, and still get a docs link.
 */
export const FAILURE_GUIDANCE: Record<InstallFailureCode, FailureGuidance> = {
  WINDOWS_APP_CONTROL_BLOCKED: {
    cause: 'app_control_policy',
    hints: ['backend.hint_app_control_policy'],
  },
  ANTIVIRUS_BLOCKED: { cause: 'antivirus', hints: ['backend.hint_antivirus'] },
  EXEC_REFUSED: { cause: 'spawn_blocked', hints: ['backend.hint_spawn_blocked'] },
  GATEKEEPER_QUARANTINE: { cause: 'gatekeeper', hints: ['backend.hint_gatekeeper'] },
  EXEC_NOEXEC_MOUNT: { cause: 'noexec', hints: ['backend.hint_noexec'] },
  RUNTIME_DOWNLOAD_STALLED: { cause: 'download', hints: ['backend.hint_download'] },
  RUNTIME_DOWNLOAD_INTERRUPTED: { cause: 'download', hints: ['backend.hint_download'] },
  DISK_FULL: NO_GUIDANCE,
  RUNTIME_FOLDER_DENIED: { cause: 'permission', hints: [] },
  RUNTIME_FILES_LOCKED: NO_GUIDANCE,
  RUNTIME_IN_SYNCED_FOLDER: { cause: 'synced_folder', hints: ['backend.hint_synced_folder'] },
  SYSTEM_LIBRARY_PROBLEM: NO_GUIDANCE,
  UNSUPPORTED_PLATFORM: NO_GUIDANCE,
  SOURCE_ENV_INCOMPLETE: { cause: 'missing_env', hints: [] },
  BROKEN_VENV: { cause: 'missing_env', hints: [] },
  NATIVE_ACCESS_VIOLATION: { cause: 'native_crash', hints: [] },
  NATIVE_ILLEGAL_INSTRUCTION: {
    cause: 'illegal_instruction',
    hints: ['backend.hint_illegal_instruction'],
  },
  NATIVE_CRASH: { cause: 'native_crash', hints: [] },
  STARTUP_TIMEOUT_SILENT: { cause: 'slow_start', hints: ['backend.hint_slow_start'] },
  STARTUP_TIMEOUT_OUTPUT: { cause: 'slow_start', hints: ['backend.hint_slow_start'] },
  BACKEND_STARTUP_FAILED: NO_GUIDANCE,
  PORT_IN_USE: NO_GUIDANCE,
  ROCM_NOT_CONFIGURED: NO_GUIDANCE,
  UNCLASSIFIED: NO_GUIDANCE,
};

/**
 * Guidance for one failure. An application control block is Smart App Control
 * only when that is known: a work PC's WDAC or AppLocker policy must not be told
 * to turn off a setting it may not even have.
 */
export function failureGuidance(
  code: InstallFailureCode,
  { message, smartAppControl }: { message?: string; smartAppControl?: SmartAppControlState } = {},
): FailureGuidance {
  const base = FAILURE_GUIDANCE[code];
  let guidance = base;
  if (message) message = withoutBackendLogEvidence(message);
  if (
    code === 'WINDOWS_APP_CONTROL_BLOCKED' &&
    (smartAppControl === 'on' ||
      smartAppControl === 'evaluation' ||
      /Smart App Control/.test(message ?? ''))
  ) {
    guidance = {
      cause: 'smart_app_control',
      hints: ['backend.hint_smart_app_control', 'backend.hint_smart_app_control_local'],
    };
  }
  if (message && inSyncedFolder(message) && !guidance.hints.includes('backend.hint_synced_folder'))
    return { ...guidance, hints: [...guidance.hints, 'backend.hint_synced_folder'] };
  return guidance;
}

/** Docs anchor of a code's section in docs/install/troubleshooting.md. */
export function failureDocsAnchor(code: InstallFailureCode): string {
  return code.toLowerCase();
}

/** Encoded budget for the facts block; the report's 7000 limit leaves the rest to logs. */
export const MAX_ENCODED_FACTS = 1500;

export function failureMarker(code: InstallFailureCode): string {
  return `<!-- vs-failure:${code}:1 -->`;
}

/** Facts for an issue body, most useful first, cut to fit before logs are. */
export function formatFailureFacts(failure: InstallFailure): string[] {
  const line = (label: string, value: unknown): string[] => {
    if (value === undefined || value === null || value === '') return [];
    if (Array.isArray(value) && value.length === 0) return [];
    return [`- ${label}: ${Array.isArray(value) ? value.join(', ') : String(value)}`];
  };
  const download = [
    failure.downloadHost,
    failure.downloadedBytes === undefined
      ? undefined
      : `${Math.round(failure.downloadedBytes / 1024 ** 2)} MB received`,
    failure.stalledSeconds === undefined
      ? undefined
      : `no progress for ${failure.stalledSeconds} s`,
  ]
    .filter(Boolean)
    .join(', ');
  const facts = [
    ...line('Code', failure.code),
    ...line('Stage', [failure.stage, failure.setupPhase].filter(Boolean).join(' / ')),
    ...line(
      'Program',
      failure.program &&
        `${failure.program}${failure.programLocation ? ` (${failure.programLocation})` : ''}`,
    ),
    ...line('Error', failure.errorCode),
    ...line('Exit code', failure.exitCode),
    ...line('Signal', failure.signal),
    ...line('Smart App Control', failure.smartAppControl),
    ...line('Blocked by Windows', failure.blockedFiles),
    ...line('Signature', failure.signature),
    ...line('Antivirus', failure.antivirus),
    ...line('Quarantined', failure.quarantined),
    ...line('noexec mount', failure.noexecMount),
    ...line('Download', download),
    ...line('Proxy set', failure.proxyConfigured),
    ...line('Region', failure.region),
    ...line('Free disk (GB)', failure.diskFreeGb),
    ...line('Path flags', failure.pathFlags),
    ...line('Elapsed (s)', failure.elapsedSeconds),
  ];
  const section = (lines: string[]) => [
    failureMarker(failure.code),
    '## Install failure',
    '',
    ...lines,
    '',
  ];
  while (
    facts.length > 1 &&
    encodeURIComponent(section(facts).join('\n')).length > MAX_ENCODED_FACTS
  )
    facts.pop();
  return section(facts);
}

/** `spawn UNKNOWN` quoted 40 times says less than quoting it once with a count. */
export function collapseRepeatedLines(lines: readonly string[]): string[] {
  const output: string[] = [];
  let count = 0;
  const flush = () => {
    if (count > 1) output[output.length - 1] += ` (×${count})`;
  };
  for (const line of lines) {
    if (count > 0 && output[output.length - 1] === line) {
      count += 1;
      continue;
    }
    flush();
    output.push(line);
    count = 1;
  }
  flush();
  return output;
}
