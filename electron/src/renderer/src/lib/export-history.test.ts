import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  bridge: null as object | null,
  saveBackendFile: vi.fn(),
  apiJson: vi.fn(),
  apiFetch: vi.fn(),
  saveLocalFile: vi.fn(),
}));
vi.mock('@/components/bridge', () => ({ getBridge: () => mocks.bridge }));
vi.mock('@/lib/native-save', () => ({ saveBackendFile: mocks.saveBackendFile }));
vi.mock('@/lib/api/client', () => ({
  API_BASE: '/api',
  apiFetch: mocks.apiFetch,
  apiJson: mocks.apiJson,
}));
vi.mock('@/lib/local-export', () => ({ saveLocalFile: mocks.saveLocalFile }));
vi.mock('@/lib/query', () => ({ queryClient: { invalidateQueries: vi.fn() } }));

import { saveExport } from './export-history';

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  mocks.bridge = null;
});

it('downloads through the authenticated API client when there is no desktop bridge (#4222)', async () => {
  const blob = new Blob(['RIFF']);
  mocks.apiFetch.mockResolvedValue(new Response(blob));
  mocks.saveLocalFile.mockResolvedValue({ canceled: false });

  const saved = await saveExport('/api/audio/ab12cd34.mp3', 'take.mp3');

  expect(saved).toEqual({ canceled: false });
  // apiFetch adds the API base itself and attaches the share PIN / admin
  // bearer session; a plain link would send neither.
  expect(mocks.apiFetch).toHaveBeenCalledWith('/audio/ab12cd34.mp3');
  expect(mocks.saveLocalFile).toHaveBeenCalledWith(expect.anything(), 'take.mp3');
  expect(mocks.saveBackendFile).not.toHaveBeenCalled();
  expect(mocks.apiJson).not.toHaveBeenCalled();
});

it('surfaces a refused web download instead of saving an error page', async () => {
  const refusal = new Error('401');
  mocks.apiFetch.mockRejectedValue(refusal);

  await expect(saveExport('/api/audio/ab12cd34.wav', 'take.wav')).rejects.toBe(refusal);
  expect(mocks.saveLocalFile).not.toHaveBeenCalled();
});

it('saves natively and records the export in the desktop app', async () => {
  mocks.bridge = { files: {} };
  mocks.saveBackendFile.mockResolvedValue({ canceled: false, path: '/home/me/take.wav' });

  await saveExport('/api/audio/ab12cd34.wav', 'take.wav');

  expect(mocks.saveBackendFile).toHaveBeenCalledWith({
    url: '/api/audio/ab12cd34.wav',
    suggestedName: 'take.wav',
  });
  expect(mocks.apiJson).toHaveBeenCalledWith('/export/record', expect.anything());
});
