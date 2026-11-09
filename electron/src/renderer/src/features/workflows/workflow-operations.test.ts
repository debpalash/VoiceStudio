import { beforeEach, expect, it, vi } from 'vitest';
const mock = vi.hoisted(() => ({
  json: vi.fn(),
  fetch: vi.fn(),
  generate: vi.fn(),
  convert: vi.fn(),
}));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.json, apiFetch: mock.fetch }));
vi.mock('@/lib/api/generate', () => ({ generateClone: mock.generate }));
vi.mock('@/lib/api/convert', () => ({ convertSpeech: mock.convert }));
import { workflowOperations } from './workflow-operations';
import { makeStep } from './workflow-model';

beforeEach(() => vi.resetAllMocks());
const signal = () => new AbortController().signal;

it('checks transcription readiness and disables optional LLM refinement', async () => {
  mock.json.mockResolvedValueOnce({ ready: true }).mockResolvedValueOnce({ text: 'Transcript' });
  const step = { ...makeStep('transcribe', { x: 0, y: 0 }), language: 'French' };
  expect(await workflowOperations.transcribe!(new Blob(['audio']), step, signal())).toBe(
    'Transcript',
  );
  const form = mock.json.mock.calls[1][1].body as FormData;
  expect(form.get('mode')).toBe('reference');
  expect(form.get('language')).toBeNull(); // This endpoint auto-detects language.
  expect(form.get('refine')).toBe('false');
});

it('does not transcribe when the required local model is unavailable', async () => {
  mock.json.mockResolvedValueOnce({ ready: false });
  await expect(
    workflowOperations.transcribe!(
      new Blob(['audio']),
      makeStep('transcribe', { x: 0, y: 0 }),
      signal(),
    ),
  ).rejects.toThrow('asr_required');
  expect(mock.json).toHaveBeenCalledTimes(1);
});

it('translates through an explicitly chosen local provider without LLM passes', async () => {
  mock.json.mockResolvedValue({ translated: [{ id: 'workflow', text: 'Bonjour' }] });
  const step = {
    ...makeStep('translate', { x: 0, y: 0 }),
    language: 'French',
    sourceLanguage: 'English',
    provider: 'nllb' as const,
  };
  expect(await workflowOperations.translate!('Hello', step, signal())).toBe('Bonjour');
  expect(JSON.parse(mock.json.mock.calls[0][1].body)).toMatchObject({
    provider: 'nllb',
    source_lang: 'en',
    target_lang: 'fr',
    quality: 'fast',
    auto_glossary: false,
    reflect: false,
    condense: false,
  });
});

it('sends a regional Portuguese choice as its tag and a base code as source (#3143)', async () => {
  mock.json.mockResolvedValue({ translated: [{ id: 'workflow', text: 'Olá' }] });
  const step = {
    ...makeStep('translate', { x: 0, y: 0 }),
    language: 'Portuguese (Portugal)',
    sourceLanguage: 'Portuguese (Brazil)',
    provider: 'argos' as const,
  };
  await workflowOperations.translate!('Hello', step, signal());
  expect(JSON.parse(mock.json.mock.calls[0][1].body)).toMatchObject({
    source_lang: 'pt',
    target_lang: 'pt-PT',
  });
});

it('treats per-segment translation failures as failures, not successful source text', async () => {
  mock.json.mockResolvedValue({
    translated: [{ id: 'workflow', text: 'Hello', error: 'Missing language pack' }],
  });
  await expect(
    workflowOperations.translate!('Hello', makeStep('translate', { x: 0, y: 0 }), signal()),
  ).rejects.toThrow('Missing language pack');
});

it('converts with the saved voice and retrieves the local audio artifact', async () => {
  mock.convert.mockResolvedValue({ audio_url: '/audio/converted.wav' });
  mock.fetch.mockResolvedValue(new Response('result'));
  const step = { ...makeStep('convert', { x: 0, y: 0 }), voiceId: 'voice-1' };
  const abort = signal();
  const result = await workflowOperations.convert!(new File(['source'], 'clip.mp3'), step, abort);
  expect(result.size).toBe(6);
  expect(mock.convert).toHaveBeenCalledWith(expect.any(File), 'voice-1', true, abort);
  expect(mock.fetch).toHaveBeenCalledWith('/audio/converted.wav', { signal: abort });
});

it('rejects a conversion result outside the local audio route', async () => {
  mock.convert.mockResolvedValue({ audio_url: 'https://example.invalid/audio' });
  await expect(
    workflowOperations.convert!(
      new Blob(['source']),
      { ...makeStep('convert', { x: 0, y: 0 }), voiceId: 'voice' },
      signal(),
    ),
  ).rejects.toThrow('incomplete');
  expect(mock.fetch).not.toHaveBeenCalled();
});
