/**
 * Rebuilding a Story from a finished dub.
 *
 * A `DubSegment` is typed `Record<string, unknown> & { id, text }`, so every
 * field this transform reads apart from `text` arrives unknown — and segments
 * can come from an imported SRT rather than from diarisation. These tests hold
 * the transform to that contract: it never throws, it never invents a voice it
 * cannot honour, and a line never references a character that does not exist.
 */
import { describe, it, expect } from 'vitest';
import { dubToStory } from './dubToStory';
import { mergedParts } from './segmentParts';
import { CAST_COLORS } from './storyCast';

const seg = (fields) => ({ id: 'x', ...fields });

/**
 * A merged row exactly as the dub table builds one: `mergeDubSegments` spreads
 * `...first`, so the row's own `speaker_id` is the FIRST speaker's and the
 * second speaker survives only inside `merge_parts`.
 */
const merged = (a, b) => ({
  ...a,
  id: `${a.id}+${b.id}`,
  end: b.end,
  text: `${a.text} ${b.text}`.trim(),
  merge_parts: mergedParts(a, b),
});

describe('dubToStory — the ordinary case', () => {
  const segments = [
    seg({ start: 0, text: 'Good morning.', speaker_id: 'Anna', profile_id: 'p-anna' }),
    seg({ start: 5, text: 'And to you.', speaker_id: 'Ben', profile_id: 'p-ben' }),
    seg({ start: 9, text: 'Shall we?', speaker_id: 'Anna', profile_id: 'p-anna' }),
  ];

  it('makes one character per speaker, in the order they first speak', () => {
    const { cast } = dubToStory(segments);
    expect(cast.map((c) => c.name)).toEqual(['Anna', 'Ben']);
    expect(cast.map((c) => c.id)).toEqual(['anna', 'ben']);
  });

  it('keeps each speaker their assigned voice', () => {
    expect(
      dubToStory(segments, { profiles: [{ id: 'p-anna' }, { id: 'p-ben' }] }).cast.map(
        (c) => c.profileId,
      ),
    ).toEqual(['p-anna', 'p-ben']);
  });

  it('makes one line per segment, under the right character', () => {
    const { tracks } = dubToStory(segments);
    expect(tracks).toEqual([
      { character: 'anna', text: 'Good morning.' },
      { character: 'ben', text: 'And to you.' },
      { character: 'anna', text: 'Shall we?' },
    ]);
  });

  it('gives each speaker a distinct colour', () => {
    const { cast } = dubToStory(segments);
    expect(new Set(cast.map((c) => c.color)).size).toBe(2);
    expect(CAST_COLORS).toContain(cast[0].color);
  });

  it('reports what it did', () => {
    expect(dubToStory(segments).stats).toEqual({ lines: 3, speakers: 2, skippedEmpty: 0 });
  });

  it('leaves every line on the cast voice, so re-voicing a character works', () => {
    // A per-line override would silently win over the cast dropdown, making the
    // character's voice unchangeable from the UI.
    for (const track of dubToStory(segments).tracks) {
      expect(track).not.toHaveProperty('profileId');
    }
  });
});

describe('every line belongs to a character that exists', () => {
  it('holds across speakers, blanks and odd ids', () => {
    const { cast, tracks } = dubToStory([
      seg({ start: 0, text: 'a', speaker_id: 'Anna' }),
      seg({ start: 1, text: 'b' }),
      seg({ start: 2, text: 'c', speaker_id: '   ' }),
      seg({ start: 3, text: 'd', speaker_id: 'Anna' }),
      seg({ start: 4, text: 'e', speaker_id: 42 }),
    ]);
    const ids = new Set(cast.map((c) => c.id));
    for (const track of tracks) expect(ids.has(track.character)).toBe(true);
  });
});

describe('segments with nothing to say', () => {
  it.each([
    ['empty string', ''],
    ['whitespace', '   '],
    ['missing', undefined],
    ['null', null],
    ['an object', { a: 1 }],
    ['an array', ['a']],
    ['a boolean', true],
  ])('drops and counts %s text', (_label, text) => {
    const { tracks, stats } = dubToStory([
      seg({ start: 0, text: 'kept', speaker_id: 'A' }),
      seg({ start: 1, text, speaker_id: 'A' }),
    ]);
    expect(tracks).toHaveLength(1);
    expect(stats.skippedEmpty).toBe(1);
  });

  it('keeps a numeric line — "0" is something a narrator says', () => {
    const { tracks, stats } = dubToStory([seg({ start: 0, text: 0, speaker_id: 'A' })]);
    expect(tracks).toEqual([{ character: 'a', text: '0' }]);
    expect(stats.skippedEmpty).toBe(0);
  });

  it('never creates a character for a speaker whose only lines were dropped', () => {
    const { cast } = dubToStory([
      seg({ start: 0, text: 'kept', speaker_id: 'A' }),
      seg({ start: 1, text: '   ', speaker_id: 'Ghost' }),
    ]);
    expect(cast.map((c) => c.name)).toEqual(['A']);
  });

  it('trims the line it keeps', () => {
    expect(dubToStory([seg({ text: '  padded  ', speaker_id: 'A' })]).tracks[0].text).toBe(
      'padded',
    );
  });
});

describe('unattributed speech', () => {
  it('collects every unnamed segment under one character, not one each', () => {
    const { cast, tracks } = dubToStory(
      [
        seg({ start: 0, text: 'one' }),
        seg({ start: 1, text: 'two', speaker_id: '' }),
        seg({ start: 2, text: 'three', speaker_id: '  ' }),
        seg({ start: 3, text: 'four', speaker_id: null }),
      ],
      { unknownSpeakerLabel: 'Narrator' },
    );
    expect(cast).toHaveLength(1);
    expect(cast[0].name).toBe('Narrator');
    expect(new Set(tracks.map((t) => t.character)).size).toBe(1);
  });

  it('takes the caller label, so the util stays free of translated text', () => {
    expect(dubToStory([seg({ text: 'x' })], { unknownSpeakerLabel: 'Sprecher' }).cast[0].name).toBe(
      'Sprecher',
    );
  });

  it('falls back to a usable name when the caller passes nothing usable', () => {
    for (const label of [undefined, '', '   ', 42, null]) {
      const { cast } = dubToStory([seg({ text: 'x' })], { unknownSpeakerLabel: label });
      expect(cast[0].name).toBe('Speaker');
      expect(cast[0].id).toBe('speaker');
    }
  });

  it('does not take the narrator id from a real speaker called Narrator', () => {
    // The real speaker appears first and claims `narrator`; the unattributed
    // group, labelled the same way, must not collide with it.
    const { cast } = dubToStory(
      [
        seg({ start: 0, text: 'named', speaker_id: 'Narrator' }),
        seg({ start: 1, text: 'unnamed' }),
      ],
      { unknownSpeakerLabel: 'Narrator' },
    );
    expect(cast.map((c) => c.id)).toEqual(['narrator', 'narrator-2']);
  });
});

describe('ordering', () => {
  it('sorts by start, so the cast is named in the order speakers are heard', () => {
    const { cast, tracks } = dubToStory([
      seg({ start: 30, text: 'later', speaker_id: 'Ben' }),
      seg({ start: 10, text: 'earlier', speaker_id: 'Anna' }),
    ]);
    expect(cast.map((c) => c.name)).toEqual(['Anna', 'Ben']);
    expect(tracks.map((t) => t.text)).toEqual(['earlier', 'later']);
  });

  it.each([
    ['missing', undefined],
    ['null', null],
    ['NaN', Number.NaN],
    ['a word', 'soon'],
    ['an object', {}],
  ])('treats %s start as 0 and keeps the arrival order', (_label, start) => {
    const { tracks } = dubToStory([
      seg({ start, text: 'first', speaker_id: 'A' }),
      seg({ start, text: 'second', speaker_id: 'A' }),
      seg({ start, text: 'third', speaker_id: 'A' }),
    ]);
    expect(tracks.map((t) => t.text)).toEqual(['first', 'second', 'third']);
  });

  it('accepts a negative start rather than discarding the segment', () => {
    const { tracks } = dubToStory([
      seg({ start: 0, text: 'zero', speaker_id: 'A' }),
      seg({ start: -5, text: 'before', speaker_id: 'A' }),
    ]);
    expect(tracks.map((t) => t.text)).toEqual(['before', 'zero']);
  });

  it('accepts a numeric string start', () => {
    const { tracks } = dubToStory([
      seg({ start: '20', text: 'b', speaker_id: 'A' }),
      seg({ start: '3', text: 'a', speaker_id: 'A' }),
    ]);
    expect(tracks.map((t) => t.text)).toEqual(['a', 'b']);
  });
});

/**
 * Cast ids and voice resolution are internal; like `buildAutoCast` next door the
 * module exports one function, so they are pinned through its output.
 */
describe('cast ids', () => {
  const castIds = (...speakers) =>
    dubToStory(speakers.map((speaker_id, i) => seg({ start: i, text: 'x', speaker_id }))).cast.map(
      (c) => c.id,
    );

  it('slugs a name the way a hand-added character is slugged', () => {
    expect(castIds('Anna Smith', 'Dr. Jones')).toEqual(['anna-smith', 'dr-jones']);
  });

  it('gives Narrator the bare narrator id', () => {
    expect(castIds('Narrator')).toEqual(['narrator']);
    expect(castIds('narrator')).toEqual(['narrator']);
  });

  it('separates names that slug the same way', () => {
    expect(castIds('Speaker 1', 'Speaker  1', 'speaker/1')).toEqual([
      'speaker-1',
      'speaker-1-2',
      'speaker-1-3',
    ]);
  });

  it('names a speaker whose id is entirely punctuation', () => {
    expect(castIds('???', '!!!')).toEqual(['char', 'char-2']);
  });

  it('does not backtrack on a hostile speaker name', () => {
    // The slug regex is one bounded negated class, so a long run of separators
    // is linear. A polynomial one would hang here rather than fail.
    const started = performance.now();
    expect(castIds('-'.repeat(100000) + 'a')).toEqual(['a']);
    expect(performance.now() - started).toBeLessThan(1000);
  });
});

describe('which voice a character gets', () => {
  const profiles = [
    { id: 'p1', name: 'Anna' },
    { id: 'p2', name: 'Ben' },
    { id: 'p-saved', name: 'Saved' },
    { id: 7, name: 'Seven' },
  ];
  const voiceOf = (profile_id, { speaker = 'Anna', list = profiles } = {}) =>
    dubToStory([seg({ text: 'x', speaker_id: speaker, profile_id })], { profiles: list }).cast[0]
      .profileId;

  it('passes a saved profile id through', () => {
    expect(voiceOf('p-saved')).toBe('p-saved');
  });

  it('falls back to the default when a saved profile was deleted', () => {
    expect(voiceOf('p-gone', { list: [] })).toBeNull();
  });

  it.each([[''], [null], [undefined]])('means the cast default for %s', (profile_id) => {
    expect(voiceOf(profile_id)).toBeNull();
  });

  it('binds an auto-clone to a saved profile of the same name', () => {
    expect(voiceOf('auto:anna')).toBe('p1');
    expect(voiceOf('auto:whoever', { speaker: '  Anna  ' })).toBe('p1');
  });

  it('falls back to the default when an auto-clone was never saved', () => {
    // The auto: payload is a lossy slug, so it is not reversed — an unsaved
    // auto-clone has no profile to point at and must not dangle.
    expect(voiceOf('auto:ghost', { speaker: 'Ghost' })).toBeNull();
  });

  it('takes the first of two profiles sharing a name, deterministically', () => {
    expect(
      voiceOf('auto:x', {
        list: [
          { id: 'first', name: 'Anna' },
          { id: 'second', name: 'Anna' },
        ],
      }),
    ).toBe('first');
  });

  it('sends a design preset to the default, since Stories cannot list it', () => {
    expect(voiceOf('preset:warm')).toBeNull();
  });

  it('survives a malformed profile list', () => {
    expect(voiceOf('auto:x', { list: [null, {}, { name: 'Anna' }] })).toBeNull();
    expect(voiceOf('auto:x', { list: null })).toBeNull();
  });

  it('coerces a non-string profile id', () => {
    expect(voiceOf(7)).toBe('7');
  });
});

describe('a speaker whose segments disagree about the voice', () => {
  it('takes the first available voice, so the cast is deterministic', () => {
    const { cast } = dubToStory(
      [
        seg({ start: 0, text: 'a', speaker_id: 'Anna', profile_id: 'p-first' }),
        seg({ start: 1, text: 'b', speaker_id: 'Anna', profile_id: 'p-second' }),
      ],
      { profiles: [{ id: 'p-first' }, { id: 'p-second' }] },
    );
    expect(cast).toHaveLength(1);
    expect(cast[0].profileId).toBe('p-first');
  });
});

describe('colours', () => {
  it('gives the first eight speakers the whole palette', () => {
    const { cast } = dubToStory(
      Array.from({ length: 8 }, (_v, i) => seg({ start: i, text: 'x', speaker_id: `S${i}` })),
    );
    expect(new Set(cast.map((c) => c.color)).size).toBe(8);
  });

  it('keeps going past the palette instead of running out', () => {
    const { cast } = dubToStory(
      Array.from({ length: 11 }, (_v, i) => seg({ start: i, text: 'x', speaker_id: `S${i}` })),
    );
    expect(cast).toHaveLength(11);
    for (const member of cast) expect(CAST_COLORS).toContain(member.color);
  });
});

describe('input it must survive', () => {
  it.each([
    ['null', null],
    ['undefined', undefined],
    ['a string', 'segments'],
    ['a number', 7],
    ['an object', { segments: [] }],
    ['a boolean', false],
  ])('returns an empty story for %s', (_label, input) => {
    expect(dubToStory(input)).toEqual({
      cast: [],
      tracks: [],
      stats: { lines: 0, speakers: 0, skippedEmpty: 0 },
    });
  });

  it('returns an empty story for an empty list', () => {
    expect(dubToStory([]).stats).toEqual({ lines: 0, speakers: 0, skippedEmpty: 0 });
  });

  it('survives a list of junk without throwing', () => {
    const { cast, tracks, stats } = dubToStory([null, undefined, 7, 'text', {}, []]);
    expect(cast).toEqual([]);
    expect(tracks).toEqual([]);
    expect(stats.skippedEmpty).toBe(6);
  });

  it('does not hand back shared state between calls', () => {
    const first = dubToStory(null);
    first.cast.push({ id: 'x' });
    expect(dubToStory(null).cast).toEqual([]);
  });

  it('does not mutate the segments it was given', () => {
    const segments = [
      seg({ start: 5, text: 'b', speaker_id: 'B' }),
      seg({ start: 1, text: 'a', speaker_id: 'A' }),
    ];
    const snapshot = JSON.parse(JSON.stringify(segments));
    dubToStory(segments);
    expect(segments).toEqual(snapshot);
  });
});

/**
 * A merged row is the case a naive transform gets wrong. `mergeDubSegments`
 * spreads `...first`, so the merged row's top-level `speaker_id` names only the
 * first speaker; the second survives in `merge_parts`. Reading the top level
 * alone would put the second character's words in the first character's voice —
 * #1612, on a new surface.
 */
describe('rows built by merging', () => {
  const anna = seg({
    id: 'a',
    start: 0,
    end: 2,
    text: 'Good morning.',
    speaker_id: 'Anna',
    profile_id: 'p-anna',
  });
  const ben = seg({
    id: 'b',
    start: 2,
    end: 4,
    text: 'And to you.',
    speaker_id: 'Ben',
    profile_id: 'p-ben',
  });

  it('keeps each speaker their own words and their own voice (#1612)', () => {
    const { cast, tracks } = dubToStory([merged(anna, ben)], {
      profiles: [{ id: 'p-anna' }, { id: 'p-ben' }],
    });
    expect(tracks).toEqual([
      { character: 'anna', text: 'Good morning.' },
      { character: 'ben', text: 'And to you.' },
    ]);
    expect(cast.map((c) => [c.name, c.profileId])).toEqual([
      ['Anna', 'p-anna'],
      ['Ben', 'p-ben'],
    ]);
  });

  it('leaves a row merged to repair one speaker’s sentence as one line', () => {
    const half = seg({ id: 'a', start: 0, end: 1, text: 'I was going to say', speaker_id: 'Anna' });
    const rest = seg({
      id: 'b',
      start: 1,
      end: 2,
      text: 'something important.',
      speaker_id: 'Anna',
    });
    const { cast, tracks } = dubToStory([merged(half, rest)]);
    expect(tracks).toEqual([
      { character: 'anna', text: 'I was going to say something important.' },
    ]);
    expect(cast).toHaveLength(1);
  });

  it('does not split a line over a voice the Story cannot express', () => {
    // One segment given its own voice before the merge. Story lines carry no
    // per-line override, so splitting here would yield two identical lines.
    const first = seg({
      id: 'a',
      start: 0,
      end: 1,
      text: 'Wait.',
      speaker_id: 'Anna',
      profile_id: 'p-anna',
    });
    const second = seg({
      id: 'b',
      start: 1,
      end: 2,
      text: 'Please.',
      speaker_id: 'Anna',
      profile_id: 'p-other',
    });
    const { cast, tracks } = dubToStory([merged(first, second)], {
      profiles: [{ id: 'p-anna' }],
    });
    expect(tracks).toEqual([{ character: 'anna', text: 'Wait. Please.' }]);
    expect(cast[0].profileId).toBe('p-anna');
  });

  it('resolves an auto-clone against the span’s own speaker, not the row’s', () => {
    const auto = seg({
      id: 'b',
      start: 2,
      end: 4,
      text: 'And to you.',
      speaker_id: 'Ben',
      profile_id: 'auto:ben-xyz',
    });
    const { cast } = dubToStory([merged(anna, auto)], {
      profiles: [{ id: 'p-anna' }, { id: 'saved-ben', name: 'Ben' }],
    });
    expect(cast.map((c) => c.profileId)).toEqual(['p-anna', 'saved-ben']);
  });

  it('counts every span as a line', () => {
    expect(dubToStory([merged(anna, ben)]).stats).toEqual({
      lines: 2,
      speakers: 2,
      skippedEmpty: 0,
    });
  });
});

/**
 * `merge_parts` offsets are written at merge time and the row stays editable
 * afterwards, so they go stale. They are hints; the words are not. No edit may
 * make a word disappear from the Story.
 */
describe('rows whose recorded offsets no longer fit the text', () => {
  const anna = seg({ id: 'a', start: 0, end: 2, text: 'Hello.', speaker_id: 'Anna' });
  const ben = seg({ id: 'b', start: 2, end: 4, text: 'Hi.', speaker_id: 'Ben' });

  const wordsOf = (tracks) => tracks.flatMap((line) => line.text.split(/\s+/)).filter(Boolean);

  it('keeps text appended after the merge', () => {
    const row = { ...merged(anna, ben), text: 'Hello. Hi. And welcome.' };
    const { tracks } = dubToStory([row]);
    expect(wordsOf(tracks)).toEqual(['Hello.', 'Hi.', 'And', 'welcome.']);
    expect(tracks[tracks.length - 1].character).toBe('ben');
  });

  it('keeps text inserted ahead of the first recorded span', () => {
    const row = {
      ...anna,
      text: 'Well, hello there.',
      merge_parts: [
        { textStart: 6, textEnd: 11, speaker_id: 'Anna' },
        { textStart: 12, textEnd: 18, speaker_id: 'Ben' },
      ],
    };
    const { tracks } = dubToStory([row]);
    expect(wordsOf(tracks)).toEqual(['Well,', 'hello', 'there.']);
    expect(tracks[0]).toEqual({ character: 'anna', text: 'Well, hello' });
  });

  it('never repeats a word when spans overlap', () => {
    const row = {
      ...anna,
      text: 'One two three',
      merge_parts: [
        { textStart: 0, textEnd: 7, speaker_id: 'Anna' },
        { textStart: 4, textEnd: 13, speaker_id: 'Ben' },
      ],
    };
    const { tracks } = dubToStory([row]);
    expect(tracks).toEqual([
      { character: 'anna', text: 'One two' },
      { character: 'ben', text: 'three' },
    ]);
    expect(wordsOf(tracks)).toEqual(['One', 'two', 'three']);
  });

  it('falls back to the row when every recorded span is empty', () => {
    const row = { ...anna, merge_parts: [{ textStart: 4, textEnd: 4, speaker_id: 'Ghost' }] };
    expect(dubToStory([row]).tracks).toEqual([{ character: 'anna', text: 'Hello.' }]);
  });

  it('ignores a merge_parts that is not a list of parts', () => {
    for (const broken of ['nope', 42, {}, null, [null], [{}]]) {
      const { tracks } = dubToStory([{ ...anna, merge_parts: broken }]);
      expect(tracks).toEqual([{ character: 'anna', text: 'Hello.' }]);
    }
  });

  it('trims each span and keeps them in reading order', () => {
    // The joining space between two merged lines belongs to neither speaker.
    expect(dubToStory([merged(anna, ben)]).tracks.map((line) => line.text)).toEqual([
      'Hello.',
      'Hi.',
    ]);
  });
});
