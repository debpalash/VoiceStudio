import { afterEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api/client';
import { argosPackInstall, argosPackStatus, buildArgosPackRequest } from './argos-packs';
import { dubSession, translateDub } from './dub-session';

vi.mock('@/lib/api/client', async (load) => ({
  ...(await load<typeof import('@/lib/api/client')>()),
  apiJson: vi.fn(),
}));

const TRANSLATE_PATH = '/dub/translate';
const STATUS_PATH = '/engines/translation/argos/packs/status';
const INSTALL_PATH = '/engines/translation/argos/packs/install';

afterEach(() => {
  delete (window as Partial<Window>).voicestudio;
  vi.resetAllMocks();
});

function sourceState(detected: string, selected?: string) {
  dubSession.setState((current) => ({
    ...current,
    jobId: 'pack-contract',
    phase: 'editing',
    recovery: null,
    sourceLang: detected,
    sourceLanguage: selected,
    quality: 'fast',
    segments: [{ id: 'a', start: 0, end: 2, text: 'Goedemorgen', text_original: 'Goedemorgen' }],
  }));
}

function requestBody(path: string): Record<string, unknown> | undefined {
  const call = vi.mocked(apiJson).mock.calls.find(([url]) => url === path);
  return call ? (JSON.parse(String(call[1]?.body)) as Record<string, unknown>) : undefined;
}

async function translatedSource(detected: string, selected?: string): Promise<unknown> {
  sourceState(detected, selected);
  vi.mocked(apiJson)
    .mockResolvedValueOnce([])
    .mockResolvedValueOnce({ translated: [{ id: 'a', text: 'Bonjour' }] });
  await expect(translateDub('fr', 'argos')).resolves.toBe(true);
  return requestBody(TRANSLATE_PATH)?.source_lang;
}

it('checks and installs the pack for the source the translation actually uses', async () => {
  const translated = await translatedSource('en', 'nl');
  expect(translated).toBe('nl');

  const request = buildArgosPackRequest(dubSession.state, ['fr']);
  expect(request.source_lang).toBe(translated);

  vi.mocked(apiJson).mockResolvedValue({ source_lang: 'nl', pairs: [] });
  await argosPackStatus(request);
  await argosPackInstall(request);

  const status = requestBody(STATUS_PATH);
  const install = requestBody(INSTALL_PATH);
  expect(status?.source_lang).toBe(translated);
  expect(install?.source_lang).toBe(translated);
  expect(status).toMatchObject({ target_langs: ['fr'], job_id: 'pack-contract' });
  expect(install).toMatchObject({ target_langs: ['fr'], job_id: 'pack-contract' });
});

it('follows the source language the user selected', async () => {
  const translated = await translatedSource('en', 'nl');
  const selected = buildArgosPackRequest(dubSession.state, ['fr']);
  expect(selected.source_lang).toBe(translated);

  sourceState('en', 'de');
  const reselected = buildArgosPackRequest(dubSession.state, ['fr']);
  expect(reselected.source_lang).toBe('de');
  expect(reselected.source_lang).not.toBe(selected.source_lang);
});

it.each([
  ['en', undefined],
  ['en', 'auto'],
  ['en', 'und'],
  ['en', ' '],
  ['auto', 'nl'],
  ['', 'nl'],
])(
  'resolves the same pack source as the translation for detected %s',
  async (detected, selected) => {
    const translated = await translatedSource(detected, selected);
    const request = buildArgosPackRequest(dubSession.state, ['fr']);
    expect(request.source_lang).toBe(translated);
  },
);

it('omits the pack source when neither the selection nor the detection is a language', async () => {
  const translated = await translatedSource('und', 'unknown');
  const request = buildArgosPackRequest(dubSession.state, ['fr']);
  expect(request.source_lang).toBeUndefined();
  expect(request.source_lang).toBe(translated);
});
