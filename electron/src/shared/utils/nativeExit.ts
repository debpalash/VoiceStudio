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

// Exit status 0xFFFFFFFF (unsigned) / -1 (signed) is what the OS reports when
// the process never produced a real exit code. It carries no evidence of what
// ended it, so describeExitCode shows the hex form for readability but asserts
// no cause.
const NO_STATUS = 0xffff_ffff;

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

export function describeExitCode(
  code: number | null | undefined,
  unknownLabel = 'unknown',
): string {
  const raw = finiteOrNull(code);
  if (raw === null) return unknownLabel;
  const name = ntStatusName(raw);
  const unsigned = normalizeExitCode(raw);
  if (name === null && unsigned !== NO_STATUS) return String(raw);
  const hex = (unsigned ?? 0).toString(16).toUpperCase().padStart(8, '0');
  return name === null ? `${raw} (0x${hex})` : `${raw} (0x${hex} ${name})`;
}

export function isNativeFaultExit(death: {
  exitCode?: number | null;
  signal?: number | string | null;
}): boolean {
  const sig = normalizeSignal(death?.signal);
  return (sig !== null && FAULT_SIGNALS.has(sig)) || ntStatusName(death?.exitCode) !== null;
}
