import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { envWithToolPath, toolSearchDirs, toolSearchDirsAsync } from './tool-path';

describe('toolSearchDirs', () => {
  it('adds user-level install locations after the inherited PATH on macOS and Linux', () => {
    const dirs = toolSearchDirs({
      env: { PATH: ['/usr/bin', '/bin'].join(delimiter) },
      platform: 'darwin',
      home: '/home/u',
    });
    expect(dirs.slice(0, 2)).toEqual(['/usr/bin', '/bin']);
    expect(dirs).toEqual(
      expect.arrayContaining([
        join('/home/u', '.local', 'bin'),
        join('/home/u', '.cargo', 'bin'),
        '/opt/homebrew/bin',
        '/usr/local/bin',
        join('/home/u', '.npm-global', 'bin'),
      ]),
    );
  });

  it('adds the npm global folder on Windows and omits POSIX-only locations', () => {
    const dirs = toolSearchDirs({
      env: { Path: 'C:\\Windows', APPDATA: 'C:\\Users\\u\\AppData\\Roaming' },
      platform: 'win32',
      home: 'C:\\Users\\u',
    });
    expect(dirs).toContain(join('C:\\Users\\u\\AppData\\Roaming', 'npm'));
    expect(dirs).not.toContain('/opt/homebrew/bin');
    expect(new Set(dirs).size).toBe(dirs.length);
  });

  it('reuses one PATH key so Windows does not get duplicate spellings', () => {
    const env = envWithToolPath({
      env: { Path: 'C:\\Windows', APPDATA: 'C:\\A' },
      platform: 'win32',
      home: 'C:\\Users\\u',
    });
    expect(Object.keys(env).filter((key) => key.toUpperCase() === 'PATH')).toEqual(['Path']);
    expect(env.Path?.startsWith('C:\\Windows')).toBe(true);
    expect(env.Path).toContain('npm');
  });
});

describe('NVM discovery order', () => {
  it('prefers the newest numeric release and skips non-version entries', () => {
    const home = mkdtempSync(join(tmpdir(), 'vs-nvm-'));
    try {
      const root = join(home, '.nvm', 'versions', 'node');
      for (const name of ['v9.11.2', 'v22.9.0', 'v22.10.0', 'v24.1.0', 'v8.0.0', 'notes'])
        mkdirSync(join(root, name), { recursive: true });
      const dirs = toolSearchDirs({ env: { PATH: '/usr/bin' }, platform: 'linux', home });
      const nvm = dirs.filter((dir) => dir.startsWith(root));
      expect(nvm.map((dir) => dir.slice(root.length + 1).split(/[\\/]/)[0])).toEqual([
        'v24.1.0',
        'v22.10.0',
        'v22.9.0',
        'v9.11.2',
        'v8.0.0',
      ]);
      expect(dirs[0]).toBe('/usr/bin');
    } finally {
      rmSync(home, { recursive: true, force: true });
    }
  });
});

describe('toolSearchDirsAsync', () => {
  it('matches the sync result', async () => {
    const home = mkdtempSync(join(tmpdir(), 'vs-nvm-'));
    try {
      mkdirSync(join(home, '.nvm', 'versions', 'node', 'v22.1.0'), { recursive: true });
      const opts = { env: { PATH: '/usr/bin' }, platform: 'linux' as const, home };
      expect(await toolSearchDirsAsync(opts)).toEqual(toolSearchDirs(opts));
    } finally {
      rmSync(home, { recursive: true, force: true });
    }
  });

  it('never blocks the event loop on a stalled home directory', async () => {
    vi.useFakeTimers();
    try {
      const hang = () => new Promise<string[]>(() => {});
      let settled = false;
      void toolSearchDirsAsync({ platform: 'linux', home: '/stalled', readdirAsync: hang }).then(
        () => (settled = true),
      );
      let deadline = false;
      setTimeout(() => (deadline = true), 1500);
      await vi.advanceTimersByTimeAsync(1500);
      expect(deadline).toBe(true);
      expect(settled).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});
