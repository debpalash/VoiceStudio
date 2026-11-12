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
  resolveTorchVariant: () => ({ variant: 'cpu', explicit: false }),
  cpuTorchApplies: () => false,
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

it('quotes a bounded stderr tail, not only the last line, on an unexpected exit (#3887)', async () => {
  const supervisor = new BackendSupervisor();
  const { child, internal } = launch(supervisor);
  for (let i = 0; i < 60; i++) internal.pushLog('err', `trace line ${i} ${'x'.repeat(500)}`, true);

  child.emit('exit', 1, null);
  await vi.advanceTimersByTimeAsync(11_000);

  const message = supervisor.status.message ?? '';
  expect(supervisor.status.stage).toBe('crashed');
  expect(message).toContain('exit code 1');
  expect(message).toContain('trace line 59');
  expect(message).toContain('trace line 55');
  expect(message).not.toContain('trace line 30 ');
  expect(message.length).toBeLessThan(4000);
  await supervisor.shutdown();
});

/** macOS posix_spawn can time out while it checks the executable; Node throws a bare `spawn ETIMEDOUT` (#4035). */
function timedOutSpawn() {
  return Object.assign(new Error('spawn ETIMEDOUT'), { code: 'ETIMEDOUT', syscall: 'spawn' });
}

it('retries a launch the OS timed out, then starts the backend (#4035)', async () => {
  const supervisor = new BackendSupervisor();
  const internal = supervisor as unknown as SupervisorInternals;
  const child = deadChild();
  mocks.spawn.mockReset();
  mocks.spawn
    .mockImplementationOnce(() => {
      throw timedOutSpawn();
    })
    .mockReturnValueOnce(child);
  const waitUntilReady = vi
    .spyOn(supervisor as unknown as { waitUntilReady: () => Promise<void> }, 'waitUntilReady')
    .mockResolvedValue();
  internal.generation = 1;
  internal.spawnChild({ argv: ['python'], cwd: '/project' }, 1);
  expect(supervisor.status.stage).not.toBe('failed');
  await vi.advanceTimersByTimeAsync(2_000);
  expect(mocks.spawn).toHaveBeenCalledTimes(2);
  expect(supervisor.status.stage).toBe('starting');
  // start() skipped readiness after the first failure; the retry starts it (#4039 review).
  expect(waitUntilReady).toHaveBeenCalledWith(1, expect.any(Number));
});

it('says what to do when every launch attempt times out (#4035)', async () => {
  const supervisor = new BackendSupervisor();
  const internal = supervisor as unknown as SupervisorInternals;
  mocks.spawn.mockReset();
  mocks.spawn.mockImplementation(() => {
    throw timedOutSpawn();
  });
  internal.generation = 1;
  internal.spawnChild({ argv: ['/rt/.venv/bin/python'], cwd: '/project' }, 1);
  await vi.advanceTimersByTimeAsync(7_000);
  expect(mocks.spawn).toHaveBeenCalledTimes(3);
  expect(supervisor.status.stage).toBe('failed');
  expect(supervisor.status.message).toContain(
    'Could not start /rt/.venv/bin/python: spawn ETIMEDOUT',
  );
  expect(supervisor.status.message).toContain('restarting the computer');
});

it('drops a pending retry once a newer launch has started (#4035)', async () => {
  const supervisor = new BackendSupervisor();
  const internal = supervisor as unknown as SupervisorInternals;
  mocks.spawn.mockReset();
  mocks.spawn.mockImplementationOnce(() => {
    throw timedOutSpawn();
  });
  internal.generation = 1;
  internal.spawnChild({ argv: ['python'], cwd: '/project' }, 1);
  internal.generation = 2;
  await vi.advanceTimersByTimeAsync(7_000);
  expect(mocks.spawn).toHaveBeenCalledTimes(1);
});

it('retries an EAGAIN that Node reports through the error event (#4039 review)', async () => {
  const supervisor = new BackendSupervisor();
  const internal = supervisor as unknown as SupervisorInternals;
  const refused = deadChild() as unknown as EventEmitter & { pid?: number };
  refused.pid = undefined;
  const child = deadChild();
  mocks.spawn.mockReset();
  mocks.spawn.mockReturnValueOnce(refused).mockReturnValueOnce(child);
  const waitUntilReady = vi
    .spyOn(supervisor as unknown as { waitUntilReady: () => Promise<void> }, 'waitUntilReady')
    .mockResolvedValue();
  internal.generation = 1;
  internal.spawnChild({ argv: ['python'], cwd: '/project' }, 1);
  refused.emit('error', Object.assign(new Error('spawn python EAGAIN'), { code: 'EAGAIN' }));
  expect(supervisor.status.stage).toBe('starting');
  await vi.advanceTimersByTimeAsync(2_000);
  expect(mocks.spawn).toHaveBeenCalledTimes(2);
  expect(supervisor.status.stage).toBe('starting');
  // start()'s readiness wait is still running for this launch; no second one.
  expect(waitUntilReady).not.toHaveBeenCalled();
});

it('drops a pending retry when a backend answered during the delay (#4039 review)', async () => {
  const supervisor = new BackendSupervisor();
  const internal = supervisor as unknown as SupervisorInternals & {
    setStage(stage: string, patch: object): void;
  };
  const refused = deadChild() as unknown as EventEmitter & { pid?: number };
  refused.pid = undefined;
  mocks.spawn.mockReset();
  mocks.spawn.mockReturnValueOnce(refused);
  internal.generation = 1;
  internal.spawnChild({ argv: ['python'], cwd: '/project' }, 1);
  refused.emit('error', Object.assign(new Error('spawn python EAGAIN'), { code: 'EAGAIN' }));
  internal.setStage('ready', { managed: false });
  await vi.advanceTimersByTimeAsync(7_000);
  expect(mocks.spawn).toHaveBeenCalledTimes(1);
  expect(supervisor.status.stage).toBe('ready');
});

it('counts the startup budget from the retried launch, not the failed one (#4039 review)', async () => {
  const supervisor = new BackendSupervisor();
  const internal = supervisor as unknown as SupervisorInternals & {
    waitUntilReady(gen: number, budgetMs: number): Promise<void>;
  };
  const refused = () => {
    const child = deadChild() as unknown as EventEmitter & { pid?: number };
    child.pid = undefined;
    return child;
  };
  const first = refused();
  const second = refused();
  mocks.spawn.mockReset();
  mocks.spawn
    .mockReturnValueOnce(first)
    .mockReturnValueOnce(second)
    .mockReturnValueOnce(deadChild());
  internal.generation = 1;
  internal.spawnChild({ argv: ['python'], cwd: '/project' }, 1);
  void internal.waitUntilReady(1, 5_000); // what start() runs after a spawn
  first.emit('error', Object.assign(new Error('spawn python EAGAIN'), { code: 'EAGAIN' }));
  await vi.advanceTimersByTimeAsync(2_000);
  second.emit('error', Object.assign(new Error('spawn python EAGAIN'), { code: 'EAGAIN' }));
  await vi.advanceTimersByTimeAsync(5_500);
  // Both delays (7 s) outlast the 5 s budget; the third launch still gets its own.
  expect(mocks.spawn).toHaveBeenCalledTimes(3);
  expect(supervisor.status.stage).toBe('starting');
});
