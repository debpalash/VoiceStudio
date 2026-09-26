import { afterEach, describe, expect, it, vi } from 'vitest';

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock('@tauri-apps/api/core', () => ({ invoke }));

import { BROWSER_DICTATION_REQUEST, requestDictationCapture } from './dictationCapture';

afterEach(() => {
  delete window.__TAURI_INTERNALS__;
  invoke.mockReset();
});

describe('dictation capture controller', () => {
  it('routes desktop requests through the queued Tauri command', async () => {
    window.__TAURI_INTERNALS__ = {};
    invoke.mockResolvedValue(undefined);

    await requestDictationCapture('start');
    await requestDictationCapture('stop');

    expect(invoke).toHaveBeenNthCalledWith(1, 'request_dictation_capture', { action: 'start' });
    expect(invoke).toHaveBeenNthCalledWith(2, 'request_dictation_capture', { action: 'stop' });
  });

  it('routes browser requests to the mounted recorder', async () => {
    const handler = vi.fn();
    window.addEventListener(BROWSER_DICTATION_REQUEST, handler);
    await requestDictationCapture('start');
    window.removeEventListener(BROWSER_DICTATION_REQUEST, handler);

    expect(handler).toHaveBeenCalledOnce();
    expect(handler.mock.calls[0][0].detail).toEqual({ action: 'start' });
  });
});
