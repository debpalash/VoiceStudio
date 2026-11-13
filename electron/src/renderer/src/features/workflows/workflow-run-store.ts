import type { WorkflowRun } from './workflow-runtime';

async function withStore<T>(mode: IDBTransactionMode, operation: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const database = await new Promise<IDBDatabase>((resolve, reject) => {
    let blocked = false;
    const request = indexedDB.open('voicestudio.workflow-runs', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('runs');
    request.onsuccess = () => {
      if (blocked) { request.result.close(); return; }
      request.result.onversionchange = () => request.result.close();
      resolve(request.result);
    };
    request.onerror = () => reject(request.error);
    request.onblocked = () => { blocked = true; reject(new Error('Workflow storage is blocked')); };
  });
  try {
    return await new Promise<T>((resolve, reject) => {
      const transaction = database.transaction('runs', mode);
      const request = operation(transaction.objectStore('runs'));
      transaction.oncomplete = () => resolve(request.result);
      transaction.onabort = transaction.onerror = () => reject(transaction.error || request.error);
    });
  } finally { database.close(); }
}
export async function readWorkflowRun(id: string): Promise<WorkflowRun | null> {
  const value = await withStore('readonly', (store) => store.get(id));
  if (!value || value.version !== 1 || typeof value.signature !== 'string' || !Array.isArray(value.items) || !value.items.length || value.items.length > 50) return null;
  if (value.items.some((item: unknown) => {
    if (!item || typeof item !== 'object') return true;
    const entry = item as Record<string, unknown>;
    if (entry.texts !== undefined && (!entry.texts || typeof entry.texts !== 'object' ||
      Object.values(entry.texts).some((text) => typeof text !== 'string'))) return true;
    if (entry.sourceId !== undefined && typeof entry.sourceId !== 'string') return true;
    if (entry.outputStep !== undefined && typeof entry.outputStep !== 'string') return true;
    return !['ready', 'running', 'done', 'failed', 'cancelled'].includes(String(entry.state)) || typeof entry.name !== 'string' || typeof entry.text !== 'string' || !entry.audio ||
      typeof entry.audio !== 'object' || Object.values(entry.audio).some((audio) => !(audio instanceof Blob) || audio.size === 0);
  })) return null;
  return value as WorkflowRun;
}
export async function writeWorkflowRun(id: string, run: WorkflowRun): Promise<void> {
  await withStore('readwrite', (store) => store.put(run, id));
}
export async function deleteWorkflowRun(id: string): Promise<void> {
  await withStore('readwrite', (store) => store.delete(id));
}

export async function saveWorkflowMedia(id: string, audio: Blob): Promise<void> {
  await withStore('readwrite', (store) => store.put(audio, 'media:' + id));
}
export async function loadWorkflowMedia(id: string): Promise<Blob> {
  const audio = await withStore('readonly', (store) => store.get('media:' + id));
  if (!(audio instanceof Blob) || !audio.size) throw new Error('workflowRun.invalid_media');
  return audio;
}

export async function deleteWorkflowArtifacts({ media, runs }: { media: string[]; runs: string[] }): Promise<void> {
  const keys = [...media.map((id) => 'media:' + id), ...runs];
  if (!keys.length) return;
  await withStore('readwrite', (store) => {
    for (const key of keys.slice(0, -1)) store.delete(key);
    return store.delete(keys[keys.length - 1]);
  });
}
