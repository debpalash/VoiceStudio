import { beforeEach, describe, expect, it } from 'vitest';
import { canCreateStoryFromDub, loadDubIntoStories, storiesDraftOccupied } from './dub-to-story';
import { longformSession, storiesImportEpoch, type Draft } from '../longform/longform-session';

const storiesDraft = () => longformSession.state.drafts.stories;
const baseline = JSON.parse(JSON.stringify(longformSession.state.drafts));
const ids = () => {
  let n = 0;
  return () => `line-${++n}`;
};

beforeEach(() => {
  longformSession.setState((s) => ({
    ...s,
    active: null,
    drafts: JSON.parse(JSON.stringify(baseline)),
  }));
});

describe('when the action is offered', () => {
  it.each([
    ['editing', true],
    ['generating', true],
    ['done', true],
    ['idle', false],
    ['preparing', false],
    ['transcribing', false],
    ['translating', false],
    ['importing', false],
    ['cleaning', false],
  ])('phase %s', (phase, expected) => {
    expect(canCreateStoryFromDub({ phase, segments: [{ id: 'a' }] })).toBe(expected);
  });

  it('needs segments, whatever the phase', () => {
    expect(canCreateStoryFromDub({ phase: 'done', segments: [] })).toBe(false);
  });
});

describe('whether the user is asked first', () => {
  it('is not asked when Stories is empty', () => {
    expect(storiesDraftOccupied(storiesDraft())).toBe(false);
  });

  it.each([
    ['lines', { lines: [{ id: 'l1', text: 'Hello', profileId: null }] }],
    ['a cast', { cast: [{ id: 'c1', name: 'Mara', profileId: null }] }],
    ['saved voice assignments', { voiceCast: { Anna: 'voice-1' } }],
    // Imported text not yet split into lines is work too, and loading a dub
    // clears it — asking only about lines would lose it silently.
    ['pending imported text', { importText: 'A chapter someone imported.' }],
    ['a completed render', { output: 'old-render.mp3' }],
  ])('is asked when Stories holds %s', (_label, patch) => {
    expect(storiesDraftOccupied({ ...storiesDraft(), ...patch } as Draft)).toBe(true);
  });
});

describe('loading a dub into Stories', () => {
  const segments = [
    { id: 'a', start: 0, text: 'Good morning.', speaker_id: 'Anna', profile_id: 'p-anna' },
    { id: 'b', start: 1, text: 'And to you.', speaker_id: 'Ben', profile_id: 'p-ben' },
  ];

  it('replaces the cast and lines, and gives every line an id', () => {
    expect(
      loadDubIntoStories(segments, {
        profiles: [{ id: 'p-anna' }, { id: 'p-ben' }],
        unknownSpeakerLabel: 'Speaker',
        newLineId: ids(),
      }),
    ).toBe(true);
    const draft = storiesDraft();
    expect(draft.cast.map((c) => [c.name, c.profileId])).toEqual([
      ['Anna', 'p-anna'],
      ['Ben', 'p-ben'],
    ]);
    expect(draft.lines).toEqual([
      { id: 'line-1', character: 'anna', text: 'Good morning.', profileId: null },
      { id: 'line-2', character: 'ben', text: 'And to you.', profileId: null },
    ]);
  });

  it('clears a pending import so it cannot reappear under the new script', () => {
    longformSession.setState((s) => ({
      ...s,
      drafts: { ...s.drafts, stories: { ...s.drafts.stories, importText: 'left over' } },
    }));
    loadDubIntoStories(segments, { unknownSpeakerLabel: 'Speaker', newLineId: ids() });
    expect(storiesDraft().importText).toBe('');
  });

  it('invalidates an import in flight and clears previous render/download state', () => {
    const oldEpoch = storiesImportEpoch.current;
    longformSession.setState((s) => ({
      ...s,
      drafts: {
        ...s.drafts,
        stories: {
          ...s.drafts.stories,
          output: 'old-render.mp3',
          outputScript: 'old script',
          outputChapters: [{ title: 'Old', status: 'done' }],
          outputCachedChapters: 1,
          outputFailedChapters: 2,
          voiceCast: { Anna: 'old-voice' },
        },
      },
    }));
    loadDubIntoStories(segments, { unknownSpeakerLabel: 'Speaker', newLineId: ids() });
    expect(storiesImportEpoch.current).toBe(oldEpoch + 1);
    expect(storiesDraft()).toMatchObject({
      output: '',
      outputScript: '',
      outputChapters: [],
      outputCachedChapters: 0,
      outputFailedChapters: 0,
      voiceCast: {},
    });
  });

  it('detaches the open project, so a later save cannot overwrite it', () => {
    longformSession.setState((s) => ({
      ...s,
      drafts: { ...s.drafts, stories: { ...s.drafts.stories, projectId: 'book-7' } },
    }));
    loadDubIntoStories(segments, { unknownSpeakerLabel: 'Speaker', newLineId: ids() });
    expect(storiesDraft().projectId).toBeNull();
  });

  it('refuses while a longform render is running, rather than half-applying', () => {
    longformSession.setState((s) => ({ ...s, active: 'stories' }));
    expect(loadDubIntoStories(segments, { unknownSpeakerLabel: 'Speaker' })).toBe(false);
    expect(storiesDraft().lines).toEqual([]);
  });

  it('refuses a dub with nothing speakable', () => {
    expect(loadDubIntoStories([{ id: 'a', text: '   ' }], { unknownSpeakerLabel: 'Speaker' })).toBe(
      false,
    );
    expect(storiesDraft().lines).toEqual([]);
  });

  it('labels unattributed speech with the caller’s translated word', () => {
    loadDubIntoStories([{ id: 'a', text: 'Once upon a time.' }], {
      unknownSpeakerLabel: 'Sprecher',
      newLineId: ids(),
    });
    expect(storiesDraft().cast[0].name).toBe('Sprecher');
  });

  it('splits a merged row back onto the speakers who actually spoke it', () => {
    // The dub table spreads `...first` when merging, so the row names only
    // Anna at the top level; Ben survives in merge_parts.
    const loaded = loadDubIntoStories(
      [
        {
          id: 'a+b',
          start: 0,
          text: 'Good morning. And to you.',
          speaker_id: 'Anna',
          profile_id: 'p-anna',
          merge_parts: [
            { textStart: 0, textEnd: 13, speaker_id: 'Anna', profile_id: 'p-anna' },
            { textStart: 14, textEnd: 25, speaker_id: 'Ben', profile_id: 'p-ben' },
          ],
        },
      ],
      {
        profiles: [{ id: 'p-anna' }, { id: 'p-ben' }],
        unknownSpeakerLabel: 'Speaker',
        newLineId: ids(),
      },
    );
    expect(loaded).toBe(true);
    expect(storiesDraft().cast.map((c) => c.name)).toEqual(['Anna', 'Ben']);
    expect(storiesDraft().lines.map((l) => [l.character, l.text])).toEqual([
      ['anna', 'Good morning.'],
      ['ben', 'And to you.'],
    ]);
  });
});
