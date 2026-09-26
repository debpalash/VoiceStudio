/**
 * nativeExit — naming the way a backend process died, from either shell.
 *
 * When the backend dies natively there is no Python traceback, only an exit
 * code. On Windows that code is an NTSTATUS, and the two desktop shells report
 * the same status differently:
 *
 *   Tauri    Rust `ExitStatus::code()` returns i32  ->  -1073741819
 *   Electron Node reports the raw DWORD             ->   3221225477
 *
 * Both are 0xC0000005, STATUS_ACCESS_VIOLATION. A table written against one
 * representation silently matches nothing from the other, which is how
 * https://github.com/debpalash/VoiceStudio/issues/2250 arrived titled
 * "exit code 3221225477" with no indication that the backend had segfaulted.
 *
 * Signals diverge the same way: POSIX Tauri reports the number (11), Node
 * reports the name ('SIGSEGV').
 *
 * So nothing here picks a side. Every entry point normalises first, then
 * classifies — which is also what keeps a third producer (the backend's own
 * run-sentinel) from needing a fourth table.
 */

/**
 * Windows statuses that mean "the process was killed by a hardware or runtime
 * fault", keyed by the unsigned DWORD. Not an exhaustive NTSTATUS list — only
 * the faults that reach a user as an ordinary non-zero exit and would
 * otherwise be indistinguishable from a clean failure.
 */
const NT_FAULT_STATUS: ReadonlyMap<number, string> = new Map([
  [0xc0000005, 'STATUS_ACCESS_VIOLATION'],
  [0xc000001d, 'STATUS_ILLEGAL_INSTRUCTION'],
  [0xc0000096, 'STATUS_PRIVILEGED_INSTRUCTION'],
  [0xc0000409, 'STATUS_STACK_BUFFER_OVERRUN'],
  [0xc00000fd, 'STATUS_STACK_OVERFLOW'],
]);

/**
 * Signal numbers that mean a native fault. Deliberately only SIGILL (4) and
 * SIGSEGV (11) — the two whose numbers are identical on every POSIX platform.
 *
 * SIGABRT (6) is excluded on purpose: abort() is how a fatal CUDA error exits,
 * including an asynchronous "CUDA error: out of memory", so it keeps the VRAM
 * guidance. SIGBUS is excluded because its number is platform-dependent (7 on
 * Linux, 10 on macOS, where 10 is SIGUSR1 on Linux) and guessing wrong would
 * misfile an ordinary signal as a hardware fault. SIGKILL (9) is the OS memory
 * killer, not a fault, and is classified separately by callers.
 */
const NATIVE_FAULT_SIGNALS: ReadonlySet<number> = new Set([4, 11]);

/** The signal names a caller may see instead of a number, and their numbers. */
const SIGNAL_NUMBERS: ReadonlyMap<string, number> = new Map([
  ['SIGILL', 4],
  ['SIGABRT', 6],
  ['SIGKILL', 9],
  ['SIGSEGV', 11],
]);

const U32 = 0x1_0000_0000;

/**
 * The exit code as an unsigned DWORD, so both shells agree.
 *
 * A Windows NTSTATUS read as i32 is negative; the same status read as a DWORD
 * is that value plus 2^32. POSIX exit codes (0-255) and ordinary positive
 * codes are returned untouched.
 */
export function normalizeExitCode(code: number | null | undefined): number | null {
  if (typeof code !== 'number' || !Number.isFinite(code)) return null;
  return code < 0 ? code + U32 : code;
}

/** The signal as a number, accepting either `11` or `'SIGSEGV'`. */
export function normalizeSignal(signal: number | string | null | undefined): number | null {
  if (typeof signal === 'number' && Number.isFinite(signal)) return signal;
  if (typeof signal !== 'string') return null;
  const named = SIGNAL_NUMBERS.get(signal.trim().toUpperCase());
  return named ?? null;
}

/** `'STATUS_ACCESS_VIOLATION'` for a known Windows fault, else `null`. */
export function ntStatusName(code: number | null | undefined): string | null {
  const normalized = normalizeExitCode(code);
  return normalized == null ? null : (NT_FAULT_STATUS.get(normalized) ?? null);
}

/**
 * The exit code as a person should read it:
 * `3221225477 (0xC0000005 STATUS_ACCESS_VIOLATION)`, or just the number when
 * it names nothing. The raw value is kept first so it still matches what the
 * shell logged and what a user searched for.
 */
export function describeExitCode(
  code: number | null | undefined,
  unknownLabel = 'unknown',
): string {
  if (typeof code !== 'number' || !Number.isFinite(code)) return unknownLabel;
  const name = ntStatusName(code);
  if (!name) return String(code);
  const normalized = normalizeExitCode(code) as number;
  return `${code} (0x${normalized.toString(16).toUpperCase().padStart(8, '0')} ${name})`;
}

/**
 * True when the process died of a native fault — a segfault, an illegal
 * instruction, a stack overflow — rather than memory pressure or an orderly
 * non-zero exit.
 */
export function isNativeFaultExit(death: {
  exitCode?: number | null;
  signal?: number | string | null;
}): boolean {
  const signal = normalizeSignal(death.signal);
  if (signal != null && NATIVE_FAULT_SIGNALS.has(signal)) return true;
  return ntStatusName(death.exitCode) != null;
}
