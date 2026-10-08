import { beforeEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api/client';
import {
  cancelDub,
  cleanupDubSegments,
  clearDubEditHistory,
  dubSession,
  mirrorDubSourceDelivery,
  translateDub,
  undoDubEdit,
  type DubSegment,
} from './dub-session';

vi.mock('@/lib/api/client', () => ({ apiJson: vi.fn() }));

const segment = (id: string, start: number, extra: Partial<DubSegment> = {}): DubSegment => ({
  id,
  start,
  end: start + 1,
  text: id,
  text_original: id,
  speaker_id: 'Speaker 1',
  ...extra,
});

function editing(segments: DubSegment[]) {
  dubSession.setState((current) => ({
    ...current,
    jobId: 'job',
    phase: 'editing',
    recovery: null,
    error: null,
    tracks: [],
    segments,
  }));
}

beforeEach(() => {
  vi.mocked(apiJson).mockReset();
  clearDubEditHistory();
});

it('fills empty directions from the source delivery without overwriting the user', async () => {
  editing([
    segment('a', 0),
    segment('b', 1, { direction: 'whispered' }),
    segment('c', 2, { sync_ratio: 1.1 }),
  ]);
  vi.mocked(apiJson).mockResolvedValueOnce({
    source: 'vocals',
    segments: [
      { id: 'a', direction: '', measured: true },
      { id: 'b', direction: 'urgent, quick', measured: true },
      { id: 'c', direction: 'calm, slow', measured: true },
    ],
  });

  await expect(mirrorDubSourceDelivery()).resolves.toEqual({
    applied: 1,
    measured: 3,
    source: 'vocals',
  });

  const [path, init] = vi.mocked(apiJson).mock.calls[0];
  expect(path).toBe('/dub/prosody-mirror/job');
  expect(JSON.parse(String(init?.body))).toEqual({
    segments: [
      { id: 'a', start: 0, end: 1, speaker_id: 'Speaker 1' },
      { id: 'b', start: 1, end: 2, speaker_id: 'Speaker 1' },
      { id: 'c', start: 2, end: 3, speaker_id: 'Speaker 1' },
    ],
  });
  const byId = new Map(dubSession.state.segments.map((row) => [row.id, row]));
  expect(byId.get('a')?.direction).toBeUndefined();
  expect(byId.get('b')?.direction).toBe('whispered');
  expect(byId.get('c')?.direction).toBe('calm, slow');
  expect(byId.get('c')?.sync_ratio).toBeUndefined();
  expect(dubSession.state.phase).toBe('editing');

  undoDubEdit();
  expect(dubSession.state.segments.find((row) => row.id === 'c')?.direction).toBeUndefined();
});

it('reports failure and leaves segments untouched when analysis fails', async () => {
  const before = [segment('a', 0)];
  editing(before);
  vi.mocked(apiJson).mockRejectedValueOnce(new Error('No audio track available'));

  await expect(mirrorDubSourceDelivery()).resolves.toBeNull();
  expect(dubSession.state.segments).toEqual(before);
  expect(dubSession.state.phase).toBe('editing');
  expect(dubSession.state.error).toBe('No audio track available');
});

it('keeps a finished dub finished when analysis fails', async () => {
  editing([segment('a', 0)]);
  dubSession.setState((current) => ({ ...current, phase: 'done', tracks: ['es'] }));
  vi.mocked(apiJson).mockRejectedValueOnce(new Error('No audio track available'));

  await expect(mirrorDubSourceDelivery()).resolves.toBeNull();
  expect(dubSession.state.phase).toBe('done');
});

it('leaves an edited dub in editing even though earlier tracks exist', async () => {
  editing([segment('a', 0), segment('b', 1), segment('c', 2)]);
  dubSession.setState((current) => ({ ...current, tracks: ['es'] }));
  vi.mocked(apiJson).mockResolvedValueOnce({
    source: 'vocals',
    segments: [{ id: 'a', direction: 'calm', measured: true }],
  });
  await mirrorDubSourceDelivery();
  expect(dubSession.state.phase).toBe('editing');

  vi.mocked(apiJson).mockRejectedValueOnce(new Error('No audio track available'));
  await mirrorDubSourceDelivery();
  expect(dubSession.state.phase).toBe('editing');
});

it('does nothing without a job', async () => {
  editing([segment('a', 0)]);
  dubSession.setState((current) => ({ ...current, jobId: null }));
  await expect(mirrorDubSourceDelivery()).resolves.toBeNull();
  expect(apiJson).not.toHaveBeenCalled();
});

it('applies the cleaned segments that Clean Up reports', async () => {
  editing([segment('a', 0), segment('b', 1)]);
  vi.mocked(apiJson).mockResolvedValueOnce({
    segments: [{ id: 'a', start: 0, end: 2, text: 'a b' }],
    before: 2,
    after: 1,
  });

  await expect(cleanupDubSegments()).resolves.toBe(1);
  const [, init] = vi.mocked(apiJson).mock.calls[0];
  expect(JSON.parse(String(init?.body)).segments.map((row: DubSegment) => row.id)).toEqual([
    'a',
    'b',
  ]);
  expect(dubSession.state.segments).toEqual([
    { id: 'a', start: 0, end: 2, text: 'a b', text_original: 'a b' },
  ]);
  expect(dubSession.state.phase).toBe('editing');
});

it('discards a Mirror response that arrives after the run was cancelled', async () => {
  editing([segment('a', 0)]);
  let settle: (value: unknown) => void = () => {};
  vi.mocked(apiJson).mockImplementation((path: string) =>
    path.startsWith('/dub/prosody-mirror/')
      ? new Promise((resolve) => {
          settle = resolve;
        })
      : Promise.resolve({}),
  );

  const pending = mirrorDubSourceDelivery();
  await Promise.resolve();
  await cancelDub();
  settle({ source: 'vocals', segments: [{ id: 'a', direction: 'calm', measured: true }] });

  await expect(pending).resolves.toBeNull();
  expect(dubSession.state.segments[0].direction).toBeUndefined();
});

it('sends each line\'s direction, mirrored or typed, with the translation request', async () => {
  editing([segment('a', 0, { direction: ' urgent, quick ' }), segment('b', 1)]);
  vi.mocked(apiJson).mockImplementation((path: string) =>
    Promise.resolve(path === '/dub/translate' ? { translated: [] } : []),
  );

  await translateDub('es', 'google');

  const call = vi.mocked(apiJson).mock.calls.find(([path]) => path === '/dub/translate');
  const body = JSON.parse(String(call?.[1]?.body));
  expect(body.segments.map((row: { direction?: string }) => row.direction)).toEqual([
    'urgent, quick',
    undefined,
  ]);
});
