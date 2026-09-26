// @vitest-environment node
import { expect, it } from 'vitest';
import { clampCrashTail } from '../shared/utils/crashReport';

it.each(['Fatal Python error: Segmentation fault', 'Windows fatal exception: access violation'])(
  'preserves native fault frames under the report character limit: %s',
  (header) => {
    const text = [
      'older log'.repeat(500),
      header,
      'Thread 0x111 (most recent call first):',
      '  File "threading.py", line 10 in wait'.repeat(100),
      'Current thread 0x222 (most recent call first):',
      '  File "failing_native_module.py", line 42 in load',
      '  File "runpy.py", line 198 in _run_module_as_main'.repeat(100),
      `Extension modules: ${'torch._C, '.repeat(600)}`,
    ].join('\n');
    const report = clampCrashTail(text);
    expect(report.length).toBeLessThanOrEqual(1200);
    expect(report).toContain(header);
    expect(report).toContain('failing_native_module.py');
    expect(report).not.toContain('Extension modules:');
  },
);

it('uses the latest native fault and supports single-thread dumps without Current thread', () => {
  const report = clampCrashTail(
    [
      'Fatal Python error: old fault',
      '  File "old.py", line 1 in old',
      'Fatal Python error: Aborted',
      'Thread 0x333 (most recent call first):',
      '  File "latest.py", line 7 in abort',
      `Extension modules: ${'module, '.repeat(300)}`,
    ].join('\n'),
  );
  expect(report).toContain('latest.py');
  expect(report).not.toContain('old.py');
  expect(report).not.toContain('Extension modules:');
});

it('still retains the newest ordinary exception and the original chained cause', () => {
  const text = [
    'OSError: underlying failure',
    'The above exception was the direct cause of the following exception:',
    '  frame\n'.repeat(300),
    'RuntimeError: final failure',
  ].join('\n');
  const report = clampCrashTail(text);
  expect(report).toContain('OSError: underlying failure');
  expect(report).toContain('RuntimeError: final failure');
  expect(report.length).toBeLessThanOrEqual(1200);
});
