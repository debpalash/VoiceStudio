import { app } from 'electron';
import { randomUUID } from 'node:crypto';
import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, isAbsolute, join } from 'node:path';

import { currentDistributionProfile } from './distribution';

interface DirectoryGrant {
  path: string;
  bookmark: string;
}

/** Keep each explicitly chosen directory available to the backend and its children. */
export class DirectoryBookmarks {
  private records = new Map<string, string>();
  private leases = new Map<string, () => void>();
  constructor(
    private readonly file: string,
    private readonly start: (bookmark: string) => () => void,
  ) {}

  restore(): string[] {
    let raw: unknown;
    try {
      raw = JSON.parse(readFileSync(this.file, 'utf8'));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
      return ['authorization_store_invalid'];
    }
    if (
      !Array.isArray(raw) ||
      raw.some(
        (item) =>
          !item ||
          typeof item.path !== 'string' ||
          !isAbsolute(item.path) ||
          typeof item.bookmark !== 'string' ||
          !item.bookmark,
      ) ||
      new Set(raw.map((item) => item.path)).size !== raw.length
    ) {
      return ['authorization_store_invalid'];
    }
    const failed: string[] = [];
    for (const item of raw as DirectoryGrant[]) {
      this.records.set(item.path, item.bookmark);
      try {
        this.leases.set(item.path, this.start(item.bookmark));
      } catch {
        failed.push(item.path);
      }
    }
    return failed;
  }

  retain(path: string, bookmark: string | undefined): void {
    if (!bookmark || !isAbsolute(path))
      throw new Error('The selected file did not include sandbox authorization');
    const stop = this.start(bookmark);
    try {
      const entries = new Map(this.records).set(path, bookmark);
      mkdirSync(dirname(this.file), { recursive: true });
      const temporary = `${this.file}.${randomUUID()}.tmp`;
      try {
        writeFileSync(
          temporary,
          JSON.stringify([...entries].map(([path, bookmark]) => ({ path, bookmark }))),
          { mode: 0o600, flag: 'wx' },
        );
        renameSync(temporary, this.file);
      } finally {
        rmSync(temporary, { force: true });
      }
      this.leases.get(path)?.();
      this.leases.set(path, stop);
      this.records = entries;
    } catch (error) {
      stop();
      throw error;
    }
  }

  close(): void {
    this.leases.forEach((stop) => {
      try {
        stop();
      } catch {
        /* Shutdown still releases the remaining grants. */
      }
    });
    this.leases.clear();
  }
}

let directories: DirectoryBookmarks | undefined;

export function restoreSecurityScopedDirectories(): () => void {
  if (!securityScopedFileAccessSupported()) return () => {};
  directories = new DirectoryBookmarks(
    join(app.getPath('userData'), 'security-scoped-directories.json'),
    (bookmark) => {
      const stop = app.startAccessingSecurityScopedResource(bookmark);
      return () => {
        stop();
      };
    },
  );
  const failed = directories.restore();
  if (failed.length)
    console.error('Sandbox folder authorization needs renewal for a previously selected directory');
  return () => {
    directories?.close();
    directories = undefined;
  };
}

export function retainSecurityScopedDirectory(picked: Electron.OpenDialogReturnValue): void {
  if (!securityScopedFileAccessSupported() || picked.canceled) return;
  if (!directories) throw new Error('Sandbox folder authorization is not initialized');
  picked.filePaths.forEach((path, index) => directories!.retain(path, picked.bookmarks?.[index]));
}

export function securityScopedFileAccessSupported(
  packaged = app.isPackaged,
  platform = process.platform,
): boolean {
  return (
    packaged &&
    platform === 'darwin' &&
    currentDistributionProfile().fileAccess === 'sandbox-scoped'
  );
}

export function securityScopedDialogOptions<T extends object>(
  options: T,
  enabled = securityScopedFileAccessSupported(),
): T & { securityScopedBookmarks?: boolean } {
  return enabled ? { ...options, securityScopedBookmarks: true } : options;
}

export async function withSecurityScopedBookmark<T>(
  bookmark: string | undefined,
  operation: () => Promise<T>,
  enabled = securityScopedFileAccessSupported(),
  startAccess = (value: string) => app.startAccessingSecurityScopedResource(value),
): Promise<T> {
  if (!enabled) return operation();
  if (!bookmark) throw new Error('The selected file did not include sandbox authorization');
  const stopAccess = startAccess(bookmark);
  try {
    return await operation();
  } finally {
    stopAccess();
  }
}
