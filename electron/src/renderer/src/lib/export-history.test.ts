import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  bridge: null as object | null,
  saveBackendFile: vi.fn(),
  apiJson: vi.fn(),
}));
vi.mock('@/components/bridge', () => ({ getBridge: () => mocks.bridge }));
vi.mock('@/lib/native-save', () => ({ saveBackendFile: mocks.saveBackendFile }));
vi.mock('@/lib/api/client', () => ({ apiJson: mocks.apiJson }));
vi.mock('@/lib/query', () => ({ queryClient: { invalidateQueries: vi.fn() } }));

import { saveExport } from './export-history';

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  mocks.bridge = null;
});

it('downloads through the browser when there is no desktop bridge (#4222)', async () => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

  const saved = await saveExport('/api/audio/ab12cd34.mp3', 'take.mp3');

  expect(saved).toEqual({ canceled: false });
  expect(click).toHaveBeenCalledOnce();
  const link = click.mock.contexts[0] as HTMLAnchorElement;
  expect(link.getAttribute('href')).toBe('/api/audio/ab12cd34.mp3');
  expect(link.download).toBe('take.mp3');
  expect(mocks.saveBackendFile).not.toHaveBeenCalled();
  expect(mocks.apiJson).not.toHaveBeenCalled();
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
