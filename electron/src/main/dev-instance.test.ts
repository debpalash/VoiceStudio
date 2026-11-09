import { describe, expect, it, vi } from 'vitest';
import {
  devElectronPids,
  devInstancePorts,
  replacePreviousDevInstance,
  signalProcess,
  stopLeftoverDevApps,
  // @ts-expect-error JavaScript launcher has no separate declaration file.
} from '../../scripts/dev.mjs';

describe('bun dev replaces an earlier dev instance', () => {
  it('clears only the dev proxy and renderer ports, never the backend port', () => {
    expect(devInstancePorts({})).toEqual([3903, 3902]);
    expect(
      devInstancePorts({ VOICESTUDIO_UI_PORT: '4100', VOICESTUDIO_ELECTRON_PROXY_PORT: '4200' }),
    ).toEqual([4200, 4100]);
    // An OS-assigned proxy port has nothing fixed to free.
    expect(devInstancePorts({ VOICESTUDIO_ELECTRON_PROXY_PORT: '0' })).toEqual([3902]);
  });

  it('asks a Windows dev app to close its windows before any forced stop', () => {
    const run = vi.fn();
    signalProcess(42, 'SIGTERM', 'win32', run);
    expect(run).toHaveBeenCalledWith('taskkill', ['/PID', '42'], { encoding: 'utf8' });
  });

  it('stops the old dev app before freeing the ports, unless multiple instances are allowed', async () => {
    const order: string[] = [];
    const clear = vi.fn(async () => void order.push('clear'));
    const stopApps = vi.fn(async () => void order.push('apps'));
    await replacePreviousDevInstance({}, clear, stopApps);
    expect(order).toEqual(['apps', 'clear']);
    await replacePreviousDevInstance(
      { VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES: '1' },
      clear,
      stopApps,
    );
    expect(clear).toHaveBeenCalledTimes(1);
  });

  it('matches only processes running from the dev Electron location', () => {
    const listing = [
      '  101 /repo/electron/node_modules/.cache/voicestudio-electron-dev/x/VoiceStudio.app/Contents/MacOS/Electron .',
      '  102 /Applications/VoiceStudio.app/Contents/MacOS/VoiceStudio',
      '  103 /repo/electron/node_modules/.cache/voicestudio-electron-dev-old/Electron',
      '  104 /usr/bin/node /repo/electron/node_modules/.cache/voicestudio-electron-dev/x',
    ].join('\n');
    expect(
      devElectronPids(
        listing,
        '/repo/electron/node_modules/.cache/voicestudio-electron-dev/',
        false,
      ),
    ).toEqual([101]);
    expect(
      devElectronPids(
        '4 C:\\Repo\\electron\\node_modules\\electron\\dist\\electron.exe\r\n5 C:\\Other\\electron.exe',
        'C:/Repo/electron/node_modules/electron/dist/',
        true,
      ),
    ).toEqual([4]);
  });

  it.each(['darwin', 'linux', 'win32'])(
    'asks a leftover dev app to quit and forces it only if it is still there on %s',
    async (platformName) => {
      const platform = Object.getOwnPropertyDescriptor(process, 'platform')!;
      Object.defineProperty(process, 'platform', { value: platformName, configurable: true });
      try {
        const location = platformName === 'win32' ? 'C:\\dev\\' : '/dev/';
        const executable = location + (platformName === 'win32' ? 'electron.exe' : 'Electron');
        let running = [7];
        const kill = vi.fn((pid: number, signal: string) => {
          if (signal === 'SIGKILL') running = running.filter((p) => p !== pid);
        });
        await stopLeftoverDevApps({
          location,
          list: () => running.map((pid) => `${pid} ${executable} .`).join('\n'),
          kill,
          sleep: async () => {},
          timeoutMs: 1000,
        });
        expect(kill.mock.calls).toEqual([
          [7, 'SIGTERM'],
          [7, 'SIGKILL'],
        ]);

        kill.mockClear();
        running = [8];
        await stopLeftoverDevApps({
          location,
          list: () => running.map((pid) => `${pid} ${executable} .`).join('\n'),
          kill: (pid: number, signal: string) => {
            kill(pid, signal);
            running = [];
          },
          sleep: async () => {},
        });
        expect(kill.mock.calls).toEqual([[8, 'SIGTERM']]);
      } finally {
        Object.defineProperty(process, 'platform', platform);
      }
    },
  );
});
