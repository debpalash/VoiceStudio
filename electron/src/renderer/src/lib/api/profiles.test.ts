import { afterEach, expect, it, vi } from 'vitest';
const mock = vi.hoisted(() => ({ json: vi.fn() }));
vi.mock('./client', () => ({ apiJson: mock.json, apiFetch: vi.fn() }));
import { createCloneProfile, replaceProfileAudio, updateProfileImage } from './profiles';
afterEach(() => vi.clearAllMocks());
it('preserves take seed zero and full transcript while sanitizing style', async () => {
  mock.json.mockResolvedValue({});
  const text =
    'A complete transcript longer than fifty characters must remain aligned with the full audio.';
  await createCloneProfile({
    name: 'Saved take',
    refAudio: new Blob(['audio']),
    refText: text,
    seed: 0,
    language: 'French',
    instruct: 'female, unsupported',
  });
  const form = mock.json.mock.calls[0][1].body as FormData;
  expect(form.get('seed')).toBe('0');
  expect(form.get('ref_text')).toBe(text);
  expect(form.get('language')).toBe('French');
  expect(form.get('instruct')).toBe('female');
});
it('does not invent a seed when saving an ordinary recording', async () => {
  mock.json.mockResolvedValue({});
  await createCloneProfile({ name: 'Recording', refAudio: new Blob(['audio']) });
  expect((mock.json.mock.calls[0][1].body as FormData).has('seed')).toBe(false);
});

it('updates profile images with the supported method and an encoded identifier', async () => {
  mock.json.mockResolvedValue({ id: 'voice/1' });
  const image = new File(['portrait'], 'portrait.png', { type: 'image/png' });

  await updateProfileImage('voice/1', image);

  expect(mock.json).toHaveBeenCalledWith(
    '/profiles/voice%2F1/image',
    expect.objectContaining({ method: 'PUT' }),
  );
  expect((mock.json.mock.calls[0][1].body as FormData).get('image')).toBe(image);
});

it('replaces a profile reference with a multipart PUT to the audio route', async () => {
  mock.json.mockResolvedValue({ id: 'voice/1' });
  const clip = new File(['audio'], 'take.webm', { type: 'audio/webm' });

  await replaceProfileAudio('voice/1', { refAudio: clip, refText: 'new words' });

  expect(mock.json).toHaveBeenCalledWith(
    '/profiles/voice%2F1/audio',
    expect.objectContaining({ method: 'PUT' }),
  );
  const form = mock.json.mock.calls[0][1].body as FormData;
  expect((form.get('ref_audio') as File).name).toBe('take.webm');
  expect(form.get('ref_text')).toBe('new words');
});

it('sends a blank transcript so the backend transcribes the new clip', async () => {
  mock.json.mockResolvedValue({});
  await replaceProfileAudio('v1', { refAudio: new Blob(['audio']) });
  const form = mock.json.mock.calls[0][1].body as FormData;
  expect(form.get('ref_text')).toBe('');
  expect((form.get('ref_audio') as File).name).toBe('reference.wav');
});

it('sends profile edits with the clip so they save atomically', async () => {
  mock.json.mockResolvedValue({});
  await replaceProfileAudio('v1', {
    refAudio: new Blob(['audio']),
    fields: { name: 'Crimson', instruct: 'male', language: 'French' },
  });
  const form = mock.json.mock.calls[0][1].body as FormData;
  expect(form.get('name')).toBe('Crimson');
  expect(form.get('instruct')).toBe('male');
  expect(form.get('language')).toBe('French');
  expect(form.get('personality')).toBeNull();
});
