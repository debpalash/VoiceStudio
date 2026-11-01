// @vitest-environment node
import { expect, it, vi } from 'vitest';
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  DirectoryBookmarks,
  securityScopedDialogOptions,
  withSecurityScopedBookmark,
} from './security-scoped-files';

it('persists private folder grants, restores them on restart and releases them on quit', () => {
  const root = mkdtempSync(join(tmpdir(), 'folder-grants-'));
  const stop = vi.fn();
  const start = vi.fn(() => stop);
  const file = join(root, 'grants.json');
  const first = new DirectoryBookmarks(file, start);
  const second = new DirectoryBookmarks(file, start);
  try {
    first.retain(join(root, 'selected'), 'scoped-bookmark');
    if (process.platform !== 'win32') expect(statSync(file).mode & 0o077).toBe(0);
    expect(JSON.parse(readFileSync(file, 'utf8'))).toHaveLength(1);
    expect(readFileSync(file, 'utf8')).not.toContain(root);
    first.close();
    expect(second.restore()).toEqual([]);
    expect(start).toHaveBeenCalledWith('scoped-bookmark');
    second.close();
    second.close();
    expect(stop).toHaveBeenCalledTimes(2);
  } finally {
    first.close();
    second.close();
    rmSync(root, { recursive: true, force: true });
  }
});

it('keeps stale selections distinct from a new empty default and tolerates a corrupt journal', () => {
  const root = mkdtempSync(join(tmpdir(), 'folder-grants-'));
  const file = join(root, 'grants.json');
  try {
    writeFileSync(file, JSON.stringify([{ id: 'a'.repeat(64), bookmark: 'stale' }]));
    const stale = new DirectoryBookmarks(file, () => {
      throw new Error('stale bookmark');
    });
    expect(stale.restore()).toEqual(['a'.repeat(64)]);
    writeFileSync(file, '{corrupt');
    const corrupt = new DirectoryBookmarks(file, vi.fn());
    expect(corrupt.restore()).toEqual(['authorization_store_invalid']);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

it('leaves direct-download dialog options unchanged', () => {
  const options = { defaultPath: 'take.wav' };
  expect(securityScopedDialogOptions(options, false)).toBe(options);
});

it('requests a bookmark only for sandbox-scoped dialogs', () => {
  expect(securityScopedDialogOptions({ defaultPath: 'take.wav' }, true)).toEqual({
    defaultPath: 'take.wav',
    securityScopedBookmarks: true,
  });
});

it('holds sandbox access for the operation and always releases it', async () => {
  const stop = vi.fn();
  const start = vi.fn(() => stop);
  const operation = vi.fn(async () => {
    expect(start).toHaveBeenCalledWith('bookmark');
    expect(stop).not.toHaveBeenCalled();
    throw new Error('write failed');
  });
  await expect(withSecurityScopedBookmark('bookmark', operation, true, start)).rejects.toThrow(
    'write failed',
  );
  expect(stop).toHaveBeenCalledOnce();
});

it('fails closed when a sandbox dialog omits authorization', async () => {
  const operation = vi.fn(async () => undefined);
  await expect(withSecurityScopedBookmark(undefined, operation, true, vi.fn())).rejects.toThrow(
    'did not include sandbox authorization',
  );
  expect(operation).not.toHaveBeenCalled();
});
