import { expect, it } from 'vitest';
import { consolidateDubCaptions, narrationFile } from './dub-narration';
import type { DubSegment } from './dub-session';
const cue = (
  id: string,
  start: number,
  end: number,
  text: string,
  extra: Partial<DubSegment> = {},
): DubSegment => ({ id, start, end, text, text_original: text, profile_id: 'voice', ...extra });
it('reconstructs complete phrases without changing caption text or timing', () => {
  const captions = [
    cue('a', 10, 12, 'We are going to configure'),
    cue('b', 12, 14, 'the server to connect'),
    cue('c', 14, 17, 'our store.'),
  ];
  const before = structuredClone(captions);
  const blocks = consolidateDubCaptions(captions, 'en');
  expect(blocks).toHaveLength(1);
  expect(blocks[0]).toMatchObject({
    start: 10,
    end: 17,
    text: 'We are going to configure the server to connect our store.',
    source_ids: ['a', 'b', 'c'],
  });
  expect(captions).toEqual(before);
  expect(consolidateDubCaptions(captions, 'en')[0].id).toBe(blocks[0].id);
});
it('respects speakers, voice settings, long pauses and block length', () => {
  const cues = [
    cue('a', 0, 5, 'Hello.'),
    cue('b', 7, 10, 'Next', { speaker_id: 'other' }),
    cue('c', 10, 15, 'voice.', { speaker_id: 'other', profile_id: 'other' }),
    cue('d', 20, 45, 'Long block'),
    cue('e', 45, 50, 'after it.'),
  ];
  expect(consolidateDubCaptions(cues, 'en').map((b) => b.source_ids)).toEqual([
    ['a'],
    ['b'],
    ['c'],
    ['d'],
    ['e'],
  ]);
});
it('keeps explicit auto-segment voice bindings separate and never drops blank captions', () => {
  const captions = [
    cue('a', 0, 1, 'One', { profile_id: 'auto-seg:a' }),
    cue('b', 1, 2, 'two.', { profile_id: 'auto-seg:b' }),
    cue('c', 2, 3, ''),
  ];
  expect(consolidateDubCaptions(captions, 'en')).toHaveLength(2);
  expect(captions).toHaveLength(3);
});
it('exports a versioned narration document with subtitle links', () => {
  const captions = [cue('a', 0, 2, 'Hello.')];
  const blocks = consolidateDubCaptions(captions, 'en');
  const file = JSON.parse(narrationFile({ language: 'en', captions, blocks }));
  expect(file.type).toBe('voicestudio.dubbing-script');
  expect(file.version).toBe(2);
  expect(file.blocks[0].source_ids).toEqual(['a']);
  expect(file.subtitles[0].text).toBe('Hello.');
});

it('detects a shifted proposed window while preserving individual source cue times', async () => {
  const { compareNarration } = await import('./dub-narration');
  const caps = [cue('a', 10, 12, 'Hello'), cue('b', 12, 15, 'world.')];
  const block = consolidateDubCaptions(caps, 'en')[0];
  expect(compareNarration(caps, [block])[0].matches).toBe(true);
  expect(compareNarration(caps, [{ ...block, start: 12, end: 17 }])[0]).toMatchObject({
    matches: false,
    start: 10,
    end: 15,
  });
  expect(caps.map((c) => [c.start, c.end])).toEqual([
    [10, 12],
    [12, 15],
  ]);
});
