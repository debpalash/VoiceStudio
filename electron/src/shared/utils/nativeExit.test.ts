import { describe, expect, test } from 'vitest';
import {
  describeExitCode,
  isExternalKillExit,
  isNativeFaultExit,
  normalizeExitCode,
  normalizeSignal,
  ntStatusName,
} from './nativeExit';

// [symbolic name, unsigned DWORD, same value as a signed i32]
const NT_TABLE = [
  ['STATUS_ACCESS_VIOLATION', 3221225477, -1073741819],
  ['STATUS_ILLEGAL_INSTRUCTION', 3221225501, -1073741795],
  ['STATUS_PRIVILEGED_INSTRUCTION', 3221225622, -1073741674],
  ['STATUS_STACK_BUFFER_OVERRUN', 3221226505, -1073740791],
  ['STATUS_STACK_OVERFLOW', 3221225725, -1073741571],
] as const;

describe.each(NT_TABLE)('%s', (name, unsigned, signed) => {
  test('both encodings collapse to one unsigned value', () => {
    expect(normalizeExitCode(signed)).toBe(unsigned);
    expect(normalizeExitCode(unsigned)).toBe(unsigned);
  });
  test('both encodings resolve to the symbol and count as faults', () => {
    for (const code of [signed, unsigned]) {
      expect(ntStatusName(code)).toBe(name);
      expect(isNativeFaultExit({ exitCode: code })).toBe(true);
    }
  });
  test('description leads with the number exactly as given', () => {
    const hex = unsigned.toString(16).toUpperCase();
    expect(describeExitCode(signed)).toBe(`${signed} (0x${hex} ${name})`);
    expect(describeExitCode(unsigned)).toBe(`${unsigned} (0x${hex} ${name})`);
  });
});

describe.each([
  ['clean exit', 0],
  ['ordinary failure', 2],
  ['POSIX SIGKILL (128 + 9)', 137],
  ['POSIX SIGSEGV (128 + 11)', 139],
  ['NTSTATUS outside the fault table (STATUS_DLL_NOT_FOUND)', 0xc0000135],
] as const)('%s is not a native fault', (_label, code) => {
  test('has no symbolic name, is not a fault, and is described as the bare number', () => {
    expect(ntStatusName(code)).toBeNull();
    expect(isNativeFaultExit({ exitCode: code })).toBe(false);
    expect(describeExitCode(code)).toBe(String(code));
  });
});

describe('external termination (0xFFFFFFFF / -1)', () => {
  test('both encodings collapse to one unsigned value and classify together', () => {
    expect(normalizeExitCode(-1)).toBe(4294967295);
    expect(normalizeExitCode(4294967295)).toBe(4294967295);
    expect(isExternalKillExit(-1)).toBe(true);
    expect(isExternalKillExit(4294967295)).toBe(true);
    expect(isExternalKillExit(0)).toBe(false);
    expect(isExternalKillExit(null)).toBe(false);
  });
  test('is never reported as a native fault', () => {
    expect(isNativeFaultExit({ exitCode: -1 })).toBe(false);
    expect(isNativeFaultExit({ exitCode: 4294967295 })).toBe(false);
    expect(ntStatusName(-1)).toBeNull();
    expect(ntStatusName(4294967295)).toBeNull();
  });
  test('description names the external termination with its hex form', () => {
    expect(describeExitCode(-1)).toBe('-1 (0xFFFFFFFF EXTERNAL_TERMINATION)');
    expect(describeExitCode(4294967295)).toBe('4294967295 (0xFFFFFFFF EXTERNAL_TERMINATION)');
  });
});

describe('unusable input', () => {
  const junk = [null, undefined, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY];
  test('every helper degrades quietly', () => {
    for (const v of junk) {
      expect(normalizeExitCode(v)).toBeNull();
      expect(normalizeSignal(v)).toBeNull();
      expect(ntStatusName(v)).toBeNull();
      expect(describeExitCode(v)).toBe('unknown');
      expect(isNativeFaultExit({ exitCode: v, signal: v })).toBe(false);
    }
    expect(isNativeFaultExit({})).toBe(false);
    expect(describeExitCode(null, '?')).toBe('?');
  });
  test('positive codes pass through untouched', () => {
    expect(normalizeExitCode(42)).toBe(42);
  });
});

describe('signal handling', () => {
  test('names are trimmed and case-insensitive', () => {
    expect(normalizeSignal('\tsigill\n')).toBe(4);
    expect(normalizeSignal('SigAbrt')).toBe(6);
    expect(normalizeSignal('SIGTERM')).toBeNull();
    expect(normalizeSignal('')).toBeNull();
  });
  test('numbers are kept as they are', () => {
    expect(normalizeSignal(15)).toBe(15);
  });
  test('only illegal-instruction and segfault signals are faults', () => {
    expect(isNativeFaultExit({ signal: 'SIGILL' })).toBe(true);
    expect(isNativeFaultExit({ signal: ' sigsegv' })).toBe(true);
    expect(isNativeFaultExit({ signal: 11 })).toBe(true);
    for (const sig of [1, 6, 9, 15, 'SIGABRT', 'SIGKILL', 'SIGTERM']) {
      expect(isNativeFaultExit({ signal: sig })).toBe(false);
    }
  });
});
