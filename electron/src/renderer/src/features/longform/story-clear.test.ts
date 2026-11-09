import { describe, expect, it } from 'vitest';
import { blankLongformDraft } from './longform-session';
import { clearedScriptPatch, scriptSize } from './story-clear';

describe('script reset patches', () => {
  it.each([
    ['audiobook', { script: '' }],
    ['stories', { lines: [], importText: '' }],
  ] as const)('creates the narrow patch for %s', (kind, expected) => {
    const patch = clearedScriptPatch(kind);
    expect(patch).toStrictEqual(expected);
    expect(Object.keys(patch)).not.toContain('cast');
  });
});

describe('content count used by the reset confirmation', () => {
  it('treats only visible audiobook text as one unit', () => {
    const base = blankLongformDraft();
    const counts = ['\n\t ', ' A chapter begins. '].map((script) =>
      scriptSize('audiobook', { ...base, script }),
    );
    expect(counts).toStrictEqual([0, 1]);
  });

  it('adds a nonblank import buffer to the existing story rows', () => {
    const base = {
      ...blankLongformDraft(),
      lines: [
        { id: 'line-a', text: 'Narration', profileId: null },
        { id: 'line-b', text: '', profileId: 'voice-b' },
      ],
    };
    expect([
      scriptSize('stories', base),
      scriptSize('stories', { ...base, importText: 'Paste queue' }),
      scriptSize('stories', { ...base, importText: '\n ' }),
      scriptSize('stories', { ...base, importText: undefined as unknown as string }),
    ]).toEqual([2, 3, 2, 2]);
  });
});
