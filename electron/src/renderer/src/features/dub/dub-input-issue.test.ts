import { beforeEach, expect, it, vi } from 'vitest';
import { ApiError, apiJson } from '@/lib/api/client';
import { consumeTaskStream } from '@/lib/api/event-stream';
import { dubInputIssue } from './dub-input-issue';
import { dubSession, resetDubSession, uploadDub } from './dub-session';

vi.mock('@/lib/api/client', async (load) => ({
  ...(await load<typeof import('@/lib/api/client')>()),
  apiJson: vi.fn(),
}));
vi.mock('@/lib/api/event-stream', async (load) => ({
  ...(await load<typeof import('@/lib/api/event-stream')>()),
  consumeTaskStream: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(apiJson).mockReset();
  vi.mocked(consumeTaskStream).mockReset();
  resetDubSession();
});

it('recognises input problems from task failures and upload rejections', () => {
  expect(dubInputIssue(new Error('x'), { docsTopic: 'NO_AUDIO_TRACK' })).toBe('no_audio');
  expect(dubInputIssue(new Error('x'), { docsTopic: 'INVALID_MEDIA_FILE' })).toBe('invalid_media');
  expect(
    dubInputIssue(
      new ApiError(422, 'No audio', { detail: { code: 'no_audio_track', docs_topic: 'X' } }),
    ),
  ).toBe('no_audio');
  expect(
    dubInputIssue(new ApiError(422, 'Bad file', { detail: { docs_topic: 'INVALID_MEDIA_FILE' } })),
  ).toBe('invalid_media');
  expect(dubInputIssue(new ApiError(415, "Unsupported video file type '.txt'"))).toBe(
    'unsupported',
  );
});

it('leaves real failures to the failure banner', () => {
  expect(dubInputIssue(new Error('GPU out of memory'), { docsTopic: 'GPU_OOM' })).toBeNull();
  expect(dubInputIssue(new ApiError(500, 'boom'))).toBeNull();
  expect(dubInputIssue('text')).toBeNull();
});

it('shows a file without an audio track as a fixable input, not a failure', async () => {
  vi.mocked(apiJson).mockResolvedValueOnce({ job_id: 'job', task_id: 'prep' });
  vi.mocked(consumeTaskStream).mockImplementationOnce(async (_path, emit) => {
    emit({
      type: 'error',
      reason: 'This file has no audio track',
      docs_topic: 'NO_AUDIO_TRACK',
      diagnostic: 'trace',
    });
  });
  await uploadDub(new File(['video'], 'Screen Recording.mov', { type: 'video/quicktime' }));
  expect(dubSession.state).toMatchObject({
    jobId: 'job',
    filename: 'Screen Recording.mov',
    phase: 'idle',
    inputIssue: 'no_audio',
    error: null,
    failure: null,
    recovery: null,
  });
});

it('an unsupported upload becomes an input issue', async () => {
  vi.mocked(apiJson).mockRejectedValueOnce(new ApiError(415, 'Unsupported video file type'));
  await uploadDub(new File(['x'], 'notes.txt'));
  expect(dubSession.state).toMatchObject({ inputIssue: 'unsupported', error: null });
});

it('an empty file is flagged without uploading it', async () => {
  await uploadDub(new File([], 'empty.mp4', { type: 'video/mp4' }));
  expect(apiJson).not.toHaveBeenCalled();
  expect(dubSession.state).toMatchObject({
    filename: 'empty.mp4',
    phase: 'idle',
    inputIssue: 'empty',
  });
});

it('a real preparation failure keeps the failure banner and its diagnostic', async () => {
  vi.mocked(apiJson).mockResolvedValueOnce({ job_id: 'job', task_id: 'prep' });
  vi.mocked(consumeTaskStream).mockImplementationOnce(async (_path, emit) => {
    emit({ type: 'error', reason: 'ffmpeg crashed', diagnostic: 'trace' });
  });
  await uploadDub(new File(['video'], 'clip.mp4', { type: 'video/mp4' }));
  expect(dubSession.state.inputIssue).toBeNull();
  expect(dubSession.state.error).toContain('ffmpeg crashed');
  expect(dubSession.state.failure?.diagnostic).toBe('trace');
});
