import { afterEach, expect, it, vi } from 'vitest';
import { exportStoryAudio } from '@shared/utils/storyExport';
import { storyChunkBody } from './story-preview';
import { blankLongformDraft } from './longform-session';
afterEach(() => vi.unstubAllGlobals());
it('assembles canonical voice, pause and speed spans for auditions', async () => {
  const close = vi.fn();
  vi.stubGlobal(
    'AudioContext',
    class {
      sampleRate = 10;
      close = close;
      async decodeAudioData() {
        return { length: 10, getChannelData: () => new Float32Array(10) };
      }
    },
  );
  const chunks = vi.fn(
    async (_text: string, _voice: string | null, _speed: number | null) => new Blob(['audio']),
  );
  const result = await exportStoryAudio(
    [{ text: '[slow]Hello[/slow][pause 0.5s][voice:actor]Again' }],
    () => ({ profileId: 'narrator', speed: 1.2 }),
    chunks,
  );
  expect(chunks.mock.calls).toEqual([
    ['Hello', 'narrator', 0.85],
    ['Again', 'actor', 1.2],
  ]);
  expect(result.durationSec).toBe(2.5);
  expect(close).toHaveBeenCalledOnce();
});
it('preserves default longform steps and resolves named inline voices', () => {
  const draft = { ...blankLongformDraft(), voice: 'narrator', voiceCast: { Mara: 'actor' } };
  const body = storyChunkBody(draft, 'Hello', 'Mara', 0.8, [{ id: 'actor' }]);
  expect(body.get('profile_id')).toBe('actor');
  expect(body.get('num_step')).toBe('32');
  expect(body.get('speed')).toBe('0.8');
});

it('uses the full-render default for an unassigned inline name', () => {
  const draft = { ...blankLongformDraft(), voice: 'narrator' };
  expect(
    storyChunkBody(draft, 'Hello', 'Unassigned', null, [{ id: 'narrator' }]).get('profile_id'),
  ).toBe('narrator');
});
