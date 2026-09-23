import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { useReferenceTranscript } from './use-reference-transcript';
import { apiJson, ApiError } from '@/lib/api/client';
import { cloneSettingsStore, setCloneSetting } from '@/lib/store/clone-settings';

vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  apiJson: vi.fn(),
}));
const api = vi.mocked(apiJson);
const file = new File(['audio'], 'voice.wav', { type: 'audio/wav' });
beforeEach(() => {
  api.mockReset();
  setCloneSetting('selectedProfileId', null);
  setCloneSetting('refText', '');
});

it('shows unavailable when the backend reports the selected ASR model is missing', async () => {
  api.mockRejectedValueOnce(
    new ApiError(409, 'ASR model missing', {
      detail: { error: 'asr_model_missing' },
    }),
  );
  const { result } = renderHook(() => useReferenceTranscript(file));
  await waitFor(() => expect(result.current.state).toBe('unavailable'));
  expect(api).toHaveBeenCalledTimes(1);
  expect(cloneSettingsStore.state.refText).toBe('');
});

it('uses selected capture settings and stores the raw transcript', async () => {
  api.mockResolvedValueOnce({ text: ' Hello there ' });
  const { result } = renderHook(() => useReferenceTranscript(file));
  await waitFor(() => expect(result.current.state).toBe('ready'));
  expect(cloneSettingsStore.state.refText).toBe('Hello there');
  const body = api.mock.calls[0][1]?.body as FormData;
  expect(body.get('audio')).toBe(file);
  expect(body.get('mode')).toBe('reference');
  expect(body.get('refine')).toBe('false');
});

it('does not overwrite edits made while ASR is working', async () => {
  let finish!: (value: unknown) => void;
  api.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { result } = renderHook(() => useReferenceTranscript(file));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
  setCloneSetting('refText', 'My correction');
  await act(async () => finish({ text: 'Machine result' }));
  expect(result.current.state).toBe('ready');
  expect(cloneSettingsStore.state.refText).toBe('My correction');
});

it('discards results after leaving the reference', async () => {
  let finish!: (value: unknown) => void;
  api.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { unmount } = renderHook(() => useReferenceTranscript(file));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
  unmount();
  await act(async () => finish({ text: 'Old result' }));
  expect(cloneSettingsStore.state.refText).toBe('');
});

it('keeps the replacement upload transcript when the previous request finishes late', async () => {
  let finishOld!: (value: unknown) => void;
  api.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishOld = resolve;
      }),
  );
  api.mockResolvedValueOnce({ text: 'Replacement transcript' });
  const { result, rerender } = renderHook(({ audio }) => useReferenceTranscript(audio), {
    initialProps: { audio: file },
  });
  await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
  const oldSignal = api.mock.calls[0][1]?.signal;
  rerender({ audio: new File(['replacement'], 'replacement.wav', { type: 'audio/wav' }) });
  await waitFor(() => expect(result.current.state).toBe('ready'));
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => finishOld({ text: 'Stale transcript' }));
  expect(cloneSettingsStore.state.refText).toBe('Replacement transcript');
});

it('does not replace a saved voice transcript when an upload request finishes', async () => {
  let finish!: (value: unknown) => void;
  api.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  renderHook(() => useReferenceTranscript(file));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
  // Even an empty saved transcript belongs to the newly selected identity.
  setCloneSetting('selectedProfileId', 'saved-voice');
  await act(async () => finish({ text: 'Previous upload transcript' }));
  expect(cloneSettingsStore.state.refText).toBe('');
});

it('leaves the transcript to an engine that picks its own passage (#2281)', async () => {
  const { result } = renderHook(() => useReferenceTranscript(file, { skip: true }));
  await waitFor(() => expect(result.current.state).toBe('idle'));
  expect(api).not.toHaveBeenCalled();
  expect(cloneSettingsStore.state.refText).toBe('');
});

it('withdraws its own transcript when the engine starts picking the passage itself', async () => {
  api.mockResolvedValueOnce({ text: 'Whole clip words' });
  const { result, rerender } = renderHook(({ skip }) => useReferenceTranscript(file, { skip }), {
    initialProps: { skip: false },
  });
  await waitFor(() => expect(result.current.state).toBe('ready'));
  expect(cloneSettingsStore.state.refText).toBe('Whole clip words');

  rerender({ skip: true });

  expect(cloneSettingsStore.state.refText).toBe('');
  expect(result.current.state).toBe('idle');
});

it('keeps a user-edited transcript when skip turns on later', async () => {
  api.mockResolvedValueOnce({ text: 'Whole clip words' });
  const { result, rerender } = renderHook(({ skip }) => useReferenceTranscript(file, { skip }), {
    initialProps: { skip: false },
  });
  await waitFor(() => expect(result.current.state).toBe('ready'));
  act(() => setCloneSetting('refText', 'My own words'));

  rerender({ skip: true });

  expect(cloneSettingsStore.state.refText).toBe('My own words');
});
