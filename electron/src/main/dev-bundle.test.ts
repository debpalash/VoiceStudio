import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join, resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
// This is a Node launcher shared with the package script, so it intentionally
// remains plain ESM rather than being compiled into Electron's main process.
import {
  createMacDevBundlePlan,
  launchElectronVite,
  prepareMacDevElectron,
  // @ts-expect-error JavaScript launcher has no separate declaration file.
} from '../../scripts/dev.mjs';

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

describe('macOS development bundle cache repair', () => {
  // cp/plutil/codesign are macOS tools; the fake runner materialises the same layout.
  const fakeRun = (command: string, args: string[]) => {
    if (command !== 'cp') return;
    const target = args[args.length - 1];
    mkdirSync(join(target, 'Contents', 'MacOS'), { recursive: true });
    mkdirSync(join(target, 'Contents', 'Resources'), { recursive: true });
    writeFileSync(join(target, 'Contents', 'MacOS', 'Electron'), 'binary');
  };

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
