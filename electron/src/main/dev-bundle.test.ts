import {
  existsSync,
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { spawn as spawnChild } from 'node:child_process';
import { EventEmitter, once } from 'node:events';
import { pathToFileURL } from 'node:url';
import { basename, join, resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
// This is a Node launcher shared with the package script, so it intentionally
// remains plain ESM rather than being compiled into Electron's main process.
import {
  MAC_TOOLS,
  createMacDevBundlePlan,
  launchElectronVite,
  prepareMacDevElectron,
  prepareMacDevSession,
  pruneOldBundles,
  // @ts-expect-error JavaScript launcher has no separate declaration file.
} from '../../scripts/dev.mjs';
import { macWatchPollingPlugin } from '../../scripts/mac-watch-polling.mjs';

it('watches main and preload changes so renderer updates cannot leave stale browser IPC running', () => {
  const spawn = vi.fn(() => ({ on: vi.fn() }));
  const platform = Object.getOwnPropertyDescriptor(process, 'platform')!;
  Object.defineProperty(process, 'platform', { value: 'linux', configurable: true });
  try {
    launchElectronVite(['--', '--disable-gpu-compositing'], spawn, () => '/electron/dist/electron');
    expect(spawn).toHaveBeenCalledWith(
      process.execPath,
      [
        expect.stringContaining('electron-vite'),
        'dev',
        '--watch',
        '--',
        '--disable-gpu-compositing',
      ],
      expect.objectContaining({ stdio: 'inherit' }),
    );
  } finally {
    Object.defineProperty(process, 'platform', platform);
  }
});

describe('built-app launches (bun run start / preview)', () => {
  it('routes every Electron-launching package script through the resolving launcher', () => {
    const manifest = JSON.parse(readFileSync(join(__dirname, '../../package.json'), 'utf8')) as {
      scripts: Record<string, string>;
    };
    const { scripts } = manifest;
    const direct = Object.entries(scripts).filter(([, command]) =>
      /electron-vite\s+(dev|preview)\b/.test(command),
    );
    expect(direct).toEqual([]);
    expect(scripts.start).toBe('node scripts/dev.mjs preview');
    expect(scripts.preview).toBe('node scripts/dev.mjs preview');
  });

  it('previews the built app with a resolved binary and without watching', () => {
    const spawn = vi.fn(() => ({ on: vi.fn() }));
    const platform = Object.getOwnPropertyDescriptor(process, 'platform')!;
    const previousExecutable = process.env.ELECTRON_EXEC_PATH;
    const previousRunAsNode = process.env.ELECTRON_RUN_AS_NODE;
    delete process.env.ELECTRON_EXEC_PATH;
    process.env.ELECTRON_RUN_AS_NODE = '1';
    Object.defineProperty(process, 'platform', { value: 'linux', configurable: true });
    try {
      launchElectronVite(
        ['preview', '--', '--disable-gpu'],
        spawn,
        () => '/electron/dist/electron',
      );
      const [, args, options] = spawn.mock.calls[0] as unknown as [
        string,
        string[],
        { env: NodeJS.ProcessEnv },
      ];
      expect(args.slice(1)).toEqual(['preview', '--', '--disable-gpu']);
      expect(options.env.ELECTRON_EXEC_PATH).toBe('/electron/dist/electron');
      // An inherited ELECTRON_RUN_AS_NODE would start the app as plain Node.
      expect(options.env).not.toHaveProperty('ELECTRON_RUN_AS_NODE');
      expect(process.env.ELECTRON_RUN_AS_NODE).toBe('1');
    } finally {
      Object.defineProperty(process, 'platform', platform);
      if (previousExecutable === undefined) delete process.env.ELECTRON_EXEC_PATH;
      else process.env.ELECTRON_EXEC_PATH = previousExecutable;
      if (previousRunAsNode === undefined) delete process.env.ELECTRON_RUN_AS_NODE;
      else process.env.ELECTRON_RUN_AS_NODE = previousRunAsNode;
    }
  });
});

describe('Electron binary resolution', () => {
  it.each(['linux', 'win32'])('resolves Electron before launching on %s', (platformName) => {
    const events: string[] = [];
    const spawn = vi.fn(() => {
      events.push('launch');
      return { on: vi.fn() };
    });
    const resolveElectron = vi.fn(() => {
      events.push('resolve');
      return '/electron/dist/electron';
    });
    const platform = Object.getOwnPropertyDescriptor(process, 'platform')!;
    const previousExecutable = process.env.ELECTRON_EXEC_PATH;
    delete process.env.ELECTRON_EXEC_PATH;
    Object.defineProperty(process, 'platform', { value: platformName, configurable: true });
    try {
      launchElectronVite([], spawn, resolveElectron);
      expect(events).toEqual(['resolve', 'launch']);
      expect(spawn).toHaveBeenCalledWith(
        process.execPath,
        expect.any(Array),
        expect.objectContaining({
          env: expect.objectContaining({ ELECTRON_EXEC_PATH: '/electron/dist/electron' }),
        }),
      );
      expect(process.env.ELECTRON_EXEC_PATH).toBeUndefined();
    } finally {
      Object.defineProperty(process, 'platform', platform);
      if (previousExecutable === undefined) delete process.env.ELECTRON_EXEC_PATH;
      else process.env.ELECTRON_EXEC_PATH = previousExecutable;
    }
  });

  it('preserves an explicit executable override without downloading Electron', () => {
    const spawn = vi.fn(() => ({ on: vi.fn() }));
    const resolveElectron = vi.fn(() => {
      throw new Error('must not download');
    });
    const platform = Object.getOwnPropertyDescriptor(process, 'platform')!;
    const previousExecutable = process.env.ELECTRON_EXEC_PATH;
    process.env.ELECTRON_EXEC_PATH = '/custom/electron';
    Object.defineProperty(process, 'platform', { value: 'linux', configurable: true });
    try {
      launchElectronVite([], spawn, resolveElectron);
      expect(resolveElectron).not.toHaveBeenCalled();
      expect(spawn).toHaveBeenCalledWith(
        process.execPath,
        expect.any(Array),
        expect.objectContaining({
          env: expect.objectContaining({ ELECTRON_EXEC_PATH: '/custom/electron' }),
        }),
      );
    } finally {
      Object.defineProperty(process, 'platform', platform);
      if (previousExecutable === undefined) delete process.env.ELECTRON_EXEC_PATH;
      else process.env.ELECTRON_EXEC_PATH = previousExecutable;
    }
  });

  it('does not launch Vite when binary resolution fails', () => {
    const spawn = vi.fn(() => ({ on: vi.fn() }));
    const failure = new Error('Electron binary download failed');
    const resolveElectron = () => {
      throw failure;
    };
    const platform = Object.getOwnPropertyDescriptor(process, 'platform')!;
    const previousExecutable = process.env.ELECTRON_EXEC_PATH;
    delete process.env.ELECTRON_EXEC_PATH;
    Object.defineProperty(process, 'platform', { value: 'linux', configurable: true });
    try {
      expect(() => launchElectronVite([], spawn, resolveElectron)).toThrow(failure);
      expect(spawn).not.toHaveBeenCalled();
    } finally {
      Object.defineProperty(process, 'platform', platform);
      if (previousExecutable === undefined) delete process.env.ELECTRON_EXEC_PATH;
      else process.env.ELECTRON_EXEC_PATH = previousExecutable;
    }
  });
});

describe('macOS development bundle branding', () => {
  it('uses a VoiceStudio bundle while preserving Electron development detection', () => {
    const plan = createMacDevBundlePlan({
      electronExecutable: '/source/Electron.app/Contents/MacOS/Electron',
      electronVersion: '44.3.0',
      appVersion: '0.5.6',
      architecture: 'arm64',
      cacheFingerprint: '1024-123_5',
      cacheRoot: '/cache',
    });

    expect(plan.sourceBundle).toBe(resolve('/source/Electron.app'));
    expect(plan.destinationBundle).toBe(
      join(resolve('/cache'), '44.3.0-0.5.6-arm64-1024-123_5', 'VoiceStudio.app'),
    );
    expect(basename(plan.destinationExecutable)).toBe('Electron');
    expect(plan.destinationExecutable).toContain(
      join('VoiceStudio.app', 'Contents', 'MacOS', 'Electron'),
    );
  });
});

// cp/plutil/codesign are macOS tools; the fake copy materialises the bundle layout.
function fakeBundleCopy(command: string, args: string[]) {
  if (command !== MAC_TOOLS.cp) return;
  const target = args[args.length - 1];
  mkdirSync(join(target, 'Contents', 'MacOS'), { recursive: true });
  mkdirSync(join(target, 'Contents', 'Resources'), { recursive: true });
  writeFileSync(join(target, 'Contents', 'MacOS', 'Electron'), 'binary');
}

describe('macOS development bundle cache repair', () => {
  const fakeRun = fakeBundleCopy;

  it('replaces a cached bundle that lost its executable instead of failing with ENOTEMPTY', () => {
    const dir = mkdtempSync(join(tmpdir(), 'vs-dev-cache-'));
    try {
      const icon = join(dir, 'icon.icns');
      writeFileSync(icon, 'icon');
      const options = {
        electronExecutable: join(dir, 'src', 'Electron.app', 'Contents', 'MacOS', 'Electron'),
        electronVersion: '44.3.0',
        appVersion: '0.5.6',
        iconPath: icon,
        cacheRoot: join(dir, 'cache'),
        runCommand: fakeRun,
      };
      const executable = prepareMacDevElectron(options);
      expect(readFileSync(executable, 'utf8')).toBe('binary');

      rmSync(executable);
      writeFileSync(join(executable, '..', 'stale-leftover'), 'x');
      expect(existsSync(executable)).toBe(false);

      expect(prepareMacDevElectron(options)).toBe(executable);
      expect(readFileSync(executable, 'utf8')).toBe('binary');
      expect(existsSync(join(executable, '..', 'stale-leftover'))).toBe(false);
      // No staging directories are left behind.
      expect(
        readdirSync(join(dir, 'cache')).filter((name: string) => name.startsWith('.staging-')),
      ).toEqual([]);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe('macOS development bundle cache pruning', () => {
  it('keeps only the current bundle and leaves a concurrent staging directory alone', () => {
    const root = mkdtempSync(join(tmpdir(), 'vs-dev-prune-'));
    try {
      for (const name of ['old-1', 'old-2', 'current', '.staging-abc']) {
        mkdirSync(join(root, name));
      }
      pruneOldBundles(root, join(root, 'current'));
      expect(readdirSync(root).sort()).toEqual(['.staging-abc', 'current']);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});

describe.skipIf(process.platform !== 'darwin')('macOS cache cleanup safety', () => {
  it('preserves a bundle used by an already-running Electron instance', async () => {
    const root = mkdtempSync(join(tmpdir(), 'vs-dev-active-'));
    const executable = join(root, 'active', 'VoiceStudio.app', 'Contents', 'MacOS', 'Electron');
    mkdirSync(join(executable, '..'), { recursive: true });
    mkdirSync(join(root, 'current'));
    writeFileSync(executable, '');
    // ps lists argv[0]; a hard-linked node breaks when it loads a relative libnode (Homebrew).
    const child = spawnChild(
      process.execPath,
      ['-e', 'process.stdout.write("ready");setInterval(()=>{},1000)'],
      { argv0: executable },
    );
    try {
      await once(child.stdout!, 'data');
      pruneOldBundles(root, join(root, 'current'));
      expect(existsSync(executable)).toBe(true);
    } finally {
      const exited = once(child, 'exit');
      child.kill();
      await exited;
      rmSync(root, { recursive: true, force: true });
    }
  });

  it('does not block a usable cache when stale directories cannot be removed', () => {
    const root = mkdtempSync(join(tmpdir(), 'vs-dev-readonly-'));
    mkdirSync(join(root, 'old'));
    mkdirSync(join(root, 'current'));
    chmodSync(root, 0o555);
    try {
      expect(() => pruneOldBundles(root, join(root, 'current'))).not.toThrow();
      expect(existsSync(join(root, 'current'))).toBe(true);
      expect(existsSync(join(root, 'old'))).toBe(true);
    } finally {
      chmodSync(root, 0o755);
      rmSync(root, { recursive: true, force: true });
    }
  });
});

it('reports asynchronous Vite spawn failure and removes signal forwarding', () => {
  const child = new EventEmitter();
  const platform = Object.getOwnPropertyDescriptor(process, 'platform')!;
  const previousExitCode = process.exitCode;
  const previousSignals = ['SIGINT', 'SIGTERM', 'SIGHUP'].map((signal) =>
    process.listenerCount(signal),
  );
  const report = vi.spyOn(console, 'error').mockImplementation(() => {});
  Object.defineProperty(process, 'platform', { value: 'linux', configurable: true });
  try {
    launchElectronVite(
      [],
      () => child,
      () => '/electron/dist/electron',
    );
    child.emit('error', new Error('spawn EACCES'));
    expect(process.exitCode).toBe(1);
    expect(report).toHaveBeenCalledWith(expect.stringContaining('spawn EACCES'));
    expect(['SIGINT', 'SIGTERM', 'SIGHUP'].map((signal) => process.listenerCount(signal))).toEqual(
      previousSignals,
    );
  } finally {
    process.exitCode = previousExitCode;
    Object.defineProperty(process, 'platform', platform);
    report.mockRestore();
  }
});

describe.skipIf(process.platform !== 'darwin')('macOS bundle lifetime coordination', () => {
  const fakeRun = fakeBundleCopy;
  const fixture = () => {
    const root = mkdtempSync(join(tmpdir(), 'vs-dev-leases-'));
    const iconPath = join(root, 'icon.icns');
    writeFileSync(iconPath, 'icon');
    return {
      root,
      options: {
        iconPath,
        cacheRoot: join(root, 'cache'),
        leaseDirectory: join(root, 'leases'),
        electronExecutable: join(root, 'Electron.app', 'Contents', 'MacOS', 'Electron'),
        electronVersion: '44.3.0',
        appVersion: '1',
        runCommand: fakeRun,
      },
    };
  };
  const stop = async (child: ReturnType<typeof spawnChild>) => {
    if (child.exitCode !== null || child.signalCode !== null) return;
    const exited = once(child, 'exit');
    child.kill();
    await exited;
  };

  it('serializes different-key preparation and protects the gap before Electron starts', async () => {
    const { root, options } = fixture();
    const gate = join(root, 'continue');
    const moduleUrl = pathToFileURL(resolve('scripts/dev.mjs')).href;
    const workers: ReturnType<typeof spawnChild>[] = [];
    const start = (appVersion: string, paused: boolean) => {
      const messages: { phase: string; executable?: string }[] = [];
      const child = spawnChild(
        process.execPath,
        [
          '--input-type=module',
          '-e',
          `
        import { prepareMacDevSession } from ${JSON.stringify(moduleUrl)};
        import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
        import { join } from 'node:path';
        process.send({phase:'started'});
        const session = prepareMacDevSession({
          ...${JSON.stringify({ ...options, appVersion })},
          runCommand(command, args) {
            if(command !== ${JSON.stringify(MAC_TOOLS.cp)}) return;
            process.send({phase:'preparing'});
            if(${paused}) {
              const deadline = Date.now() + 10000;
              while(!existsSync(${JSON.stringify(gate)})) {
                if(Date.now() > deadline) throw Error('gate timeout');
                Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);
              }
            }
            const target = args.at(-1);
            mkdirSync(join(target,'Contents','MacOS'), {recursive:true});
            mkdirSync(join(target,'Contents','Resources'), {recursive:true});
            writeFileSync(join(target,'Contents','MacOS','Electron'), 'binary');
          }
        });
        process.send({phase:'ready',executable:session.executable});
        process.on('message', () => {session.release();process.exit(0);});
      `,
        ],
        { stdio: ['ignore', 'pipe', 'pipe', 'ipc'] },
      );
      child.on('message', (message) => messages.push(message as (typeof messages)[number]));
      workers.push(child);
      return { child, messages };
    };
    try {
      const first = start('1', true);
      await vi.waitFor(() =>
        expect(first.messages.some((m) => m.phase === 'preparing')).toBe(true),
      );
      const second = start('2', false);
      await vi.waitFor(() => expect(second.messages.some((m) => m.phase === 'started')).toBe(true));
      // A second process has reached preparation, but cannot enter the shared cache yet.
      await new Promise((done) => setTimeout(done, 150));
      expect(second.messages.map((m) => m.phase)).toEqual(['started']);
      writeFileSync(gate, 'go');
      await vi.waitFor(() => expect(first.messages.some((m) => m.phase === 'ready')).toBe(true));
      await vi.waitFor(() => expect(second.messages.some((m) => m.phase === 'ready')).toBe(true));
      const firstExecutable = first.messages.find((m) => m.phase === 'ready')!.executable!;
      const secondExecutable = second.messages.find((m) => m.phase === 'ready')!.executable!;
      expect(existsSync(firstExecutable)).toBe(true);
      expect(existsSync(secondExecutable)).toBe(true);
      // Neither process has spawned Electron: reservations alone must protect them.
      await stop(first.child);
      const third = prepareMacDevSession({ ...options, appVersion: '3' });
      try {
        expect(existsSync(firstExecutable)).toBe(false);
        expect(existsSync(secondExecutable)).toBe(true);
        expect(existsSync(third.executable)).toBe(true);
      } finally {
        third.release();
      }
    } finally {
      await Promise.all(workers.map(stop));
      rmSync(root, { recursive: true, force: true });
    }
  }, 15000);

  it('keeps the lease inherited by Vite after the wrapper releases its own descriptor', async () => {
    const { root, options } = fixture();
    const first = prepareMacDevSession(options);
    const child = spawnChild(
      process.execPath,
      ['-e', 'process.stdout.write("ready");setInterval(()=>{},1000)'],
      {
        stdio: ['ignore', 'pipe', 'pipe', first.fd],
      },
    );
    try {
      await once(child.stdout!, 'data');
      first.release();
      const second = prepareMacDevSession({ ...options, appVersion: '2' });
      try {
        expect(existsSync(first.executable)).toBe(true);
      } finally {
        second.release();
      }
      await stop(child);
      const third = prepareMacDevSession({ ...options, appVersion: '3' });
      try {
        expect(existsSync(first.executable)).toBe(false);
      } finally {
        third.release();
      }
    } finally {
      first.release();
      await stop(child);
      rmSync(root, { recursive: true, force: true });
    }
  });

  it('launches a valid read-only cache even when stale bundles cannot be pruned', () => {
    const { root, options } = fixture();
    const first = prepareMacDevSession(options);
    first.release();
    mkdirSync(join(options.cacheRoot, 'old'));
    chmodSync(options.cacheRoot, 0o555);
    try {
      const second = prepareMacDevSession(options);
      try {
        expect(second.executable).toBe(first.executable);
        expect(readFileSync(second.executable, 'utf8')).toBe('binary');
        expect(existsSync(join(options.cacheRoot, 'old'))).toBe(true);
      } finally {
        second.release();
      }
    } finally {
      chmodSync(options.cacheRoot, 0o755);
      rmSync(root, { recursive: true, force: true });
    }
  });
});

describe('macOS development bundle tools', () => {
  // Homebrew's GNU coreutils often shadow /bin on PATH; GNU cp rejects `-c`.
  it('runs the system macOS tools by absolute path, never a PATH lookup', () => {
    const dir = mkdtempSync(join(tmpdir(), 'vs-dev-tools-'));
    try {
      const icon = join(dir, 'icon.icns');
      writeFileSync(icon, 'icon');
      const commands = new Set<string>();
      prepareMacDevElectron({
        electronExecutable: join(dir, 'src', 'Electron.app', 'Contents', 'MacOS', 'Electron'),
        electronVersion: '44.3.0',
        appVersion: '0.5.6',
        iconPath: icon,
        cacheRoot: join(dir, 'cache'),
        runCommand: (command: string, args: string[]) => {
          commands.add(command);
          fakeBundleCopy(command, args);
        },
      });
      expect(commands).toEqual(new Set(['/bin/cp', '/usr/bin/plutil', '/usr/bin/codesign']));
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe('macOS watch builds', () => {
  // rolldown's native macOS watcher can end the first watch build without
  // writing it, so electron-vite never reaches closeBundle and never launches.
  const configFor = (platform: string, config: object) =>
    macWatchPollingPlugin(platform).config(config);

  it('polls for main and preload changes on macOS when watching', () => {
    expect(configFor('darwin', { build: { watch: {} } })).toEqual({
      build: { watch: { chokidar: { usePolling: true } } },
    });
  });

  it('leaves production builds and other platforms unchanged', () => {
    expect(configFor('darwin', { build: {} })).toBeUndefined();
    expect(configFor('darwin', { build: { watch: null } })).toBeUndefined();
    expect(configFor('linux', { build: { watch: {} } })).toBeUndefined();
    expect(configFor('win32', { build: { watch: {} } })).toBeUndefined();
  });

  it('is installed on both the main and preload builds', () => {
    const config = readFileSync(resolve(__dirname, '../../electron.vite.config.ts'), 'utf8');
    expect(config.match(/externalizeDepsPlugin\(\), macWatchPollingPlugin\(\)/g)).toHaveLength(2);
  });
});
