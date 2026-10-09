import { beforeEach, expect, it, vi } from 'vitest';
import { loadTranscriptions } from '@shared/utils/transcriptionsStore';

const apiJson = vi.fn();
const toast = { success: vi.fn(), error: vi.fn() };
vi.mock('@/lib/api/client', () => ({
  apiJson: (...args: unknown[]) => apiJson(...args),
  describeError: (error: unknown) => (error instanceof Error ? error.message : String(error)),
}));
vi.mock('sonner', () => ({ toast }));

const job = await import('./transcription-job');
const audio = new File(['x'], 'talk.wav');

function respond(transcribe: () => Promise<unknown>) {
  apiJson.mockImplementation((path: string) => {
    if (path.startsWith('/dictation/readiness')) return Promise.resolve({ ready: true });
    if (path === '/api/settings/dictation-refinement') return Promise.resolve({ auto: false });
    return transcribe();
  });
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  job.setTranscriptionsViewing(false);
  job.clearTranscriptionFailure();
});

it('saves the result and toasts when the page was left mid-job', async () => {
  let finish!: (value: unknown) => void;
  respond(() => new Promise((resolve) => (finish = resolve)));
  const run = job.startTranscription(audio, 'fast');
  await vi.waitFor(() => expect(apiJson).toHaveBeenCalledWith('/transcribe', expect.anything()));
  // Page unmounts: nothing may abort the request.
  job.setTranscriptionsViewing(false);
  finish({ text: 'hello', language: 'en', segments: [] });
  expect(await run).toBe('saved');
  expect(loadTranscriptions()[0].text).toBe('hello');
  expect(job.transcriptionJob.state.savedId).toBe(loadTranscriptions()[0].id);
  expect(job.transcriptionJob.state.running).toBe(false);
  expect(toast.success).toHaveBeenCalledOnce();
});

it('does not toast when the page is visible', async () => {
  respond(() => Promise.resolve({ text: 'hi' }));
  job.setTranscriptionsViewing(true);
  expect(await job.startTranscription(audio, 'fast')).toBe('saved');
  expect(toast.success).not.toHaveBeenCalled();
});

it('cancel aborts the request and saves nothing', async () => {
  respond(
    () =>
      new Promise((_, reject) => {
        const signal = apiJson.mock.calls.at(-1)![1].signal as AbortSignal;
        signal.addEventListener('abort', () => reject(new Error('aborted')));
      }),
  );
  const run = job.startTranscription(audio, 'accurate');
  await vi.waitFor(() => expect(apiJson).toHaveBeenCalledWith('/transcribe', expect.anything()));
  job.cancelTranscription();
  expect(await run).toBe('cancelled');
  expect(loadTranscriptions()).toEqual([]);
  expect(job.transcriptionJob.state.error).toBeNull();
});

it('reports a failure while away and keeps it for the page', async () => {
  respond(() => Promise.reject(new Error('boom')));
  expect(await job.startTranscription(audio, 'fast')).toBe('failed');
  expect(job.transcriptionJob.state.error).toBe('boom');
  expect(toast.error).toHaveBeenCalledOnce();
});

it('refuses a second job while one is running', async () => {
  let finish!: (value: unknown) => void;
  respond(() => new Promise((resolve) => (finish = resolve)));
  const first = job.startTranscription(audio, 'fast');
  await vi.waitFor(() => expect(apiJson).toHaveBeenCalledWith('/transcribe', expect.anything()));
  expect(await job.startTranscription(audio, 'fast')).toBe('busy');
  finish({ text: 'x' });
  await first;
});
