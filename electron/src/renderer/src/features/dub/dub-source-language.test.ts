import { afterEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api/client';
import { dubSession, translateDub, translateDubWithAgent } from './dub-session';

vi.mock('@/lib/api/client', async (load) => ({
  ...(await load<typeof import('@/lib/api/client')>()),
  apiJson: vi.fn(),
}));

afterEach(() => {
  delete (window as Partial<Window>).voicestudio;
  vi.resetAllMocks();
});

function sourceState(detected: string, selected?: string) {
  dubSession.setState((current) => ({
    ...current,
    jobId: 'source-language',
    phase: 'editing',
    recovery: null,
    sourceLang: detected,
    sourceLanguage: selected,
    quality: 'fast',
    segments: [{ id: 'a', start: 0, end: 2, text: 'Goedemorgen', text_original: 'Goedemorgen' }],
  }));
}

it.each([
  ['', 'nl', 'nl'],
  ['en', 'nl', 'nl'],
  ['en', undefined, 'en'],
  ['en', 'auto', 'en'],
  ['en', 'und', 'en'],
  ['en', ' ', 'en'],
])('sends selected source %s/%s before detected source', async (detected, selected, expected) => {
  sourceState(detected, selected);
  vi.mocked(apiJson)
    .mockResolvedValueOnce([])
    .mockResolvedValueOnce({
      translated: [{ id: 'a', text: 'Bonjour' }],
    });
  await expect(translateDub('fr', 'argos')).resolves.toBe(true);
  const request = vi.mocked(apiJson).mock.calls.find(([url]) => url === '/dub/translate');
  expect(JSON.parse(String(request?.[1]?.body)).source_lang).toBe(expected);
});

it('sends the explicit source choice to a local translation agent', async () => {
  sourceState('en', 'nl');
  const translate = vi
    .fn()
    .mockResolvedValue({ agent: 'codex', translations: [{ id: 'a', text: 'Bonjour' }] });
  Object.defineProperty(window, 'voicestudio', {
    configurable: true,
    value: { repair: { translate, stopTranslation: vi.fn(), onTranslationEvent: () => vi.fn() } },
  });
  vi.mocked(apiJson).mockResolvedValueOnce([]);
  await expect(translateDubWithAgent('fr', 'codex')).resolves.toBe(true);
  expect(translate).toHaveBeenCalledWith(expect.objectContaining({ sourceLanguage: 'nl' }));
});
