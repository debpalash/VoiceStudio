// Classifies how the backend process died. Rust reports a signed i32 exit
// status while Node reports the raw unsigned DWORD, so every comparison goes
// through the unsigned form.

const UINT32_RANGE = 0x1_0000_0000;

const POSIX_SIGNALS = new Map<string, number>([
  ['SIGILL', 4],
  ['SIGABRT', 6],
  ['SIGKILL', 9],
  ['SIGSEGV', 11],
]);

// NTSTATUS values that mean the process itself misbehaved natively.
const NT_FAULTS = new Map<number, string>([
  [0xc0000005, 'STATUS_ACCESS_VIOLATION'],
  [0xc000001d, 'STATUS_ILLEGAL_INSTRUCTION'],
  [0xc0000096, 'STATUS_PRIVILEGED_INSTRUCTION'],
  [0xc0000409, 'STATUS_STACK_BUFFER_OVERRUN'],
  [0xc00000fd, 'STATUS_STACK_OVERFLOW'],
]);

// Only signals that point at bad code or CPU features count; SIGABRT and
// SIGKILL have their own handling elsewhere.
const FAULT_SIGNALS: ReadonlySet<number> = new Set([4, 11]);

// Exit status 0xFFFFFFFF (unsigned) / -1 (signed): the process never reached a
// real exit code. The shell records this when the backend was ended from
// outside — security software, an OS policy, or another program — rather than
// the backend choosing to exit (or faulting).
const EXTERNAL_TERMINATION = 0xffff_ffff;
const EXTERNAL_TERMINATION_LABEL = 'EXTERNAL_TERMINATION';

/** The value itself when it is a finite number, otherwise null. */
function finiteOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function normalizeExitCode(code: number | null | undefined): number | null {
  const n = finiteOrNull(code);
  if (n === null) return null;
  return Number.isInteger(n) && n < 0 ? n + UINT32_RANGE : n;
}

export function normalizeSignal(signal: number | string | null | undefined): number | null {
  if (typeof signal === 'string') return POSIX_SIGNALS.get(signal.trim().toUpperCase()) ?? null;
  return finiteOrNull(signal);
}

export function ntStatusName(code: number | null | undefined): string | null {
  const unsigned = normalizeExitCode(code);
  return unsigned === null ? null : (NT_FAULTS.get(unsigned) ?? null);
}

/** True when the exit status is 0xFFFFFFFF / -1 — the process was ended from
 * outside (security software, an OS policy, another program) and never
 * reached a real exit code. */
export function isExternalKillExit(code: number | null | undefined): boolean {
  return normalizeExitCode(code) === EXTERNAL_TERMINATION;
}

export function describeExitCode(
  code: number | null | undefined,
  unknownLabel = 'unknown',
): string {
  const raw = finiteOrNull(code);
  if (raw === null) return unknownLabel;
  const name = ntStatusName(raw);
  const unsigned = normalizeExitCode(raw);
  if (name === null && unsigned !== EXTERNAL_TERMINATION) return String(raw);
  const hex = (unsigned ?? 0).toString(16).toUpperCase().padStart(8, '0');
  const label = name ?? EXTERNAL_TERMINATION_LABEL;
  return `${raw} (0x${hex} ${label})`;
}

export function isNativeFaultExit(death: {
  exitCode?: number | null;
  signal?: number | string | null;
}): boolean {
  const sig = normalizeSignal(death?.signal);
  return (sig !== null && FAULT_SIGNALS.has(sig)) || ntStatusName(death?.exitCode) !== null;
}
