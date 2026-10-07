// @vitest-environment node
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { CrashJournal } from './crash-journal';
it('retains three bounded records across restart and ignores other versions without rewriting', () => {
  const path = join(mkdtempSync(join(tmpdir(), 'voice-crash-')), 'crashes.json');
  const journal = new CrashJournal(path, '1');
  for (let code = 1; code <= 4; code++)
    journal.record(code, null, 100, Array(45).fill('x'.repeat(5000)));
  const restored = new CrashJournal(path, '1').latest()!;
  expect(restored.exitCode).toBe(4);
  expect(restored.logTail).toHaveLength(40);
  expect(restored.logTail[0]).toHaveLength(4096);
  expect(restored.acknowledged).toBe(false);
  const saved = readFileSync(path, 'utf8');
  expect(JSON.parse(saved)).toHaveLength(3);
  expect(new CrashJournal(path, '2').latest()).toBeUndefined();
  expect(readFileSync(path, 'utf8')).toBe(saved);
});
it('retains acknowledged evidence across restart and resets seen state for a new crash', () => {
  const path = join(mkdtempSync(join(tmpdir(), 'voice-crash-')), 'crashes.json');
  const journal = new CrashJournal(path, '1');
  journal.record(1, null, 100, ['first']);
  expect(journal.acknowledgeLatest()?.acknowledged).toBe(true);
  expect(new CrashJournal(path, '1').latest()).toMatchObject({
    acknowledged: true,
    logTail: ['first'],
  });
  journal.record(2, null, 200, ['second']);
  expect(journal.latest()).toMatchObject({ exitCode: 2, acknowledged: false });
});
it('ignores port collisions and debugger exits, and tolerates corrupt or unwritable storage', () => {
  const path = join(mkdtempSync(join(tmpdir(), 'voice-crash-')), 'crashes.json');
  writeFileSync(path, 'corrupt');
  const journal = new CrashJournal(path, '1');
  journal.record(78, null, 0, []);
  journal.record(0x40010004, null, 0, []);
  expect(journal.latest()).toBeUndefined();
  expect(readFileSync(path, 'utf8')).toBe('corrupt');
  const inaccessible = new CrashJournal(join(path, 'child.json'), '1');
  inaccessible.record(null, 'SIGKILL', 3000, ['out of memory']);
  expect(inaccessible.latest()).toMatchObject({ signal: 'SIGKILL', uptimeMs: 3000 });
});

it.each(['Fatal Python error: Segmentation fault', 'Windows fatal exception: access violation'])(
  'retains the faulting thread instead of extension lists: %s',
  (header) => {
    const path = join(mkdtempSync(join(tmpdir(), 'voice-crash-')), 'crashes.json');
    const journal = new CrashJournal(path, '1');
    journal.record(null, 'SIGSEGV', 100, [
      ...Array(100).fill('startup'),
      header,
      'Thread 0x111 (most recent call first):',
      ...Array(50).fill('  File "threading.py", line 10 in wait'),
      'Current thread 0x222 (most recent call first):',
      '  File "failing_native_module.py", line 42 in load',
      ...Array(60).fill('  File "runpy.py", line 198 in _run_module_as_main'),
      `Extension modules: ${'torch._C, '.repeat(600)}`,
    ]);
    const restored = new CrashJournal(path, '1').latest()!;
    expect(restored.logTail.join('\n')).toContain(header);
    expect(restored.logTail.join('\n')).toContain('failing_native_module.py');
    expect(restored.logTail.join('\n')).not.toContain('Extension modules:');
    expect(restored.logTail.length).toBeLessThanOrEqual(40);
  },
);

it('clears streaming evidence between backend runs', () => {
  const path = join(mkdtempSync(join(tmpdir(), 'voice-crash-')), 'crashes.json');
  const journal = new CrashJournal(path, '1');
  journal.captureLine('Fatal Python error: old crash');
  journal.captureLine('  File "old.py", line 1 in fault');
  journal.resetCapture();
  journal.captureLine('RuntimeError: current failure');
  journal.record(1, null, 200, [
    'Fatal Python error: old crash',
    ...Array(45).fill('old output'),
    'RuntimeError: current failure',
  ]);
  expect(journal.latest()!.logTail.join('\n')).toContain('RuntimeError: current failure');
  expect(journal.latest()!.logTail.join('\n')).not.toContain('old crash');
});

it.each([
  '/Users/private-person/voice.py',
  '/home/private-person/voice.py',
  String.raw`C:\Users\private-person\voice.py`,
])('scrubs home paths and credentials before persisting: %s', (source) => {
  const path = join(mkdtempSync(join(tmpdir(), 'voice-crash-')), 'crashes.json');
  const journal = new CrashJournal(path, '1');
  journal.captureLine('Fatal Python error: Segmentation fault');
  journal.captureLine(`  File "${source}", line 42 in load`);
  journal.record(null, 'SIGSEGV', 100, []);
  journal.record(1, null, 100, [`${source} Bearer ${'a'.repeat(32)}`]);
  const stored = readFileSync(path, 'utf8');
  expect(stored).not.toContain('private-person');
  expect(stored).not.toContain('a'.repeat(32));
  expect(stored).toContain('voice.py');
  expect(stored).toContain('REDACTED');
});

it('records the loading frame below deep recursion and the other threads (#2382)', () => {
  const path = join(mkdtempSync(join(tmpdir(), 'voice-crash-')), 'crashes.json');
  const journal = new CrashJournal(path, '1');
  for (const line of [
    'Windows fatal exception: access violation',
    'Current thread 0x222 (most recent call first):',
    '  File "/venv/lib/python3.11/site-packages/torch/nn/modules/module.py", line 1355 in convert',
    ...Array(300).fill(
      '  File "/venv/lib/python3.11/site-packages/torch/nn/modules/module.py", line 928 in _apply',
    ),
    '  File "/app/backend/services/model_manager.py", line 2901 in _load_model_sync',
    'Thread 0x111 (most recent call first):',
    '  File "/app/backend/services/asr_backend.py", line 812 in _transcribe_chunk',
    `Extension modules: ${'torch._C, '.repeat(600)}`,
  ])
    journal.captureLine(line);
  journal.record(3221225477, null, 100, []);
  const kept = new CrashJournal(path, '1').latest()!.logTail.join('\n');
  expect(kept).toContain('[Previous line repeated 299 more times]');
  expect(kept).toContain('backend/services/model_manager.py", line 2901 in _load_model_sync');
  expect(kept).toContain('backend/services/asr_backend.py", line 812 in _transcribe_chunk');
});
