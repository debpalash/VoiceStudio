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

it('keeps the authenticated account identity encrypted with the session across refresh and restart', () => {
  const p = path();
  const store = new RefreshTokenStore(p, fakeSafe(), 'darwin');
  expect(store.bindAccount('Us3nM8qWe2RtY6uI')).toBe(false);
  store.save('refresh-one');
  expect(store.bindAccount('Us3nM8qWe2RtY6uI')).toBe(true);
  store.save('refresh-two');
  expect(readFileSync(p, 'utf8')).not.toContain('Us3nM8qWe2RtY6uI');
  const restarted = new RefreshTokenStore(p, fakeSafe(), 'darwin');
  expect(restarted.accountId()).toBe('Us3nM8qWe2RtY6uI');
  expect(restarted.load()).toBe('refresh-two');
  restarted.save('new-login', { newSession: true });
  expect(restarted.accountId()).toBeNull();
  restarted.bindAccount('Us3nM8qWe2RtY6uI');
  restarted.clear();
  expect(restarted.accountId()).toBeNull();
});

it('migrates legacy encrypted tokens without inventing an account identity', () => {
  const p = path();
  writeFileSync(
    p,
    JSON.stringify({ v: 1, token: fakeSafe().encryptString('old-refresh').toString('base64') }),
  );
  const store = new RefreshTokenStore(p, fakeSafe(), 'darwin');
  expect(store.accountId()).toBeNull();
  expect(store.load()).toBe('old-refresh');
  expect(store.bindAccount('email@example.com')).toBe(false);
  expect(store.bindAccount('Us3nM8qWe2RtY6uI')).toBe(true);
  expect(new RefreshTokenStore(p, fakeSafe(), 'darwin').accountId()).toBe('Us3nM8qWe2RtY6uI');
});

it('does not resurrect a signed-out session by rereading stale disk state', () => {
  const p = path();
  const store = new RefreshTokenStore(p, fakeSafe(), 'darwin');
  store.save('refresh');
  store.bindAccount('Us3nM8qWe2RtY6uI');
  const stale = readFileSync(p, 'utf8');
  store.clear();
  writeFileSync(p, stale);
  expect(store.load()).toBeNull();
  expect(store.accountId()).toBeNull();
});
