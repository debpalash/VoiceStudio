// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { execFile } from 'node:child_process';
import {
  RUNTIME_IMPORT_PROBE,
  RUNTIME_PROBE_TIMEOUT_MS,
  probeFailureDetail,
  runtimeDependenciesReady,
  runtimePython,
} from './runtime-project';
vi.mock('node:child_process', () => ({ execFile: vi.fn() }));
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});
it.each([null, new Error('No module named uvicorn'), new Error('ETIMEDOUT'), new Error('ENOENT')])(
  'validates imports using the selected interpreter and fails closed (%s)',
  async (error) => {
    vi.mocked(execFile).mockImplementation(((
      _command: unknown,
      _args: unknown,
      _options: unknown,
      callback: (error: Error | null) => void,
    ) => callback(error)) as never);
    const project = '/runtime with spaces';
    expect(await runtimeDependenciesReady(project)).toBe(error === null);
    expect(execFile).toHaveBeenCalledWith(
      runtimePython(project),
      ['-c', RUNTIME_IMPORT_PROBE],
      expect.objectContaining({
        cwd: project,
        timeout: RUNTIME_PROBE_TIMEOUT_MS,
        windowsHide: true,
        env: expect.objectContaining({
          HF_HUB_OFFLINE: '1',
          TRANSFORMERS_OFFLINE: '1',
          PYTHONNOUSERSITE: '1',
        }),
      }),
      expect.any(Function),
    );
  },
);

it('checks the matched native PyTorch stack before launching the backend', async () => {
  vi.mocked(execFile).mockImplementation(((
    _command: unknown,
    args: unknown,
    _options: unknown,
    callback: (error: Error | null) => void,
  ) => {
    callback(String(args).includes('torchaudio') ? new Error('missing libtorchaudio.pyd') : null);
  }) as never);

  expect(await runtimeDependenciesReady('/selected-runtime')).toBe(false);
  expect(vi.mocked(execFile).mock.calls[0]?.[1]).toEqual(['-c', RUNTIME_IMPORT_PROBE]);
});

it.each(['PYTHONPATH', 'PYTHONHOME'] as const)(
  'isolates imports from inherited %s',
  async (variable) => {
    vi.stubEnv(variable, '/unrelated-python');
    vi.mocked(execFile).mockImplementation(((
      _command: unknown,
      _args: unknown,
      options: { env: NodeJS.ProcessEnv },
      callback: (error: Error | null) => void,
    ) => {
      const contaminated = Boolean(options.env[variable]);
      callback(
        variable === 'PYTHONPATH'
          ? contaminated
            ? null
            : new Error('No module named uvicorn')
          : contaminated
            ? new Error('invalid Python home')
            : null,
      );
    }) as never);
    expect(await runtimeDependenciesReady('/selected-runtime')).toBe(variable === 'PYTHONHOME');
    expect(process.env[variable]).toBe('/unrelated-python');
  },
);

// #2445/#2465: a cold torch import on a slow disk or scanner-contended host can
// outlast any fixed ceiling. "Still importing" is not "broken" - declaring it
// broken sent intact runtimes back to the multi-GB setup screen.
it('trusts the runtime when the probe merely ran out of time', async () => {
  vi.mocked(execFile).mockImplementation(((
    _command: unknown,
    _args: unknown,
    _options: unknown,
    callback: (error: Error | null) => void,
  ) =>
    callback(
      Object.assign(new Error('Command failed'), { killed: true, signal: 'SIGTERM' }),
    )) as never);
  expect(await runtimeDependenciesReady('/slow-host-runtime')).toBe(true);
});

it('still rejects a probe that exited with a real import failure', async () => {
  vi.mocked(execFile).mockImplementation(((
    _command: unknown,
    _args: unknown,
    _options: unknown,
    callback: (error: Error | null) => void,
  ) =>
    callback(
      Object.assign(new Error('No module named torch'), { killed: false, code: 1, signal: null }),
    )) as never);
  expect(await runtimeDependenciesReady('/broken-runtime')).toBe(false);
});

it('gives a cold import far more than the old 30 s ceiling', () => {
  expect(RUNTIME_PROBE_TIMEOUT_MS).toBeGreaterThanOrEqual(120_000);
});

it('reports the final exception line of a failed probe and nothing for a ready one (#2555)', async () => {
  const stderr = [
    'Traceback (most recent call last):',
    '  File "<string>", line 1, in <module>',
    "ModuleNotFoundError: No module named 'sentencepiece'",
    '',
  ].join('\r\n');
  vi.mocked(execFile).mockImplementation(((
    _command: unknown,
    _args: unknown,
    _options: unknown,
    callback: (error: Error | null, stdout: string, stderr: string) => void,
  ) => callback(new Error('Command failed'), '', stderr)) as never);
  const failures: string[] = [];
  expect(await runtimeDependenciesReady('/broken', (detail) => failures.push(detail))).toBe(false);
  expect(failures).toEqual(["ModuleNotFoundError: No module named 'sentencepiece'"]);

  vi.mocked(execFile).mockImplementation(((
    _command: unknown,
    _args: unknown,
    _options: unknown,
    callback: (error: Error | null, stdout: string, stderr: string) => void,
  ) => callback(null, '', '')) as never);
  expect(await runtimeDependenciesReady('/ready', (detail) => failures.push(detail))).toBe(true);
  expect(failures).toHaveLength(1);
});

it('describes an interpreter that never started', () => {
  expect(
    probeFailureDetail(Object.assign(new Error('spawn x ENOENT'), { code: 'ENOENT' }), ''),
  ).toBe('the interpreter could not be started (ENOENT)');
  expect(probeFailureDetail(new Error('first\nsecond'), undefined)).toBe('first');
  expect(probeFailureDetail(null, `E: ${'x'.repeat(500)}`)).toHaveLength(300);
});
