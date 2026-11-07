import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { RefreshTokenStore, type SafeStorageLike } from './token-store';

const fakeSafe = (over: Partial<SafeStorageLike> = {}): SafeStorageLike => ({
  isEncryptionAvailable: () => true,
  getSelectedStorageBackend: () => 'gnome_libsecret',
  encryptString: (s) => Buffer.from(`enc:${s}`),
  decryptString: (b) => b.toString().replace(/^enc:/, ''),
  ...over,
});
const path = () => join(mkdtempSync(join(tmpdir(), 'vs-token-')), 'account.json');

it('round-trips through safeStorage and never writes the token in clear', () => {
  const p = path();
  new RefreshTokenStore(p, fakeSafe(), 'darwin').save('secret-refresh');
  expect(readFileSync(p, 'utf8')).not.toContain('secret-refresh');
  expect(new RefreshTokenStore(p, fakeSafe(), 'darwin').load()).toBe('secret-refresh');
});

it('basic_text on Linux keeps the token in memory only', () => {
  const p = path();
  const store = new RefreshTokenStore(
    p,
    fakeSafe({ getSelectedStorageBackend: () => 'basic_text' }),
    'linux',
  );
  store.save('t');
  expect(store.persistent).toBe(false);
  expect(existsSync(p)).toBe(false);
  expect(store.load()).toBe('t');
});

it('no encryption available keeps it in memory only', () => {
  const store = new RefreshTokenStore(
    path(),
    fakeSafe({ isEncryptionAvailable: () => false }),
    'win32',
  );
  expect(store.persistent).toBe(false);
});

it('a corrupt or undecryptable file loads as signed out', () => {
  const p = path();
  writeFileSync(p, '{"v":1,"token":"@@@');
  expect(new RefreshTokenStore(p, fakeSafe(), 'darwin').load()).toBeNull();
  writeFileSync(p, JSON.stringify({ v: 1, token: 'AAAA' }));
  const throwing = fakeSafe({
    decryptString: () => {
      throw new Error('keychain changed');
    },
  });
  expect(new RefreshTokenStore(p, throwing, 'darwin').load()).toBeNull();
});

it('clear removes the file and the memory copy', () => {
  const p = path();
  const store = new RefreshTokenStore(p, fakeSafe(), 'darwin');
  store.save('t');
  store.clear();
  expect(existsSync(p)).toBe(false);
  expect(store.load()).toBeNull();
});

it('never touches safeStorage until there is a token to read or write', () => {
  // On macOS safeStorage opens the Keychain, which can block startup on a prompt.
  const calls: string[] = [];
  const spy = fakeSafe({
    isEncryptionAvailable: () => (calls.push('isEncryptionAvailable'), true),
    getSelectedStorageBackend: () => (calls.push('getSelectedStorageBackend'), 'gnome_libsecret'),
    decryptString: (b) => (calls.push('decryptString'), b.toString().replace(/^enc:/, '')),
  });
  const p = path();
  const store = new RefreshTokenStore(p, spy, 'darwin');
  expect(store.load()).toBeNull();
  store.clear();
  expect(calls).toEqual([]);

  store.save('t');
  expect(calls).toEqual(['isEncryptionAvailable']);
  expect(new RefreshTokenStore(p, spy, 'darwin').load()).toBe('t');
  expect(calls).toEqual(['isEncryptionAvailable', 'isEncryptionAvailable', 'decryptString']);
});
