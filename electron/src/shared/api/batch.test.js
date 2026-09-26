/**
 * Contract test for the batch API client: /batch/enqueue receives uploaded
 * File BYTES only. Filesystem paths must never appear in the request — the
 * watch-folder feature (and everything else) rides the same guarantee.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const invoke = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({ invoke: (...args) => invoke(...args) }));

vi.mock('./client', () => ({
  ApiError: class ApiError extends Error {
    constructor(message, init = {}) {
      super(message);
      Object.assign(this, init);
    }
  },
  apiJson: vi.fn(),
  apiPost: vi.fn(async () => ({ job_id: 'j1', status: 'queued', queue_position: 0 })),
  apiDelete: vi.fn(),
  API: 'http://127.0.0.1:3900',
}));

import { apiPost } from './client';
import { enqueueBatchJob } from './batch';

describe('enqueueBatchJob', () => {
  beforeEach(() => vi.clearAllMocks());

  it('uploads the File itself — no filesystem path in any form field', async () => {
    const file = new File(['bytes'], 'clip.mp4', { type: 'video/mp4' });
    // Desktop-flavored File objects sometimes carry a path property; even if
    // one sneaks in, the client must not serialize it.
    Object.defineProperty(file, 'path', { value: '/Users/me/Watched/clip.mp4' });

    await enqueueBatchJob(file, ['es', 'fr'], 'voice-1', false);

    expect(apiPost).toHaveBeenCalledTimes(1);
    const [url, form] = apiPost.mock.calls[0];
    expect(url).toBe('/batch/enqueue');
    expect(form).toBeInstanceOf(FormData);

    expect([...form.keys()].sort()).toEqual(['langs', 'preserve_bg', 'video', 'voice_id']);
    expect(form.get('video')).toBeInstanceOf(File);
    expect(form.get('video').name).toBe('clip.mp4');
    expect(form.get('langs')).toBe('es,fr');
    expect(form.get('voice_id')).toBe('voice-1');
    expect(form.get('preserve_bg')).toBe('false');

    for (const [, value] of form.entries()) {
      if (typeof value === 'string') {
        expect(value).not.toContain('/Users/me');
      }
    }
  });

  it('streams native watch entries without constructing FormData in the renderer', async () => {
    const watched = {
      __voiceStudioNativeWatchUpload: /** @type {const} */ (true),
      token: 'a'.repeat(64),
      name: 'large.mkv',
      size: 8 * 1024 * 1024 * 1024,
      mtime: 1234,
    };
    invoke.mockResolvedValueOnce({
      status: 200,
      body: { job_id: 'native-1', status: 'queued', queue_position: 1 },
    });

    await expect(enqueueBatchJob(watched, ['es'], '', true)).resolves.toMatchObject({
      job_id: 'native-1',
    });
    expect(invoke).toHaveBeenCalledWith('watch_folder_enqueue', {
      token: watched.token,
      name: 'large.mkv',
      expectedSize: watched.size,
      expectedMtime: watched.mtime,
      langs: ['es'],
      voiceId: '',
      preserveBg: true,
    });
    expect(apiPost).not.toHaveBeenCalled();
  });

  it('preserves structured backend errors from native watch uploads', async () => {
    const detail = { error: 'asr_model_missing', recommended: { repo_id: 'model/repo' } };
    invoke.mockResolvedValueOnce({ status: 409, body: { detail } });
    const watched = {
      __voiceStudioNativeWatchUpload: /** @type {const} */ (true),
      token: 'b'.repeat(64),
      name: 'clip.mp4',
      size: 4,
      mtime: 1,
    };

    await expect(enqueueBatchJob(watched, ['es'])).rejects.toMatchObject({ status: 409, detail });
  });

  it('the batch client source never touches a path at all', () => {
    const source = fs.readFileSync(
      path.join(path.dirname(fileURLToPath(import.meta.url)), 'batch.ts'),
      'utf-8',
    );
    // No `path` identifier anywhere: not a form field, not a query param, not
    // a property read. (Regression guard for the watch-folder rule that the
    // backend only ever receives uploaded bytes.)
    expect(source).not.toMatch(/[^A-Za-z]path/i);
  });
});
