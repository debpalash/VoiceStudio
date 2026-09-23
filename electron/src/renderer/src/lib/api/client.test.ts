import i18next from 'i18next';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  _resetBackendContactForTests,
  lastBackendContact,
} from '../../../../../../frontend/src/utils/backendContact';
import {
  ApiError,
  apiFetch,
  apiJson,
  audioUrl,
  describeError,
  errorFromResponse,
  profileAudioUrl,
} from './client';

beforeEach(() => {
  _resetBackendContactForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  _resetBackendContactForTests();
});

describe('errorFromResponse', () => {
  it('localizes Argos runtime errors and retains diagnostics', async () => {
    const translate = vi.spyOn(i18next, 't').mockReturnValue('Localized recovery guidance');
    const detail = { code: 'argos_runtime_unavailable', message: 'Raw native diagnostic' };
    const err = await errorFromResponse(new Response(JSON.stringify({ detail }), { status: 400 }));
    expect(err.detail).toBe('Localized recovery guidance');
    expect(translate).toHaveBeenCalledWith('engines.argosRuntimeUnavailable');
    expect(err.payload?.detail).toEqual(detail);
  });
  it('localizes background preservation errors and retains diagnostics', async () => {
    const detail = { code: 'dub_background_unavailable', message: 'Raw diagnostic' };
    const err = await errorFromResponse(new Response(JSON.stringify({ detail }), { status: 409 }));
    expect(err.detail).not.toBe('Raw diagnostic');
    expect(err.detail).not.toContain('Raw diagnostic');
    expect(err.payload?.detail).toEqual(detail);
  });
  it('uses a string detail verbatim and keeps the payload', async () => {
    const res = new Response(JSON.stringify({ detail: 'Unsupported instruct items' }), {
      status: 400,
    });
    const err = await errorFromResponse(res);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
    expect(err.detail).toBe('Unsupported instruct items');
    expect(err.message).toBe('Unsupported instruct items');
    expect(err.payload).toEqual({ detail: 'Unsupported instruct items' });
  });

  it('compact-stringifies a structured detail', async () => {
    const detail = { error: 'model_not_downloaded', repo_ids: ['a/b'] };
    const res = new Response(JSON.stringify({ detail }), { status: 409 });
    const err = await errorFromResponse(res);
    expect(err.detail).toBe(JSON.stringify(detail));
    expect(err.payload?.detail).toEqual(detail);
  });

  it('falls back to the text body when it is not JSON', async () => {
    const res = new Response('Internal Server Error', { status: 500 });
    const err = await errorFromResponse(res);
    expect(err.detail).toBe('Internal Server Error');
    expect(err.payload).toBeNull();
  });

  it('falls back to the status line on an empty body', async () => {
    const res = new Response(null, { status: 502, statusText: 'Bad Gateway' });
    const err = await errorFromResponse(res);
    expect(err.detail).toBe('HTTP 502 Bad Gateway');
  });
});

describe('apiFetch', () => {
  it('prefixes API_BASE and returns the response when ok', async () => {
    const fetchMock = vi.fn(async () => new Response('{"ok":true}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const body = await apiJson<{ ok: boolean }>('/system/info');
    expect(body).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledWith('/api/system/info', undefined);
    expect(lastBackendContact()).not.toBeNull();
  });

  it('throws ApiError for a non-2xx response', async () => {
    vi.stubGlobal('fetch', async () => new Response('{"detail":"nope"}', { status: 404 }));
    await expect(apiFetch('/profiles/x')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      detail: 'nope',
    });
    expect(lastBackendContact()).not.toBeNull();
  });

  it('maps a network failure to status 0 with the backend_unreachable key', async () => {
    vi.stubGlobal('fetch', async () => {
      throw new TypeError('Failed to fetch');
    });
    await expect(apiFetch('/engines')).rejects.toMatchObject({
      name: 'ApiError',
      status: 0,
      detail: 'tts_errors.backend_unreachable',
    });
    expect(lastBackendContact()).toBeNull();
  });

  it('re-throws aborts untouched', async () => {
    vi.stubGlobal('fetch', async () => {
      throw new DOMException('Aborted', 'AbortError');
    });
    await expect(apiFetch('/generate')).rejects.toMatchObject({ name: 'AbortError' });
  });
});

describe('url helpers', () => {
  it('builds same-origin audio URLs', () => {
    expect(audioUrl('take 1.wav')).toBe('/api/audio/take%201.wav');
    expect(profileAudioUrl('abc')).toBe('/api/profiles/abc/audio');
    expect(profileAudioUrl('abc', null)).toBe('/api/profiles/abc/audio');
    // The versioned URL changes when the reference is replaced (#2282).
    expect(profileAudioUrl('abc', '/profiles/abc/audio?v=42')).toBe('/api/profiles/abc/audio?v=42');
  });

  it('describes errors for toasts', () => {
    expect(describeError(new ApiError(500, 'boom'))).toBe('boom');
    expect(describeError(new Error('plain'))).toBe('plain');
    expect(describeError('str')).toBe('str');
  });
});

describe('JSON request headers', () => {
  it('labels serialized JSON bodies while preserving caller headers', async () => {
    const fetchMock = vi.fn(
      async (_url: RequestInfo | URL, _init?: RequestInit) => new Response('{}'),
    );
    vi.stubGlobal('fetch', fetchMock);
    await apiJson('/audiobook/plan', {
      method: 'POST',
      body: JSON.stringify({ text: 'Chapter' }),
      headers: { 'X-Test': 'preserved' },
    });
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get('content-type')).toBe('application/json');
    expect(headers.get('x-test')).toBe('preserved');
  });
  it('leaves multipart boundaries and explicit content types to the caller', async () => {
    const fetchMock = vi.fn(
      async (_url: RequestInfo | URL, _init?: RequestInit) => new Response('{}'),
    );
    vi.stubGlobal('fetch', fetchMock);
    const body = new FormData();
    body.set('cover', new Blob(['image']), 'cover.png');
    await apiJson('/audiobook/cover', { method: 'POST', body });
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).has('content-type')).toBe(false);
    await apiJson('/custom', {
      method: 'POST',
      body: 'text',
      headers: { 'content-type': 'text/plain' },
    });
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get('content-type')).toBe('text/plain');
  });
});
it('uses a structured error message without losing recovery metadata', async () => {
  const detail = {
    error: 'asr_model_missing',
    message: 'Install the selected speech model.',
    recommended: { repo_id: 'test/model' },
  };
  const error = await errorFromResponse(new Response(JSON.stringify({ detail }), { status: 409 }));
  expect(error.message).toBe(detail.message);
  expect(error.payload?.detail).toEqual(detail);
});

it('localizes structured profile language failures', async () => {
  const translate = vi.spyOn(i18next, 't').mockReturnValue('Localized profile guidance');
  try {
    const detail = {
      code: 'profile_language_rejected',
      language: 'Persian',
      message: 'English fallback',
    };
    const error = await errorFromResponse(
      new Response(JSON.stringify({ detail }), { status: 400 }),
    );
    expect(error.detail).toBe('Localized profile guidance');
    expect(translate).toHaveBeenCalledWith('tts_errors.profile_language_rejected', {
      language: 'Persian',
    });
  } finally {
    translate.mockRestore();
  }
});

it('localizes a no-audio-track upload rejection (422)', async () => {
  const translate = vi.spyOn(i18next, 't').mockReturnValue('Localized no-audio guidance');
  try {
    const detail = {
      code: 'no_audio_track',
      docs_topic: 'NO_AUDIO_TRACK',
      message: 'This file has no audio track, so there is no speech to transcribe, dub or clone.',
      hint: 'English hint',
    };
    const error = await errorFromResponse(new Response(JSON.stringify({ detail }), { status: 422 }));
    expect(error.message).toBe('Localized no-audio guidance');
    expect(error.payload?.detail).toEqual(detail);
    expect(translate).toHaveBeenCalledWith('tts_errors.no_audio_track');
  } finally {
    translate.mockRestore();
  }
});

it.each([false, true])('localizes HTTP failure topics (nested: %s)', async (nested) => {
  const translate = vi.spyOn(i18next, 't').mockReturnValue('Localized recovery');
  try {
    const failure = { docs_topic: 'GPU_ARCH_UNSUPPORTED', detail: 'English fallback' };
    const payload = nested ? { detail: failure } : failure;
    const error = await errorFromResponse(new Response(JSON.stringify(payload), { status: 500 }));
    expect(error.message).toBe('Localized recovery');
    expect(error.payload).toEqual(payload);
    expect(translate).toHaveBeenCalledWith('tts_errors.gpu_arch_unsupported');
  } finally {
    translate.mockRestore();
  }
});
