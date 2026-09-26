export const LONGFORM_DB_NAME = 'omnivoice.longform';
const LONGFORM_DB_STORE = 'documents';
const LONGFORM_DB_RECORD_ID = 'workspace';
export const LONGFORM_DB_SCHEMA = 1;

export interface DurableLongformRecord {
  schema: typeof LONGFORM_DB_SCHEMA;
  /** Monotonic writer revision; absent records are treated as legacy revision 0. */
  revision?: number;
  payload: Record<string, unknown>;
}

export interface LongformDurableStore {
  read(): Promise<DurableLongformRecord | null>;
  write(record: DurableLongformRecord): Promise<void>;
  clear(): Promise<void>;
}

function isDurableRecord(value: unknown): value is DurableLongformRecord {
  if (value === null || typeof value !== 'object') return false;
  const record = value as Partial<DurableLongformRecord>;
  return (
    record.schema === LONGFORM_DB_SCHEMA &&
    (record.revision === undefined ||
      (Number.isSafeInteger(record.revision) && record.revision >= 0)) &&
    record.payload !== null &&
    typeof record.payload === 'object' &&
    !Array.isArray(record.payload)
  );
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(request.error ?? new DOMException('Request failed', 'UnknownError'));
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () =>
      reject(transaction.error ?? new DOMException('Transaction aborted', 'AbortError'));
    transaction.onerror = () =>
      reject(transaction.error ?? new DOMException('Transaction failed', 'UnknownError'));
  });
}

/** Native IndexedDB implementation; no runtime package or network dependency. */
export function createIndexedDbLongformStore(
  getFactory: () => IDBFactory = () => globalThis.indexedDB,
  databaseName = LONGFORM_DB_NAME,
): LongformDurableStore {
  let databasePromise: Promise<IDBDatabase> | null = null;

  function invalidateDatabase(
    database: IDBDatabase,
    opening: Promise<IDBDatabase>,
    close: boolean,
  ): void {
    if (close) {
      try {
        database.close();
      } catch {
        // The connection is already unusable; clearing the cache is enough.
      }
    }
    if (databasePromise === opening) databasePromise = null;
  }

  function openDatabase(): Promise<IDBDatabase> {
    if (databasePromise) return databasePromise;
    const opening = new Promise<IDBDatabase>((resolve, reject) => {
      let request: IDBOpenDBRequest;
      try {
        const factory = getFactory();
        if (!factory) throw new DOMException('IndexedDB unavailable', 'NotSupportedError');
        request = factory.open(databaseName, LONGFORM_DB_SCHEMA);
      } catch (error) {
        reject(error);
        return;
      }

      let settled = false;
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(LONGFORM_DB_STORE)) {
          request.result.createObjectStore(LONGFORM_DB_STORE);
        }
      };
      request.onsuccess = () => {
        if (settled) {
          request.result.close();
          return;
        }
        settled = true;
        const database = request.result;
        database.onversionchange = () => {
          invalidateDatabase(database, opening, true);
        };
        database.onclose = () => invalidateDatabase(database, opening, false);
        resolve(database);
      };
      request.onerror = () => {
        if (settled) return;
        settled = true;
        reject(request.error ?? new DOMException('Database open failed', 'UnknownError'));
      };
      request.onblocked = () => {
        if (settled) return;
        settled = true;
        reject(new DOMException('Database open blocked', 'InvalidStateError'));
      };
    });
    databasePromise = opening;
    void opening.catch(() => {
      // The Promise executor runs synchronously, so clearing databasePromise
      // inside its catch path is overwritten by the assignment above. Clear the
      // exact cached attempt only after rejection instead, allowing a retry.
      if (databasePromise === opening) databasePromise = null;
    });
    return opening;
  }

  async function withDatabase<T>(operation: (database: IDBDatabase) => Promise<T>): Promise<T> {
    const opening = openDatabase();
    const database = await opening;
    try {
      return await operation(database);
    } catch (error) {
      if (
        error !== null &&
        typeof error === 'object' &&
        (error as { name?: unknown }).name === 'InvalidStateError'
      ) {
        invalidateDatabase(database, opening, true);
      }
      throw error;
    }
  }

  return {
    read() {
      return withDatabase(async (database) => {
        const transaction = database.transaction(LONGFORM_DB_STORE, 'readonly');
        const request = transaction.objectStore(LONGFORM_DB_STORE).get(LONGFORM_DB_RECORD_ID);
        const [value] = await Promise.all([requestResult(request), transactionDone(transaction)]);
        if (value === undefined) return null;
        if (!isDurableRecord(value)) throw new DOMException('', 'DataError');
        return value;
      });
    },
    write(record) {
      return withDatabase(async (database) => {
        const transaction = database.transaction(LONGFORM_DB_STORE, 'readwrite');
        transaction.objectStore(LONGFORM_DB_STORE).put(record, LONGFORM_DB_RECORD_ID);
        await transactionDone(transaction);
      });
    },
    clear() {
      return withDatabase(async (database) => {
        const transaction = database.transaction(LONGFORM_DB_STORE, 'readwrite');
        transaction.objectStore(LONGFORM_DB_STORE).delete(LONGFORM_DB_RECORD_ID);
        await transactionDone(transaction);
      });
    },
  };
}
