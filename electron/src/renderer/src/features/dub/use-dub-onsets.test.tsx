import { act, renderHook } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

const apiJson = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api/client', () => ({ apiJson }));
import { useDubOnsets } from './use-dub-onsets';

it('keeps the new waveform when an aborted request resolves later', async () => {
  let finishOld!: (result: { onsets: number[]; peaks: number[] }) => void;
  apiJson.mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve; }));
  apiJson.mockResolvedValueOnce({ onsets: [4], peaks: [0.7] });
  const { result, rerender } = renderHook(({ jobId }) => useDubOnsets(jobId, true), {
    initialProps: { jobId: 'old' },
  });

  await act(async () => { rerender({ jobId: 'new' }); });
  expect(result.current.peaks).toEqual([0.7]);
  await act(async () => { finishOld({ onsets: [1], peaks: [0.2] }); });
  expect(result.current.onsets).toEqual([4]);
  expect(result.current.peaks).toEqual([0.7]);
});
