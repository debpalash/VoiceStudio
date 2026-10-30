import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { BackendSupervisor } from './backend';
import type { VoiceStudioBridge } from '../preload/index.d';

const fixture = vi.hoisted(() => {
  const mainFrame = { url: 'app://voicestudio/index.html' };
  const webContents = { mainFrame };
  return {
    owner: { webContents, isMaximized: () => false },
    missingOwner: false,
    handlers: new Map<string, (event: unknown, raw?: unknown) => unknown>(),
    setPreference: vi.fn(),
    beginSetup: vi.fn(),
    invoke: vi.fn(),
    bridge: null as VoiceStudioBridge | null,
  };
});

vi.mock('electron', () => ({
  app: { getPath: () => '/unused-ipc-runtime-torch', exit: vi.fn() },
  BrowserWindow: { fromWebContents: () => fixture.owner, getAllWindows: () => [] },
  ipcMain: {
    handle: (channel: string, handler: (event: unknown, raw?: unknown) => unknown) =>
      fixture.handlers.set(channel, handler),
    on: vi.fn(),
  },
  contextBridge: {
    exposeInMainWorld: (_name: string, bridge: VoiceStudioBridge) => {
      fixture.bridge = bridge;
    },
  },
  ipcRenderer: { invoke: fixture.invoke, on: vi.fn(), send: vi.fn() },
  dialog: {},
  net: {},
  shell: {},
  systemPreferences: {},
}));
vi.mock('./site-browser', () => ({ registerSiteBrowser: vi.fn() }));

import { CHANNELS, registerIpc } from './ipc';
import '../preload/index';

const event = {
  sender: fixture.owner.webContents,
  senderFrame: fixture.owner.webContents.mainFrame,
};

beforeEach(() => {
  fixture.handlers.clear();
  fixture.missingOwner = false;
  fixture.owner.webContents.mainFrame.url = 'app://voicestudio/index.html';
  fixture.setPreference.mockReset().mockImplementation((preference) => preference);
  fixture.beginSetup.mockReset().mockResolvedValue(undefined);
  fixture.invoke.mockReset();
  registerIpc(
    {
      subscribe: () => () => {},
      setRuntimeTorchPreference: fixture.setPreference,
      beginRuntimeTorchSetup: fixture.beginSetup,
    } as unknown as BackendSupervisor,
    () => (fixture.missingOwner ? null : (fixture.owner as never)),
  );
});

afterEach(() => {
  vi.unstubAllEnvs();
});

it.each(['auto', 'cpu', 'cuda', 'rocm'] as const)(
  'forwards %s only through the trusted owner frame',
  async (preference) => {
    expect(
      await fixture.handlers.get(CHANNELS.backendSetRuntimeTorchPreference)!(event, preference),
    ).toBe(preference);
    expect(fixture.setPreference).toHaveBeenCalledExactlyOnceWith(preference);
    await fixture.handlers.get(CHANNELS.backendBeginRuntimeTorchSetup)!(event, preference);
    expect(fixture.beginSetup).toHaveBeenCalledExactlyOnceWith(preference);
  },
);

it.each([CHANNELS.backendSetRuntimeTorchPreference, CHANNELS.backendBeginRuntimeTorchSetup])(
  'rejects untrusted callers on %s before invoking the supervisor',
  async (channel) => {
    const handler = fixture.handlers.get(channel)!;
    const untrusted = [
      { sender: {}, senderFrame: event.senderFrame },
      { sender: event.sender, senderFrame: { url: event.senderFrame.url } },
      { sender: event.sender, senderFrame: null },
    ];
    for (const caller of untrusted)
      await expect(Promise.resolve().then(() => handler(caller, 'rocm'))).rejects.toThrow(
        'Untrusted main-frame request',
      );
    fixture.owner.webContents.mainFrame.url = 'https://attacker.example';
    await expect(Promise.resolve().then(() => handler(event, 'rocm'))).rejects.toThrow(
      'Untrusted main-frame request',
    );
    fixture.owner.webContents.mainFrame.url = 'app://voicestudio/index.html';
    fixture.missingOwner = true;
    await expect(Promise.resolve().then(() => handler(event, 'rocm'))).rejects.toThrow(
      'Untrusted main-frame request',
    );
    expect(fixture.setPreference).not.toHaveBeenCalled();
    expect(fixture.beginSetup).not.toHaveBeenCalled();
  },
);

it('serializes migration with preference writes and other migration requests', async () => {
  let finish!: () => void;
  fixture.beginSetup.mockImplementationOnce(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  const begin = fixture.handlers.get(CHANNELS.backendBeginRuntimeTorchSetup)!;
  const setPreference = fixture.handlers.get(CHANNELS.backendSetRuntimeTorchPreference)!;
  const pending = begin(event, 'rocm');
  expect(() => setPreference(event, 'cpu')).toThrow('maintenance_busy');
  await expect(begin(event, 'cpu')).rejects.toThrow('maintenance_busy');
  expect(fixture.beginSetup).toHaveBeenCalledOnce();
  finish();
  await pending;
  expect(setPreference(event, 'cpu')).toBe('cpu');
});

it('releases the IPC maintenance lock after a refused migration', async () => {
  fixture.beginSetup.mockRejectedValueOnce(new Error('runtime_torch_not_owned'));
  await expect(
    fixture.handlers.get(CHANNELS.backendBeginRuntimeTorchSetup)!(event, 'rocm'),
  ).rejects.toThrow('runtime_torch_not_owned');
  expect(fixture.handlers.get(CHANNELS.backendSetRuntimeTorchPreference)!(event, 'cpu')).toBe(
    'cpu',
  );
});

it('preserves supervisor validation and persistence errors', async () => {
  fixture.setPreference.mockImplementationOnce(() => {
    throw new Error('invalid_runtime_torch_preference');
  });
  expect(() =>
    fixture.handlers.get(CHANNELS.backendSetRuntimeTorchPreference)!(event, 'invalid'),
  ).toThrow('invalid_runtime_torch_preference');
  fixture.beginSetup.mockRejectedValueOnce(new Error('runtime_torch_pinned'));
  await expect(
    fixture.handlers.get(CHANNELS.backendBeginRuntimeTorchSetup)!(event, 'rocm'),
  ).rejects.toThrow('runtime_torch_pinned');
});

it('exposes typed preload promises on the exact IPC channels', async () => {
  fixture.invoke.mockResolvedValueOnce('rocm').mockResolvedValueOnce(undefined);
  await expect(fixture.bridge!.backend.setRuntimeTorchPreference('rocm')).resolves.toBe('rocm');
  await expect(fixture.bridge!.backend.beginRuntimeTorchSetup('cuda')).resolves.toBeUndefined();
  expect(fixture.invoke.mock.calls).toEqual([
    [CHANNELS.backendSetRuntimeTorchPreference, 'rocm'],
    [CHANNELS.backendBeginRuntimeTorchSetup, 'cuda'],
  ]);
});
