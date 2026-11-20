import { describe, expect, it, vi } from 'vitest';

const handlers = new Map<string, (...args: unknown[]) => unknown>();
const listeners = new Map<string, (...args: unknown[]) => unknown>();
let windowOf: (contents: unknown) => unknown = () => null;

vi.mock('electron', () => ({
  BrowserWindow: { fromWebContents: (contents: unknown) => windowOf(contents) },
  ipcMain: {
    handle: (channel: string, fn: (...args: unknown[]) => unknown) => handlers.set(channel, fn),
    on: (channel: string, fn: (...args: unknown[]) => unknown) => listeners.set(channel, fn),
  },
}));

import {
  assertTrustedMainFrame,
  assertTrustedTopLevel,
  handleTrusted,
  onTrusted,
  trustedOwner,
} from './trusted-ipc';

const TRUSTED_URL = 'app://voicestudio/index.html';

function fixture(frameUrl = TRUSTED_URL) {
  const mainFrame = { url: frameUrl };
  const webContents = { mainFrame };
  const owner = { webContents };
  const event = { sender: webContents, senderFrame: mainFrame };
  windowOf = (contents) => (contents === webContents ? owner : null);
  return { owner, webContents, mainFrame, event };
}

describe('trusted-ipc gate', () => {
  it('accepts the owner window main frame on the app origin', () => {
    const { owner, event } = fixture();
    expect(() => assertTrustedMainFrame(event as never, owner as never)).not.toThrow();
    expect(() => assertTrustedTopLevel(event as never)).not.toThrow();
    expect(trustedOwner(event as never, owner as never)).toBe(owner);
  });

  it('rejects a subframe, a foreign origin, another window and a missing owner', () => {
    const { owner, event } = fixture();
    const subframe = { ...event, senderFrame: { url: TRUSTED_URL } };
    expect(() => assertTrustedMainFrame(subframe as never, owner as never)).toThrow();
    expect(() => assertTrustedTopLevel(subframe as never)).toThrow();
    expect(trustedOwner(subframe as never, owner as never)).toBeNull();

    const foreign = fixture('https://evil.example/');
    expect(() => assertTrustedMainFrame(foreign.event as never, foreign.owner as never)).toThrow();
    expect(() => assertTrustedTopLevel(foreign.event as never)).toThrow();

    const other = fixture();
    expect(() => assertTrustedMainFrame(other.event as never, { webContents: {} } as never)).toThrow();
    expect(() => assertTrustedMainFrame(other.event as never, null)).toThrow(
      'Untrusted main-frame request',
    );
    expect(() => assertTrustedMainFrame(other.event as never, null, 'custom')).toThrow('custom');
  });

  it('handleTrusted and onTrusted run the handler only for a trusted sender', () => {
    const run = vi.fn(() => 'ok');
    handleTrusted('t:handle', run);
    onTrusted('t:on', run);

    const bad = fixture('https://evil.example/');
    expect(() => handlers.get('t:handle')!(bad.event)).toThrow();
    listeners.get('t:on')!(bad.event);
    expect(run).not.toHaveBeenCalled();

    const good = fixture();
    expect(handlers.get('t:handle')!(good.event)).toBe('ok');
    listeners.get('t:on')!(good.event);
    expect(run).toHaveBeenCalledTimes(2);
  });
});
