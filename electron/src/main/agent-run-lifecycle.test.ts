// @vitest-environment node
import { EventEmitter } from 'node:events';
import type { BackendSupervisor } from './backend';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AGENT_SCAN_TTL_MS, registerRepairAgents, REPAIR_CHANNELS } from './repair-agents';
const mock = vi.hoisted(() => ({
  handlers: new Map<string, (...args: any[]) => any>(),
  spawn: vi.fn(),
  execFile: vi.fn(),
  spawnSync: vi.fn(),
  bridge: vi.fn(),
  close: vi.fn(async () => {}),
  output: vi.fn(),
}));
vi.mock('electron', () => ({
  app: { getPath: () => '/temp', getAppPath: () => '/app' },
  BrowserWindow: {},
  dialog: {},
  ipcMain: {
    handle: (name: string, callback: (...args: any[]) => any) => mock.handlers.set(name, callback),
    removeHandler: (name: string) => mock.handlers.delete(name),
  },
}));
vi.mock('node:fs', () => ({
  existsSync: () => true,
  accessSync: () => {},
  constants: { W_OK: 2 },
  readFileSync: vi.fn(),
  mkdtempSync: vi.fn(),
  rmSync: vi.fn(),
  writeFileSync: vi.fn(),
}));
vi.mock('node:child_process', () => ({
  spawn: mock.spawn,
  execFile: mock.execFile,
  spawnSync: mock.spawnSync,
}));
type ProbeCallback = (error: Error | null, stdout: string, stderr: string) => void;
function answerProbes() {
  mock.execFile.mockImplementation(
    (_file: string, args: string[], _options: unknown, callback: ProbeCallback) => {
      // Only codex is installed; probes answer asynchronously like a real child.
      setTimeout(() =>
        args.includes('--version')
          ? callback(null, 'codex 1.2.3\n', '')
          : args[0] === 'codex'
            ? callback(
                null,
                process.platform === 'win32' ? 'C:\\agents\\codex.exe' : '/usr/bin/codex',
                '',
              )
            : callback(new Error('not found'), '', ''),
      );
      return { stdin: { end: vi.fn() } };
    },
  );
}
vi.mock('./repair-api-bridge', () => ({ startRepairApiBridge: mock.bridge }));
vi.mock('./llm-agent-bridge', () => ({
  startLlmAgentBridge: async () => ({ url: '', token: '', close() {} }),
}));
vi.mock('./window-safety', () => ({ sendToLiveWindow: mock.output }));
const frame = { url: 'app://voicestudio/index.html' };
const contents = { mainFrame: frame };
const owner = { webContents: contents };
const event = { sender: contents, senderFrame: frame };
const request = {
  agent: 'codex',
  mode: 'fix',
  workspace: 'app',
  report: 'Create a preview',
  context: '{}',
};
let dispose: (() => void) | undefined;
beforeEach(() => {
  vi.clearAllMocks();
  answerProbes();
  mock.bridge.mockResolvedValue({ contextFile: '/temp/isolated/context.json', close: mock.close });
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, text: async () => '{}' })),
  );
});
afterEach(() => {
  dispose?.();
  vi.unstubAllGlobals();
});
const start = () => mock.handlers.get(REPAIR_CHANNELS.start)!(event, request);
async function setup() {
  dispose = await registerRepairAgents(
    {
      baseUrl: 'http://localhost',
      requestHeaders: () => ({}),
      status: { stage: 'ready' },
    } as unknown as BackendSupervisor,
    '/source',
    () => owner as any,
  );
}

it('cancels during setup and rejects a concurrent request before launching a process', async () => {
  let release!: (value: unknown) => void;
  mock.bridge.mockReturnValue(
    new Promise((resolve) => {
      release = resolve;
    }),
  );
  await setup();
  const first = start();
  await vi.waitFor(() => expect(mock.bridge).toHaveBeenCalled());
  await expect(start()).rejects.toThrow('already running');
  const stopped = mock.handlers.get(REPAIR_CHANNELS.stop)!(event);
  expect(stopped.status).toBe('stopped');
  release({ contextFile: '/temp/isolated/context.json', close: mock.close });
  await first;
  expect(mock.spawn).not.toHaveBeenCalled();
  expect(mock.close).toHaveBeenCalled();
  expect(mock.handlers.get(REPAIR_CHANNELS.state)!(event).status).toBe('stopped');
});
it('launches app chat outside the checkout and closes its capability after streaming completion', async () => {
  const child = Object.assign(new EventEmitter(), {
    stdin: Object.assign(new EventEmitter(), { end: vi.fn() }),
    stdout: new EventEmitter(),
    stderr: new EventEmitter(),
    kill: vi.fn(),
  });
  mock.spawn.mockReturnValue(child);
  await setup();
  const result = await start();
  expect(mock.spawn.mock.calls[0][2].cwd.replaceAll('\\', '/')).toBe('/temp/isolated');
  expect(child.stdin.end).toHaveBeenCalledWith(
    expect.stringContaining('No source checkout is attached'),
  );
  child.stdout.emit('data', Buffer.from('Preview ready'));
  child.emit('close', 0);
  expect(mock.output).toHaveBeenCalledWith(
    owner,
    REPAIR_CHANNELS.event,
    expect.objectContaining({ sessionId: result.sessionId, type: 'output', text: 'Preview ready' }),
  );
  expect(mock.handlers.get(REPAIR_CHANNELS.state)!(event).status).toBe('complete');
  expect(mock.close).toHaveBeenCalled();
});

it('scans CLIs asynchronously and reuses the scan until the TTL or an explicit refresh', async () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  try {
    await setup();
    const list = (options?: { refresh?: boolean }) =>
      mock.handlers.get(REPAIR_CHANNELS.list)!(event, options);
    const pending = list();
    // The handler answers with a promise: the main process never blocks on a probe.
    expect(pending).toBeInstanceOf(Promise);
    const agents = await pending;
    expect(agents.find((agent: { id: string }) => agent.id === 'codex')).toMatchObject({
      available: true,
      version: 'codex 1.2.3',
    });
    const probes = mock.execFile.mock.calls.length;
    await list();
    expect(mock.execFile.mock.calls.length).toBe(probes);
    vi.setSystemTime(Date.now() + 2_000);
    await list({ refresh: true });
    expect(mock.execFile.mock.calls.length).toBeGreaterThan(probes);
    const refreshed = mock.execFile.mock.calls.length;
    vi.setSystemTime(Date.now() + AGENT_SCAN_TTL_MS);
    await list();
    expect(mock.execFile.mock.calls.length).toBeGreaterThan(refreshed);
    expect(mock.spawnSync).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
  }
});

/** Holds the next CLI lookup, so a launch stays suspended inside locate(). */
function holdLocate(): () => void {
  let answer: (() => void) | undefined;
  const answered = mock.execFile.getMockImplementation()!;
  mock.execFile.mockImplementationOnce(
    (file: string, args: string[], options: unknown, callback: ProbeCallback) => {
      answer = () => answered(file, args, options, callback);
      return { stdin: { end: vi.fn() } };
    },
  );
  return () => answer!();
}
const translation = {
  agent: 'codex',
  purpose: 'translate',
  targetLanguage: 'fr',
  segments: [{ id: 'a', sourceText: 'Hello', start: 0, end: 1 }],
};
const translate = () => mock.handlers.get(REPAIR_CHANNELS.translate)!(event, translation);

it('stopping while the repair CLI is located prevents the spawn', async () => {
  await setup();
  const release = holdLocate();
  const first = start();
  await vi.waitFor(() => expect(mock.execFile).toHaveBeenCalledTimes(5));
  // Pending before the await: a concurrent run is refused, a stop is honoured.
  await expect(start()).rejects.toThrow('already running');
  expect(mock.handlers.get(REPAIR_CHANNELS.stop)!(event).status).toBe('stopped');
  release();
  const { sessionId } = await first;
  expect(mock.bridge).not.toHaveBeenCalled();
  expect(mock.spawn).not.toHaveBeenCalled();
  expect(mock.handlers.get(REPAIR_CHANNELS.state)!(event)).toMatchObject({
    sessionId,
    status: 'stopped',
  });
});

it('stopping while the translation CLI is located prevents the spawn', async () => {
  await setup();
  const release = holdLocate();
  const pending = translate();
  await vi.waitFor(() => expect(mock.execFile).toHaveBeenCalledTimes(5));
  await expect(start()).rejects.toThrow('already running');
  mock.handlers.get(REPAIR_CHANNELS.stopTranslation)!(event);
  release();
  await expect(pending).rejects.toThrow('Agent translation was stopped');
  expect(mock.spawn).not.toHaveBeenCalled();
  // The cancelled launch is no longer pending, and a stale stop cancels nothing new.
  const again = holdLocate();
  const next = translate();
  await vi.waitFor(() => expect(mock.execFile).toHaveBeenCalledTimes(6));
  mock.handlers.get(REPAIR_CHANNELS.stopTranslation)!(event);
  again();
  await expect(next).rejects.toThrow('Agent translation was stopped');
  expect(mock.spawn).not.toHaveBeenCalled();
});

it('disposing while a CLI is located prevents both launches from spawning', async () => {
  await setup();
  let release = holdLocate();
  const repair = start();
  await vi.waitFor(() => expect(mock.execFile).toHaveBeenCalledTimes(5));
  dispose!();
  release();
  await repair;

  await setup();
  release = holdLocate();
  const pending = translate();
  await vi.waitFor(() => expect(mock.execFile).toHaveBeenCalledTimes(10));
  dispose!();
  dispose = undefined;
  release();
  await expect(pending).rejects.toThrow('Agent translation was stopped');
  expect(mock.bridge).not.toHaveBeenCalled();
  expect(mock.spawn).not.toHaveBeenCalled();
});
