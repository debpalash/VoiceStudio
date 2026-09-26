import {
  createIndexedDbLongformStore,
  type LongformDurableStore,
} from '@shared/utils/indexedDbLongformStore';
import type { Draft, Mode } from './longform-session';
export interface LongformProject {
  id: string;
  name: string;
  mode: Mode;
  updatedAt: number;
  draft: Draft;
}
/** A distinct DB keeps Electron records from overwriting Tauri's workspace envelope. */
export function createProjectLibrary(store: LongformDurableStore) {
  let queue: Promise<unknown> = Promise.resolve();
  const list = async (): Promise<LongformProject[]> => {
    const record = await store.read();
    if (!record) return [];
    const projects = record.payload.projects;
    if (
      !Array.isArray(projects) ||
      projects.some(
        (p) =>
          !p ||
          typeof p.id !== 'string' ||
          typeof p.name !== 'string' ||
          !['stories', 'audiobook'].includes(p.mode) ||
          !p.draft ||
          !Array.isArray(p.draft.lines) ||
          typeof p.draft.script !== 'string',
      )
    )
      throw new Error('Invalid project library');
    return projects;
  };
  const mutate = <T>(
    change: (projects: LongformProject[]) => { projects: LongformProject[]; result: T },
  ): Promise<T> => {
    const next = queue.then(async () => {
      const value = change(await list());
      await store.write({ schema: 1, payload: { projects: value.projects } });
      return value.result;
    });
    queue = next.catch(() => {});
    return next;
  };
  return {
    list,
    flush: () => queue.then(() => undefined),
    clear: () => {
      const next = queue.then(() => store.clear());
      queue = next.catch(() => {});
      return next;
    },
    save: (name: string, mode: Mode, draft: Draft, id?: string | null) => {
      const snapshot = structuredClone(draft);
      return mutate((projects) => {
        if (!name.trim()) throw new Error('Project name is required');
        if (id && !projects.some((project) => project.id === id))
          throw new Error('Project no longer exists');
        const project: LongformProject = {
          id: id || crypto.randomUUID(),
          name: name.trim(),
          mode,
          updatedAt: Date.now(),
          draft: snapshot,
        };
        project.draft.projectId = project.id;
        return {
          projects: [project, ...projects.filter((p) => p.id !== project.id)],
          result: project,
        };
      });
    },
    rename: (id: string, name: string) =>
      mutate((projects) => {
        if (!name.trim()) throw new Error('Project name is required');
        if (!projects.some((project) => project.id === id))
          throw new Error('Project no longer exists');
        return {
          projects: projects.map((project) =>
            project.id === id ? { ...project, name: name.trim(), updatedAt: Date.now() } : project,
          ),
          result: undefined,
        };
      }),
    remove: (id: string) =>
      mutate((projects) => ({ projects: projects.filter((p) => p.id !== id), result: undefined })),
  };
}
export const projectLibrary = createProjectLibrary(
  createIndexedDbLongformStore(undefined, 'voicestudio.electron.longform.projects'),
);
