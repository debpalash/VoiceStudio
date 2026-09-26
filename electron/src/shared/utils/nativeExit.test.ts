/**
 * A native death has to be recognisable from either shell.
 *
 * When the backend is killed below Python there is no traceback, only an exit
 * code — and on Windows that code is an NTSTATUS the two shells report
 * differently:
 *
 *   Tauri    Rust `ExitStatus::code()` -> i32   -> -1073741819
 *   Electron Node -> the raw DWORD             ->  3221225477
 *
 * Both are 0xC0000005. The fault table used to hold only Rust's form, so the
 * same access violation was a recognised fault from Tauri and an anonymous
 * non-zero exit from Electron — which is how issue #2250 arrived titled
 * "exit code 3221225477" with nothing saying the backend had segfaulted.
 *
 * Signals split the same way: POSIX gives 11, Node gives 'SIGSEGV'.
 */
import { describe, expect, it } from 'vitest';
import {
  describeExitCode,
  isNativeFaultExit,
  normalizeExitCode,
  normalizeSignal,
  ntStatusName,
} from './nativeExit';

// Every Windows fault the table knows, in both representations.
const FAULTS: ReadonlyArray<[string, number, number]> = [
  ['STATUS_ACCESS_VIOLATION', 3221225477, -1073741819],
  ['STATUS_ILLEGAL_INSTRUCTION', 3221225501, -1073741795],
  ['STATUS_PRIVILEGED_INSTRUCTION', 3221225622, -1073741674],
  ['STATUS_STACK_BUFFER_OVERRUN', 3221226505, -1073740791],
  ['STATUS_STACK_OVERFLOW', 3221225725, -1073741571],
];

describe('both shells report the same death', () => {
  it.each(FAULTS)('%s is one fault, not two', (name, unsigned, signed) => {
    expect(normalizeExitCode(signed)).toBe(unsigned);
    expect(ntStatusName(unsigned)).toBe(name);
    expect(ntStatusName(signed)).toBe(name);
    expect(isNativeFaultExit({ exitCode: unsigned })).toBe(true);
    expect(isNativeFaultExit({ exitCode: signed })).toBe(true);
  });

  it('reads the exact code from issue #2250 as an access violation', () => {
    expect(isNativeFaultExit({ exitCode: 3221225477 })).toBe(true);
    expect(describeExitCode(3221225477)).toBe('3221225477 (0xC0000005 STATUS_ACCESS_VIOLATION)');
  });

  it('keeps the raw value first so it still matches the log', () => {
    // A user searching their own log for "-1073741819" must still find it.
    expect(describeExitCode(-1073741819)).toContain('-1073741819');
    expect(describeExitCode(-1073741819)).toContain('STATUS_ACCESS_VIOLATION');
  });
});

describe('signals, numbered or named', () => {
  it.each([
    [11, 'SIGSEGV'],
    [4, 'SIGILL'],
  ])('signal %i and %s are the same fault', (number, name) => {
    expect(normalizeSignal(name)).toBe(number);
    expect(isNativeFaultExit({ signal: number })).toBe(true);
    expect(isNativeFaultExit({ signal: name })).toBe(true);
  });

  it('is case- and whitespace-tolerant about the name', () => {
    expect(normalizeSignal(' sigsegv ')).toBe(11);
  });

  it('leaves an unknown name alone rather than guessing', () => {
    expect(normalizeSignal('SIGWINCH')).toBeNull();
    expect(isNativeFaultExit({ signal: 'SIGWINCH' })).toBe(false);
  });
});

describe('what must NOT be called a native fault', () => {
  it('SIGKILL is the OS memory killer, not a fault', () => {
    // Kept out deliberately: callers give this its own out-of-memory guidance.
    expect(isNativeFaultExit({ signal: 9 })).toBe(false);
    expect(isNativeFaultExit({ signal: 'SIGKILL' })).toBe(false);
  });

  it('SIGABRT is how a fatal CUDA error exits, so it keeps the VRAM advice', () => {
    expect(isNativeFaultExit({ signal: 6 })).toBe(false);
    expect(isNativeFaultExit({ signal: 'SIGABRT' })).toBe(false);
  });

  it.each([0, 1, 78, 255])('an ordinary exit code (%i) is not a fault', (code) => {
    expect(isNativeFaultExit({ exitCode: code })).toBe(false);
    expect(ntStatusName(code)).toBeNull();
    expect(describeExitCode(code)).toBe(String(code));
  });

  it('a Windows status outside the table is not invented into one', () => {
    // 0xC0000135 (DLL not found) is a real NTSTATUS but not a hardware fault.
    expect(ntStatusName(0xc0000135)).toBeNull();
    expect(isNativeFaultExit({ exitCode: 0xc0000135 })).toBe(false);
  });

  it('nothing at all stays nothing', () => {
    expect(isNativeFaultExit({})).toBe(false);
    expect(isNativeFaultExit({ exitCode: null, signal: null })).toBe(false);
    expect(normalizeExitCode(null)).toBeNull();
    expect(normalizeExitCode(undefined)).toBeNull();
    expect(normalizeSignal(null)).toBeNull();
    expect(describeExitCode(null)).toBe('unknown');
  });

  it('survives a nonsense value instead of throwing mid-report', () => {
    expect(normalizeExitCode(Number.NaN)).toBeNull();
    expect(describeExitCode(Number.NaN)).toBe('unknown');
    expect(isNativeFaultExit({ exitCode: Number.NaN })).toBe(false);
  });
});
