import i18next from 'i18next';
import { afterEach, expect, it, vi } from 'vitest';
import { encodeDownloadFailure } from '@shared/download-failure';

const mocks = vi.hoisted(() => ({ bridge: null as object | null }));
vi.mock('@/components/bridge', () => ({ getBridge: () => mocks.bridge }));

import { ApiError } from '@/lib/api/client';
import { saveBackendFile, saveNativeData } from './native-save';

afterEach(() => {
  vi.restoreAllMocks();
  mocks.bridge = null;
});

/** What `ipcRenderer.invoke` rejects with when the main handler throws. */
function ipcRejection(channel: string, message: string) {
  return new Error(`Error invoking remote method '${channel}': Error: ${message}`);
}

function rejectSave(body: string, status = 409) {
  const saveAudio = vi
    .fn()
    .mockRejectedValue(
      ipcRejection('files:saveAudio', encodeDownloadFailure({ status, statusText: '', body })),
    );
  mocks.bridge = { files: { saveAudio } };
}

it('shows localized recovery guidance instead of "HTTP 409" (#2616)', async () => {
  const translate = vi.spyOn(i18next, 't').mockReturnValue('Localized background guidance');
  rejectSave(
    JSON.stringify({ detail: { code: 'dub_background_unavailable', message: 'Raw diagnostic' } }),
  );
  const error = await saveBackendFile({ url: '/x', suggestedName: 'a.mp4' }).catch((e) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect(error.status).toBe(409);
  expect(error.message).toBe('Localized background guidance');
  expect(translate).toHaveBeenCalledWith('dubIntegrity.backgroundUnavailable');
});

it("surfaces the backend's own detail message", async () => {
  rejectSave(JSON.stringify({ detail: 'Job not found' }), 404);
  const error = await saveBackendFile({ url: '/x', suggestedName: 'a.mp4' }).catch((e) => e);
  expect(error).toMatchObject({ status: 404, message: 'Job not found' });
  expect(error.message).not.toContain('invoking remote method');
});

it('keeps the status as ApiError for callers that branch on it', async () => {
  rejectSave('', 503);
  const error = await saveBackendFile({ url: '/x', suggestedName: 'a.ovsvoice' }).catch((e) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect(error.status).toBe(503);
});

it('drops the IPC wrapper from failures that are not HTTP responses', async () => {
  mocks.bridge = {
    files: {
      saveData: vi
        .fn()
        .mockRejectedValue(ipcRejection('files:saveData', 'no space left on device')),
    },
  };
  await expect(saveNativeData({ data: new Uint8Array(), suggestedName: 'a.txt' })).rejects.toThrow(
    /^no space left on device$/,
  );
});

it('returns null without a desktop bridge', async () => {
  expect(await saveBackendFile({ url: '/x', suggestedName: 'a' })).toBeNull();
});
