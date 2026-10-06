import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { execFileSync, spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { basename, join, resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
// This is a Node launcher shared with the package script, so it intentionally
// remains plain ESM rather than being compiled into Electron's main process.
// @ts-expect-error JavaScript launcher has no separate declaration file.
import { createMacDevBundlePlan, launchElectronVite, prepareMacDevElectron } from '../../scripts/dev.mjs';

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


it.skipIf(process.platform !== 'darwin')('rebuilds an incomplete macOS development cache with native bundle tools', () => {
  const root = mkdtempSync(join(tmpdir(), 'vs-dev-cache-'));
  try {
    const contents = join(root, 'Electron.app', 'Contents');
    mkdirSync(join(contents, 'MacOS'), { recursive: true });
    mkdirSync(join(contents, 'Resources'));
    const executable = join(contents, 'MacOS', 'Electron');
    copyFileSync('/bin/echo', executable);
    writeFileSync(join(contents, 'Info.plist'), `<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>Electron</string>
<key>CFBundleIdentifier</key><string>org.example.vs-dev-cache</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>`);
    const iconPath = join(root, 'icon.icns');
    writeFileSync(iconPath, 'icns');
    const options = {
      electronExecutable: executable, electronVersion: 'test', appVersion: '1.0.0',
      iconPath, cacheRoot: join(root, 'cache'),
    };
    const cached = prepareMacDevElectron(options);
    const inode = statSync(cached).ino;
    expect(prepareMacDevElectron(options)).toBe(cached);
    expect(statSync(cached).ino).toBe(inode);
    rmSync(cached);
    const rebuilt = prepareMacDevElectron(options);
    expect(rebuilt).toBe(cached);
    expect(readFileSync(rebuilt).subarray(0, 4)).toEqual(readFileSync(executable).subarray(0, 4));
    expect(() => execFileSync('/usr/bin/codesign', ['--verify', '--deep', resolve(rebuilt, '../../..')])).not.toThrow();
    // A native copy failure must release custody so a corrected source can retry.
    rmSync(rebuilt);
    const source = join(root, 'Electron.app');
    const backup = join(root, 'Electron.backup');
    renameSync(source, backup);
    try {
      expect(() => prepareMacDevElectron(options)).toThrow('cp failed');
    } finally {
      renameSync(backup, source);
    }
    expect(prepareMacDevElectron(options)).toBe(cached);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

it.skipIf(process.platform !== 'darwin')('concurrent launchers retain the same published cache executable', async () => {
  const root = mkdtempSync(join(tmpdir(), 'vs-dev-cache-race-'));
  const children: ReturnType<typeof spawn>[] = [];
  const exits: Promise<void>[] = [];
  const release = join(root, 'release');
  try {
    const contents = join(root, 'Electron.app', 'Contents');
    mkdirSync(join(contents, 'MacOS'), { recursive: true });
    mkdirSync(join(contents, 'Resources'));
    const executable = join(contents, 'MacOS', 'Electron');
    copyFileSync('/bin/echo', executable);
    writeFileSync(join(contents, 'Info.plist'), `<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>Electron</string>
<key>CFBundleIdentifier</key><string>org.example.vs-dev-cache-race</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>`);
    const iconPath = join(root, 'icon.icns');
    writeFileSync(iconPath, 'icns');
    const options = {
      electronExecutable: executable, electronVersion: 'test', appVersion: '1.0.0',
      iconPath, cacheRoot: join(root, 'cache'),
    };
    const cached = prepareMacDevElectron(options);
    rmSync(cached);
    const destinationRoot = resolve(cached, '../../../..');
    const config = join(root, 'options.json');
    writeFileSync(config, JSON.stringify(options));
    const barrier = join(root, 'barrier');
    const attempt = join(root, 'attempt');
    const worker = join(root, 'worker.mjs');
    writeFileSync(worker, `import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
const [config, destinationRoot, barrier, release, result, attempt, launcher] = process.argv.slice(2);
if (barrier) {
  const remove = fs.rmSync;
  let paused = false;
  fs.rmSync = function(path, options) {
    if (path === destinationRoot && !paused) {
      paused = true;
      fs.writeFileSync(barrier, 'paused');
      const deadline = Date.now() + 5000;
      while (!fs.existsSync(release)) {
        if (Date.now() > deadline) throw new Error('Scheduling barrier timed out');
        Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);
      }
    }
    return remove(path, options);
  };
} else {
  const makeDirectory = fs.mkdirSync;
  const remove = fs.rmSync;
  let acknowledged = false;
  fs.mkdirSync = function(path, options) {
    try {
      return makeDirectory(path, options);
    } catch (error) {
      if (path === destinationRoot + '.lock' && error.code === 'EEXIST' && !acknowledged) {
        acknowledged = true;
        fs.writeFileSync(attempt, 'lock-contended');
      }
      throw error;
    }
  };
  fs.rmSync = function(path, options) {
    const result = remove(path, options);
    if (path === destinationRoot && !acknowledged) {
      acknowledged = true;
      fs.writeFileSync(attempt, 'retired-invalid');
    }
    return result;
  };
}
syncBuiltinESMExports();
const { prepareMacDevElectron } = await import(launcher);
const executable = prepareMacDevElectron(JSON.parse(fs.readFileSync(config, 'utf8')));
fs.writeFileSync(result, JSON.stringify({ executable, inode: fs.statSync(executable).ino }));
`);
    const resultA = join(root, 'result-a');
    const resultB = join(root, 'result-b');
    const launch = (pause: string, result: string) => {
      const child = spawn(process.execPath, [worker, config, destinationRoot, pause, release, result, attempt,
        fileURLToPath(new URL('../../scripts/dev.mjs', import.meta.url))]);
      children.push(child);
      let errors = '';
      child.stdout.resume();
      child.stderr.setEncoding('utf8').on('data', (data) => { errors += data; });
      const exit = new Promise<void>((resolve, reject) => {
        child.once('error', reject);
        child.once('close', (code) => code === 0 ? resolve() : reject(new Error(errors || `Child exited ${code}`)));
      });
      exit.catch(() => {});
      exits.push(exit);
      return exit;
    };
    const waitForFile = async (path: string, timeoutMs: number) => {
      const deadline = Date.now() + timeoutMs;
      while (!existsSync(path)) {
        if (Date.now() >= deadline) return false;
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      return true;
    };
    const first = launch(barrier, resultA);
    expect(await waitForFile(barrier, 2000)).toBe(true);
    const second = launch('', resultB);
    // Confirm B actually reached native mkdir contention or invalid retirement,
    // rather than assuming its process ran during an arbitrary sleep window.
    expect(await waitForFile(attempt, 5000)).toBe(true);
    const observed = readFileSync(attempt, 'utf8');
    expect(['lock-contended', 'retired-invalid']).toContain(observed);
    if (observed === 'retired-invalid') {
      // Before custody existed, B could publish while A was paused. Wait for
      // that actual publication before reproducing A's deletion of the winner.
      expect(await waitForFile(resultB, 5000)).toBe(true);
    } else {
      expect(existsSync(resultB)).toBe(false);
    }
    writeFileSync(release, 'continue');
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([Promise.all([first, second]), new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error('Concurrent launchers did not exit')), 5000);
      })]);
    } finally {
      clearTimeout(timer);
    }
    const a = JSON.parse(readFileSync(resultA, 'utf8'));
    const b = JSON.parse(readFileSync(resultB, 'utf8'));
    expect(a.executable).toBe(b.executable);
    expect(a.inode).toBe(b.inode);
    expect(statSync(a.executable).ino).toBe(a.inode);
    expect(() => execFileSync('/usr/bin/codesign', ['--verify', '--deep', resolve(a.executable, '../../..')])).not.toThrow();
  } finally {
    writeFileSync(release, 'continue');
    for (const child of children) child.kill('SIGKILL');
    await Promise.allSettled(exits);
    rmSync(root, { recursive: true, force: true });
  }
}, 15_000);
