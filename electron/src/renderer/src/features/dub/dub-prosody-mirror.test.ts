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

vi.mock('@/lib/api/client', async (load) => ({
  ...(await load<typeof import('@/lib/api/client')>()),
  apiJson: vi.fn(),
}));

const seg = (id: string, start: number, extra: Partial<DubSegment> = {}): DubSegment => ({
  id,
  start,
  end: start + 1,
  text: `Line ${id}`,
  text_original: `Line ${id}`,
  ...extra,
});

function open(phase: 'editing' | 'done', segments: DubSegment[], jobId: string | null = 'job-1') {
  clearDubEditHistory();
  dubSession.setState((current) => ({
    ...current,
    jobId,
    phase,
    segments,
    recovery: null,
    error: null,
    taskId: null,
  }));
}

beforeEach(() => {
  vi.mocked(apiJson).mockReset();
});

const reply = (rows: Array<{ id: string; direction?: string; measured?: boolean }>) => ({
  source: 'vocals' as const,
  segments: rows.map((r) => ({ direction: '', tokens: {}, z: {}, measured: true, ...r })),
});

it('fills only empty directions and is one undo step', async () => {
  open('editing', [
    seg('a', 0),
    seg('b', 2, { direction: 'sad' }),
    seg('c', 4, { direction: '   ' }),
    seg('d', 6),
  ]);
  vi.mocked(apiJson).mockResolvedValueOnce(
    reply([
      { id: 'a', direction: 'urgent, quick' },
      { id: 'b', direction: 'whispered' },
      { id: 'c', direction: 'calm' },
      { id: 'd', direction: '', measured: true },
    ]),
  );
  const outcome = await mirrorDubSourceDelivery();
  expect(outcome).toEqual({ applied: 2, measured: 4, source: 'vocals' });
  expect(apiJson).toHaveBeenCalledWith(
    '/dub/prosody-mirror/job-1',
    expect.objectContaining({ method: 'POST' }),
  );
  const body = JSON.parse(vi.mocked(apiJson).mock.calls[0][1]!.body as string);
  expect(body.segments[0]).toEqual({ id: 'a', start: 0, end: 1, speaker_id: '' });
  expect(dubSession.state.segments.map((s) => s.direction)).toEqual([
    'urgent, quick',
    'sad',
    'calm',
    undefined,
  ]);
  expect(dubSession.state.phase).toBe('editing');
  undoDubEdit();
  expect(dubSession.state.segments.map((s) => s.direction)).toEqual([
    undefined,
    'sad',
    '   ',
    undefined,
  ]);
});

it('applies nothing and records no edit when no line needs a direction', async () => {
  open('editing', [seg('a', 0)]);
  vi.mocked(apiJson).mockResolvedValueOnce(reply([{ id: 'a', direction: '' }]));
  const before = dubSession.state.segments;
  expect(await mirrorDubSourceDelivery()).toEqual({ applied: 0, measured: 1, source: 'vocals' });
  expect(dubSession.state.segments).toBe(before);
});

it('leaves segments untouched on failure and keeps an edited dub editing', async () => {
  open('editing', [seg('a', 0)]);
  const before = dubSession.state.segments;
  vi.mocked(apiJson).mockRejectedValueOnce(new Error('analysis failed'));
  expect(await mirrorDubSourceDelivery()).toBeNull();
  expect(dubSession.state.segments).toBe(before);
  expect(dubSession.state.phase).toBe('editing');
  expect(dubSession.state.error).toBe('analysis failed');
});

it('keeps a finished dub finished when analysis fails', async () => {
  open('done', [seg('a', 0)]);
  vi.mocked(apiJson).mockRejectedValueOnce(new Error('boom'));
  expect(await mirrorDubSourceDelivery()).toBeNull();
  expect(dubSession.state.phase).toBe('done');
});

it('keeps a finished dub finished after a successful pass', async () => {
  open('done', [seg('a', 0)]);
  vi.mocked(apiJson).mockResolvedValueOnce(reply([{ id: 'a', direction: 'calm' }]));
  expect((await mirrorDubSourceDelivery())?.applied).toBe(1);
  expect(dubSession.state.phase).toBe('done');
});

it('does nothing without a job or segments', async () => {
  open('editing', [seg('a', 0)], null);
  expect(await mirrorDubSourceDelivery()).toBeNull();
  expect(await cleanupDubSegments()).toBeNull();
  open('editing', []);
  expect(await mirrorDubSourceDelivery()).toBeNull();
  expect(apiJson).not.toHaveBeenCalled();
});

it('discards a late response after the run was cancelled', async () => {
  open('editing', [seg('a', 0)]);
  let release!: (value: unknown) => void;
  vi.mocked(apiJson).mockImplementationOnce(
    () => new Promise((resolve) => (release = resolve)) as never,
  );
  vi.mocked(apiJson).mockResolvedValue({});
  const pending = mirrorDubSourceDelivery();
  expect(dubSession.state.phase).toBe('mirroring');
  await cancelDub();
  release(reply([{ id: 'a', direction: 'urgent' }]));
  expect(await pending).toBeNull();
  expect(dubSession.state.segments[0].direction).toBeUndefined();
  expect(dubSession.state.phase).toBe('editing');
});

it('Clean Up posts the editor segments and applies the returned ones as one edit', async () => {
  const editor = [seg('0', 0, { text: 'edited' }), seg('1', 1), seg('2', 2)];
  open('editing', editor);
  vi.mocked(apiJson).mockResolvedValueOnce({
    segments: [{ id: 0, start: 0, end: 2, text: 'edited merged' }],
    before: 3,
    after: 1,
  });
  expect(await cleanupDubSegments()).toBe(2);
  const call = vi.mocked(apiJson).mock.calls[0];
  expect(call[0]).toBe('/dub/cleanup-segments/job-1');
  expect(JSON.parse(call[1]!.body as string).segments).toHaveLength(3);
  expect(dubSession.state.segments).toMatchObject([
    { id: '0', text: 'edited merged', text_original: 'edited merged' },
  ]);
  expect(dubSession.state.phase).toBe('editing');
  undoDubEdit();
  expect(dubSession.state.segments).toHaveLength(3);
  expect(dubSession.state.segments[0].text).toBe('edited');
});

it('Clean Up failure keeps segments and the starting phase', async () => {
  open('done', [seg('0', 0)]);
  const before = dubSession.state.segments;
  vi.mocked(apiJson).mockRejectedValueOnce(new Error('nope'));
  expect(await cleanupDubSegments()).toBeNull();
  expect(dubSession.state.segments).toBe(before);
  expect(dubSession.state.phase).toBe('done');
});

it('sends mirrored and typed directions with translation', async () => {
  open('editing', [seg('a', 0), seg('b', 2, { direction: ' sad ' })]);
  vi.mocked(apiJson).mockResolvedValueOnce(reply([{ id: 'a', direction: 'urgent' }]));
  await mirrorDubSourceDelivery();
  vi.mocked(apiJson).mockReset();
  vi.mocked(apiJson).mockResolvedValueOnce([]).mockResolvedValueOnce({ translated: [] });
  await translateDub('es', 'google');
  const translate = vi.mocked(apiJson).mock.calls.find((c) => c[0] === '/dub/translate')!;
  const sent = JSON.parse(translate[1]!.body as string).segments;
  expect(sent.map((s: { direction?: string }) => s.direction)).toEqual(['urgent', 'sad']);
});
