// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { EventEmitter } from 'node:events';
import { browserCandidates, listInstalledBrowsers, openInBrowser } from './installed-browsers';

const mocks = vi.hoisted(() => ({ access: vi.fn(), spawn: vi.fn(), external: vi.fn() }));
vi.mock('node:fs/promises', () => ({ access: mocks.access }));
vi.mock('node:child_process', () => ({ spawn: mocks.spawn }));
vi.mock('electron', () => ({ shell: { openExternal: mocks.external } }));
beforeEach(() => {
  vi.clearAllMocks();
  mocks.access.mockRejectedValue(new Error('missing'));
});

afterEach(() => vi.unstubAllGlobals());

it('discovers native locations on all three platforms regardless of test host', () => {
  expect(
    browserCandidates(
      'win32',
      { LOCALAPPDATA: 'C:\\Users\\User\\AppData\\Local', ProgramFiles: 'C:\\Program Files' },
      'C:\\Users\\User',
    ),
  ).toContainEqual({
    id: 'edge',
    name: 'Microsoft Edge',
    path: 'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  });
  expect(browserCandidates('darwin', {}, '/Users/user')).toContainEqual({
    id: 'chrome',
    name: 'Google Chrome',
    path: '/Users/user/Applications/Google Chrome.app',
  });
  const linux = browserCandidates('linux', { PATH: '/usr/bin:.:relative:/snap/bin' }, '/home/user');
  expect(linux).toContainEqual({
    id: 'chromium',
    name: 'Chromium',
    path: '/snap/bin/chromium-browser',
  });
  expect(linux.every((candidate) => candidate.path.startsWith('/'))).toBe(true);
});

it('lists only installed browsers, deduplicates them, and hides executable paths', async () => {
  mocks.access.mockImplementation(async (path: string) => {
    if (!/google[\\/ -]chrome/i.test(path)) throw new Error('missing');
  });
  expect(await listInstalledBrowsers()).toEqual([{ id: 'chrome', name: 'Google Chrome' }]);
});

it('always supports the default browser and rejects arbitrary executables and schemes', async () => {
  await openInBrowser('default', 'https://voicestudio.sh');
  expect(mocks.external).toHaveBeenCalledWith('https://voicestudio.sh/');
  await expect(openInBrowser('cmd.exe', 'https://example.com')).rejects.toThrow();
  await expect(openInBrowser('default', 'file:///private')).rejects.toThrow();
  expect(mocks.spawn).not.toHaveBeenCalled();
});

it('passes URLs as a single argument to a detected browser without a command shell', async () => {
  vi.stubGlobal('process', Object.create(process, { platform: { value: 'linux' } }));
  mocks.access.mockImplementation(async (path: string) => {
    if (!/google[\\/ -]chrome/i.test(path)) throw new Error('missing');
  });
  mocks.spawn.mockImplementation(() => {
    const child = Object.assign(new EventEmitter(), { unref: vi.fn() });
    queueMicrotask(() => child.emit('spawn'));
    return child;
  });
  await openInBrowser('chrome', 'https://example.com/?q=$(unsafe)&a=2');
  const [, args, options] = mocks.spawn.mock.calls[0];
  expect(args.at(-1)).toBe('https://example.com/?q=$(unsafe)&a=2');
  expect(options.shell).not.toBe(true);
  expect(options.windowsHide).toBe(true);
});

it.each([
  [0, null],
  [1, null],
  [null, 'SIGTERM'],
] as const)('reports the macOS launch helper exit (%s, %s)', async (code, signal) => {
  vi.stubGlobal('process', Object.create(process, { platform: { value: 'darwin' } }));
  mocks.access.mockImplementation(async (path: string) => {
    if (!path.endsWith('/Google Chrome.app')) throw new Error('missing');
  });
  mocks.spawn.mockImplementation(() => {
    const child = Object.assign(new EventEmitter(), { unref: vi.fn() });
    queueMicrotask(() => {
      child.emit('spawn');
      child.emit('exit', code, signal);
    });
    return child;
  });
  const launch = openInBrowser('chrome', 'https://example.com');
  if (code === 0) await expect(launch).resolves.toBeUndefined();
  else await expect(launch).rejects.toThrow(/could not open/i);
  expect(mocks.spawn.mock.calls[0][0]).toBe('/usr/bin/open');
});
