import { expect, it } from 'vitest';
import { importedLongformDraft, type ProjectBundleResult } from './project-handoff-format';
import { createProjectLibrary } from '../longform/project-library';
import { blankLongformDraft } from '../longform/longform-session';
import type { DurableLongformRecord } from '@shared/utils/indexedDbLongformStore';

it('keeps incoming edits and settings while allocating a new local project identity', async () => {
  let record: DurableLongformRecord | null = null;
  const library = createProjectLibrary({
    read: async () => record,
    write: async (value) => {
      record = structuredClone(value);
    },
    clear: async () => {},
  });
  const old = await library.save('Existing', 'stories', {
    ...blankLongformDraft(),
    script: 'Desktop original',
  });
  const imported = importedLongformDraft({
    kind: 'stories',
    title: 'Phone edit',
    project: {
      draft: {
        ...blankLongformDraft(),
        projectId: old.id,
        script: 'Edited on phone',
        globalSpeed: 1.15,
      },
    },
  });
  const next = await library.save('Phone edit', 'stories', imported);
  expect(next.id).not.toBe(old.id);
  expect(next.draft).toMatchObject({
    projectId: next.id,
    script: 'Edited on phone',
    globalSpeed: 1.15,
  });
  expect((await library.list()).find((p) => p.id === old.id)?.draft.script).toBe(
    'Desktop original',
  );
});
it('rejects duplicate line identities before edits can affect multiple rows', () => {
  expect(() =>
    importedLongformDraft({
      kind: 'stories',
      title: 'Duplicates',
      project: {
        draft: {
          ...blankLongformDraft(),
          lines: [
            { id: 'same', text: 'First', profileId: null },
            { id: 'same', text: 'Second', profileId: null },
          ],
        },
      },
    }),
  ).toThrow();
});
it('rejects malformed incoming documents before persisting them', () => {
  for (const value of [
    null,
    { lines: [], cast: [], script: 7 },
    { lines: 'invalid', script: 'text' },
    { lines: [null], cast: [], script: 'text' },
    { lines: [], cast: [], script: 'text', overrides: { emoText: 7 } },
  ]) {
    expect(() =>
      importedLongformDraft({
        kind: 'stories',
        title: 'Bad',
        project: { draft: value },
      } as unknown as ProjectBundleResult),
    ).toThrow();
  }
});

it('rejects invalid imported output formats and chapter counters', () => {
  for (const fields of [
    { format: ['m4b'] },
    { format: 'wav' },
    { outputCachedChapters: '1' },
    { outputFailedChapters: -1 },
    { outputCachedChapters: 0.5 },
    { outputFailedChapters: Infinity },
  ]) {
    expect(() =>
      importedLongformDraft({
        kind: 'audiobook',
        title: 'Bad output',
        project: { draft: { ...blankLongformDraft(), ...fields } },
      } as unknown as ProjectBundleResult),
    ).toThrow();
  }
  expect(
    importedLongformDraft({
      kind: 'audiobook',
      title: 'Valid output',
      project: {
        draft: {
          ...blankLongformDraft(),
          format: 'mp3',
          outputCachedChapters: 2,
          outputFailedChapters: 1,
        },
      },
    }),
  ).toMatchObject({ format: 'mp3', outputCachedChapters: 2, outputFailedChapters: 1 });
});
