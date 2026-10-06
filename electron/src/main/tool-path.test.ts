import { delimiter, join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { envWithToolPath, toolSearchDirs } from './tool-path';

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

it('searches NVM releases by numeric version, keeping an inherited Node first', async () => {
  const { mkdtempSync, mkdirSync, rmSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const home = mkdtempSync(join(tmpdir(), 'voice-tool-path-'));
  try {
    for (const version of ['v9.11.2', 'v22.9.0', 'v22.10.0', 'v24.1.0'])
      mkdirSync(join(home, '.nvm', 'versions', 'node', version, 'bin'), { recursive: true });
    const dirs = toolSearchDirs({ env: { PATH: '/chosen/node/bin' }, platform: 'darwin', home });
    expect(dirs[0]).toBe('/chosen/node/bin');
    expect(dirs.filter((path) => path.includes('/.nvm/'))).toEqual(
      ['v24.1.0', 'v22.10.0', 'v22.9.0', 'v9.11.2'].map((version) =>
        join(home, '.nvm', 'versions', 'node', version, 'bin'),
      ),
    );
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});
