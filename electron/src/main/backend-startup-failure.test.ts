// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { EventEmitter } from 'node:events';
import { join } from 'node:path';

const mocks = vi.hoisted(() => ({
  dependencies: vi.fn(async () => true),
  ready: vi.fn(async () => false),
  compatible: vi.fn(async () => false),
  interrupted: vi.fn(async () => false),
  install: vi.fn(async () => {}),
  promoteCaches: vi.fn(),
  stage: vi.fn(),
  spawn: vi.fn(),
}));

vi.mock('electron', () => ({ app: { isPackaged: true, getPath: () => '/private/voicestudio' } }));
vi.mock('node:child_process', () => ({ spawn: mocks.spawn, spawnSync: vi.fn() }));
vi.mock('node:fs/promises', () => ({ rm: vi.fn() }));
vi.mock('./runtime-project', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./runtime-project')>()),
  runtimeDependenciesReady: mocks.dependencies,
  runtimeReady: mocks.ready,
  runtimeCompatible: mocks.compatible,
  runtimeInstallInterrupted: mocks.interrupted,
  installRuntime: mocks.install,
  promoteLegacyRuntimeCaches: mocks.promoteCaches,
  stageRuntimeSources: mocks.stage,
  runtimePython: (root: string) => root + '/.venv/bin/python',
  MISSING_SYSTEM_LIBRARY_CODE: 'MISSING_SYSTEM_LIBRARY',
  OUTDATED_SYSTEM_LIBRARY_CODE: 'OUTDATED_SYSTEM_LIBRARY',
  RUNTIME_FILES_LOCKED_CODE: 'RUNTIME_FILES_LOCKED',
}));

import { BackendSupervisor } from './backend';

interface SupervisorInternals {
  generation: number;
  runtimeProject: string | null;
  spawnChild: (plan: { argv: string[]; cwd: string }, generation: number) => void;
  pushLog: (stream: 'out' | 'err', line: string, fromChild?: boolean) => void;
}

const FATAL_STARTUP =
  "FATAL: backend startup failed during 'ml_imports': Could not find module " +
  "'~\\AppData\\Roaming\\VoiceStudio\\runtime\\project\\.venv\\Lib\\site-packages\\torchaudio\\lib\\libtorchaudio.pyd' " +
  '(or one of its dependencies).';
/** The project this install created: the only kind a crash-time repair may rebuild. */
const OWN_PROJECT = join('/private/voicestudio', 'runtime', 'project');
const ACCESS_LOG = 'INFO: 127.0.0.1:50081 - "GET /health HTTP/1.1" 503 Service Unavailable';

/** A child that never answers, so the shell reports the crash instead of attaching. */
function deadChild() {
  return Object.assign(new EventEmitter(), {
    stdin: null,
    stdout: null,
    stderr: null,
    exitCode: null,
    signalCode: null,
    pid: 4242,
  });
}

function launch(supervisor: BackendSupervisor) {
  const internal = supervisor as unknown as SupervisorInternals;
  const child = deadChild();
  mocks.spawn.mockReturnValueOnce(child);
  internal.generation = 1;
  internal.spawnChild({ argv: ['python'], cwd: '/project' }, 1);
  return { child, internal };
}

/** Run as if on `platform`/`arch`, restoring the real host afterwards. */
function pinHost(platform: NodeJS.Platform, arch: NodeJS.Architecture) {
  const real = {
    platform: Object.getOwnPropertyDescriptor(process, 'platform')!,
    arch: Object.getOwnPropertyDescriptor(process, 'arch')!,
  };
  Object.defineProperty(process, 'platform', { value: platform, configurable: true });
  Object.defineProperty(process, 'arch', { value: arch, configurable: true });
  return () => {
    Object.defineProperty(process, 'platform', real.platform);
    Object.defineProperty(process, 'arch', real.arch);
  };
}
let restoreHost: () => void;

beforeEach(() => {
  // Intel Macs never install the local runtime (isUnsupportedPlatform), so a
  // repair test run on the macOS x64 release runner saw no repair. Pin a host
  // that supports it; the Intel case has its own test below.
  restoreHost = pinHost('linux', 'x64');
  vi.useFakeTimers();
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
});

afterEach(() => {
  restoreHost();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

it("quotes the backend's own startup verdict instead of the last access-log line", async () => {
  const supervisor = new BackendSupervisor();
  const { child, internal } = launch(supervisor);
  // uvicorn's access log goes to stdout; the verdict goes to stderr.
  internal.pushLog('out', ACCESS_LOG, true);
  internal.pushLog('err', FATAL_STARTUP, true);

  child.emit('exit', 1, null);
  await vi.advanceTimersByTimeAsync(11_000);

  expect(supervisor.status.stage).toBe('crashed');
  expect(supervisor.status.message).toContain('ml_imports');
  expect(supervisor.status.message).toContain('libtorchaudio.pyd');
  expect(supervisor.status.message).not.toContain('503 Service Unavailable');
  // The child was the shell's own: a report that says otherwise sends the
  // triage after the wrong owner.
  expect(supervisor.status.managed).toBe(true);
  await supervisor.shutdown();
});

it('repairs the managed runtime when the backend died importing its dependencies', async () => {
  const supervisor = new BackendSupervisor();
  const { child, internal } = launch(supervisor);
  internal.runtimeProject = OWN_PROJECT;
  internal.pushLog(
    'err',
    [
      'Traceback (most recent call last):',
      '  File "backend/main.py", line 671, in _phase_a_build_inner',
      '    import torchaudio',
      'FileNotFoundError: Could not find module',
      "'...\\torchaudio\\lib\\libtorchaudio.pyd' (or one of its dependencies).",
      FATAL_STARTUP,
    ].join('\n'),
    true,
  );

  child.emit('exit', 1, null);
  await vi.advanceTimersByTimeAsync(11_000);

  // A broken environment is repaired, not retried: the dead app becomes a
  // running repair instead of a dead end (#3058).
  expect(mocks.install).toHaveBeenCalled();
  expect(supervisor.status.stage).not.toBe('crashed');
  await supervisor.shutdown();
});

it('never starts a runtime repair on an Intel Mac, which cannot install one', async () => {
  restoreHost();
  restoreHost = pinHost('darwin', 'x64');
  const supervisor = new BackendSupervisor();
  const { child, internal } = launch(supervisor);
  internal.runtimeProject = OWN_PROJECT;
  internal.pushLog('err', ['    import torchaudio', FATAL_STARTUP].join('\n'), true);

  child.emit('exit', 1, null);
  await vi.advanceTimersByTimeAsync(11_000);

  expect(mocks.install).not.toHaveBeenCalled();
  await supervisor.shutdown();
});

it('leaves a crash alone when the backend died for an unrelated reason', async () => {
  const supervisor = new BackendSupervisor();
  const { child, internal } = launch(supervisor);
  internal.runtimeProject = '/project';
  internal.pushLog('err', 'RuntimeError: CUDA out of memory', true);

  child.emit('exit', 1, null);
  await vi.advanceTimersByTimeAsync(11_000);

  expect(mocks.install).not.toHaveBeenCalled();
  expect(supervisor.status.stage).toBe('crashed');
  await supervisor.shutdown();
});

it('never rebuilds a runtime project another install owns', async () => {
  const supervisor = new BackendSupervisor();
  const { child, internal } = launch(supervisor);
  // A legacy Tauri project adopted as the runtime: repairing it would run
  // `uv sync` inside files this install does not own.
  internal.runtimeProject = '/legacy-tauri/project';
  internal.pushLog('err', FATAL_STARTUP, true);

  child.emit('exit', 1, null);
  await vi.advanceTimersByTimeAsync(11_000);

  expect(mocks.install).not.toHaveBeenCalled();
  expect(supervisor.status.stage).toBe('crashed');
  await supervisor.shutdown();
});

it('forgets import errors once the backend has been healthy', async () => {
  const supervisor = new BackendSupervisor();
  const { child, internal } = launch(supervisor);
  internal.runtimeProject = OWN_PROJECT;
  internal.pushLog('err', FATAL_STARTUP, true);
  await (
    supervisor as unknown as {
      markReady: (gen: number, patch: object, supervise: boolean) => Promise<boolean>;
    }
  ).markReady(1, {}, false);

  // An unrelated exit later must not match the stale import error.
  child.emit('exit', 1, null);
  await vi.advanceTimersByTimeAsync(11_000);

  expect(mocks.install).not.toHaveBeenCalled();
  await supervisor.shutdown();
});

it('names a Windows load block instead of rebuilding a runtime that cannot help (#3209)', async () => {
  const supervisor = new BackendSupervisor();
  const { child, internal } = launch(supervisor);
  internal.runtimeProject = OWN_PROJECT;
  internal.pushLog(
    'err',
    [
      'Traceback (most recent call last):',
      '  File "backend/main.py", line 365, in <module>',
      '    import torch',
      'ImportError: DLL load failed while importing _C: An Application Control policy has blocked this file.',
    ].join('\n'),
    true,
  );

  child.emit('exit', 1, null);
  await vi.advanceTimersByTimeAsync(11_000);

  // torch is a core dependency, so this used to read as a broken environment
  // and download the whole runtime again, only to be blocked again.
  expect(mocks.install).not.toHaveBeenCalled();
  expect(supervisor.status.stage).toBe('crashed');
  expect(supervisor.status.message).toContain("Windows refused to load one of the engine's files");
  expect(supervisor.status.message).toContain('application control policy');
  await supervisor.shutdown();
});
