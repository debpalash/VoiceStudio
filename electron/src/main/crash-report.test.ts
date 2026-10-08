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

// #2382: the shape of a real Windows dump. The faulting thread recursed
// through Module._apply far deeper than the old 40-line / 1200-character
// window, so the report showed only torch internals and no other thread.
const VENV = String.raw`~\AppData\Roaming\VoiceStudio\runtime\project\.venv\Lib\site-packages`;
const STDLIB = String.raw`~\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\Lib`;
const PROJECT = String.raw`~\AppData\Roaming\VoiceStudio\runtime\project`;
const windowsDump = [
  'INFO:     127.0.0.1:56863 - "GET /model/status HTTP/1.1" 200 OK',
  'Windows fatal exception: access violation',
  '',
  'Thread 0x00001111 (most recent call first):',
  `  File "${STDLIB}\\threading.py", line 324 in wait`,
  `  File "${PROJECT}\\backend\\services\\asr_backend.py", line 812 in _transcribe_chunk`,
  `  File "${STDLIB}\\concurrent\\futures\\thread.py", line 58 in run`,
  `  File "${STDLIB}\\threading.py", line 1002 in _bootstrap`,
  '',
  'Current thread 0x00002ad8 (most recent call first):',
  `  File "${VENV}\\torch\\nn\\modules\\module.py", line 1355 in convert`,
  `  File "${VENV}\\torch\\nn\\modules\\module.py", line 955 in _apply`,
  ...Array(40).fill(`  File "${VENV}\\torch\\nn\\modules\\module.py", line 928 in _apply`),
  `  File "${VENV}\\torch\\nn\\modules\\module.py", line 1369 in to`,
  `  File "${PROJECT}\\backend\\services\\model_manager.py", line 2901 in _load_model_sync`,
  `  File "${STDLIB}\\concurrent\\futures\\thread.py", line 58 in run`,
  ...Array(10).fill(`  File "${STDLIB}\\threading.py", line 1045 in _bootstrap_inner`),
  'INFO:     127.0.0.1:50117 - "GET /model/loaded HTTP/1.1" 200 OK',
  '',
  'Thread 0x00003333 (most recent call first):',
  `  File "${VENV}\\uvicorn\\server.py", line 77 in run`,
  `  File "<frozen runpy>", line 198 in _run_module_as_main`,
  `Extension modules: ${'torch._C, '.repeat(300)}`,
].join('\n');

it('keeps the frames that identify a deep native fault and the other threads (#2382)', async () => {
  const { nativeCrashExcerpt } = await import('../shared/utils/crashReport');
  const report = clampCrashTail(windowsDump);
  expect(report.length).toBeLessThanOrEqual(1200);
  expect(report).toContain('Windows fatal exception: access violation');
  expect(report).toContain('File "…/torch/nn/modules/module.py", line 1355 in convert');
  expect(report).toContain('[Previous line repeated 39 more times]');
  expect(report).toContain('backend/services/model_manager.py", line 2901 in _load_model_sync');
  // The concurrent thread and the job it was running.
  expect(report).toContain('Thread 0x00001111');
  expect(report).toContain('backend/services/asr_backend.py", line 812 in _transcribe_chunk');
  expect(report).not.toContain('site-packages');
  expect(report).not.toContain('Extension modules');
  expect(report).not.toContain('GET /model');
  // The faulting thread leads even though another thread was dumped first.
  expect(report.indexOf('Current thread')).toBeLessThan(report.indexOf('Thread 0x00001111'));
  // Stored excerpts are condensed again by the report: that must not change them.
  expect(nativeCrashExcerpt(nativeCrashExcerpt(windowsDump))).toBe(nativeCrashExcerpt(windowsDump));
});

it('summarises a native fault by where it happened instead of the last output line', async () => {
  const { nativeFaultSummary } = await import('../shared/utils/crashReport');
  expect(nativeFaultSummary(windowsDump)).toBe(
    'Windows fatal exception: access violation at …/torch/nn/modules/module.py:1355 in convert ' +
      '(from backend/services/model_manager.py:2901 in _load_model_sync)',
  );
  expect(nativeFaultSummary('Fatal Python error: Aborted')).toBe('Fatal Python error: Aborted');
  expect(nativeFaultSummary('RuntimeError: ordinary failure')).toBe('');
});

it('keeps a library-only fault readable without a VoiceStudio frame', async () => {
  const { nativeCrashExcerpt } = await import('../shared/utils/crashReport');
  const excerpt = nativeCrashExcerpt(
    [
      'Fatal Python error: Segmentation fault',
      'Current thread 0x1 (most recent call first):',
      ...Array.from(
        { length: 12 },
        (_, i) => `  File "/usr/lib/python3.11/m${i}.py", line ${i} in f${i}`,
      ),
    ].join('\n'),
  );
  expect(excerpt).toContain('m0.py');
  expect(excerpt).toContain('m5.py');
  expect(excerpt).not.toContain('m6.py');
  expect(excerpt).toContain('… 6 more frames');
});
