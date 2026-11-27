import { rmSync } from 'node:fs';

const RETRYABLE = new Set(['ENOTEMPTY', 'EBUSY', 'EPERM']);

/**
 * Removes a test's temporary folder. rmSync's own maxRetries repeats only the
 * final rmdir, so a file written after the folder was listed (a late log or
 * journal write) keeps failing with ENOTEMPTY. Each attempt here starts a fresh
 * recursive removal, which also deletes the late file.
 */
export function removeTempDir(dir: string, attempts = 5): void {
  for (let attempt = 1; ; attempt++) {
    try {
      rmSync(dir, { recursive: true, force: true, maxRetries: 2, retryDelay: 25 });
      return;
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code ?? '';
      if (attempt >= attempts || !RETRYABLE.has(code)) throw error;
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 25 * attempt);
    }
  }
}
