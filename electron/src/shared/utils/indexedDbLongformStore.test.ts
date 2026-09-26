import { describe, expect, it, vi } from 'vitest';

import { createIndexedDbLongformStore } from './indexedDbLongformStore';

function createReadableDatabase(value: unknown = undefined): IDBDatabase {
  const database = {
    close: vi.fn(),
    objectStoreNames: { contains: () => true },
    transaction: vi.fn(() => {
      const transaction: Record<string, unknown> = {
        error: null,
        objectStore: () => ({
          get: () => {
            const request: Record<string, unknown> = { error: null, result: value };
            queueMicrotask(() => {
              (request.onsuccess as (() => void) | undefined)?.();
              (transaction.oncomplete as (() => void) | undefined)?.();
            });
            return request;
          },
        }),
      };
      return transaction;
    }),
  };
  return database as unknown as IDBDatabase;
}

function createReadableFactory(...databases: IDBDatabase[]): IDBFactory {
  let databaseIndex = 0;
  return {
    open: vi.fn(() => {
      const database = databases[databaseIndex++] ?? createReadableDatabase();
      const request: Record<string, unknown> = { error: null, result: database };
      queueMicrotask(() => (request.onsuccess as (() => void) | undefined)?.());
      return request;
    }),
  } as unknown as IDBFactory;
}

describe('IndexedDB long-form store', () => {
  it('reopens after the factory throws synchronously', async () => {
    const factory = createReadableFactory(createReadableDatabase());
    const getFactory = vi
      .fn<() => IDBFactory>()
      .mockImplementationOnce(() => {
        throw new DOMException('content intentionally omitted', 'UnknownError');
      })
      .mockReturnValue(factory);
    const store = createIndexedDbLongformStore(getFactory);

    await expect(store.read()).rejects.toMatchObject({ name: 'UnknownError' });
    await expect(store.read()).resolves.toBeNull();
    expect(getFactory).toHaveBeenCalledTimes(2);
  });

  it('reopens after the cached connection closes unexpectedly', async () => {
    const firstDatabase = createReadableDatabase();
    const secondDatabase = createReadableDatabase();
    const factory = createReadableFactory(firstDatabase, secondDatabase);
    const store = createIndexedDbLongformStore(() => factory);

    await expect(store.read()).resolves.toBeNull();
    firstDatabase.onclose?.(new Event('close'));
    await expect(store.read()).resolves.toBeNull();

    expect(factory.open).toHaveBeenCalledTimes(2);
  });

  it('invalidates a dead connection after transaction InvalidStateError', async () => {
    const deadDatabase = createReadableDatabase();
    vi.mocked(deadDatabase.transaction).mockImplementation(() => {
      throw new DOMException('content intentionally omitted', 'InvalidStateError');
    });
    const healthyDatabase = createReadableDatabase();
    const factory = createReadableFactory(deadDatabase, healthyDatabase);
    const store = createIndexedDbLongformStore(() => factory);

    await expect(store.read()).rejects.toMatchObject({ name: 'InvalidStateError' });
    await expect(store.read()).resolves.toBeNull();

    expect(deadDatabase.close).toHaveBeenCalledOnce();
    expect(factory.open).toHaveBeenCalledTimes(2);
  });

  it('rejects a present record with an unsupported schema', async () => {
    const malformed = {
      schema: 999,
      revision: 4,
      payload: { script: 'only durable copy' },
    };
    const factory = createReadableFactory(createReadableDatabase(malformed));
    const store = createIndexedDbLongformStore(() => factory);

    await expect(store.read()).rejects.toMatchObject({ name: 'DataError' });
  });
});
