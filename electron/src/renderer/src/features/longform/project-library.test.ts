import { expect, it } from 'vitest';
import { createProjectLibrary } from './project-library';
import { blankLongformDraft } from './longform-session';
import type { DurableLongformRecord } from '@shared/utils/indexedDbLongformStore';
it('serializes simultaneous saves without losing a project and snapshots inputs', async () => {
  let record: DurableLongformRecord | null = null;
  const library = createProjectLibrary({
    read: async () => record,
    write: async (value) => {
      record = structuredClone(value);
    },
    clear: async () => {},
  });
  const draft = blankLongformDraft();
  draft.script = 'Original';
  const first = library.save('First', 'audiobook', draft);
  draft.script = 'Edited';
  const second = library.save('Second', 'stories', draft);
  const [a, b] = await Promise.all([first, second]);
  expect((await library.list()).map((p) => p.id)).toEqual([b.id, a.id]);
  expect((await library.list()).find((p) => p.id === a.id)?.draft.script).toBe('Original');
  await library.remove(a.id);
  await expect(library.save('Deleted', 'audiobook', draft, a.id)).rejects.toThrow(
    'no longer exists',
  );
  expect(await library.list()).toHaveLength(1);
});
it('does not report a successful save when durable storage fails', async () => {
  const library = createProjectLibrary({
    read: async () => null,
    write: async () => {
      throw new Error('Disk full');
    },
    clear: async () => {},
  });
  await expect(library.save('Book', 'audiobook', blankLongformDraft())).rejects.toThrow(
    'Disk full',
  );
});

it('renames the latest saved record without overwriting its draft', async () => {
  let record: DurableLongformRecord | null = null;
  const library = createProjectLibrary({
    read: async () => record,
    write: async (value) => {
      record = structuredClone(value);
    },
    clear: async () => {},
  });
  const original = await library.save('Original', 'stories', blankLongformDraft());
  const save = library.save(
    'Original',
    'stories',
    { ...original.draft, script: 'New edit' },
    original.id,
  );
  const rename = library.rename(original.id, ' Renamed ');
  await Promise.all([save, rename]);
  expect((await library.list())[0]).toMatchObject({
    name: 'Renamed',
    draft: { script: 'New edit' },
  });
  await expect(library.rename(original.id, '  ')).rejects.toThrow('required');
  await library.remove(original.id);
  await expect(library.rename(original.id, 'Missing')).rejects.toThrow('no longer exists');
});

it('serializes clearing after pending project writes', async () => {
  let record: DurableLongformRecord | null = null;
  const writes: string[] = [];
  const library = createProjectLibrary({
    read: async () => record,
    write: async (value) => {
      writes.push('write');
      record = structuredClone(value);
    },
    clear: async () => {
      writes.push('clear');
      record = null;
    },
  });
  const save = library.save('Disposable', 'stories', blankLongformDraft());
  const clear = library.clear();
  await Promise.all([save, clear]);
  expect(writes).toEqual(['write', 'clear']);
  expect(await library.list()).toEqual([]);
});
