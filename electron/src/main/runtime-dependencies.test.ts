// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { execFile } from 'node:child_process';
import { runtimeDependenciesReady, runtimePython } from './runtime-project';
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
      ['-c', 'import fastapi, uvicorn, omnivoice, faster_whisper, sentencepiece'],
      expect.objectContaining({
        cwd: project,
        timeout: 30_000,
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
