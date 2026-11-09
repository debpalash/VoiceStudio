import { afterEach, expect, it, vi } from 'vitest';
import { onStorageKey } from '@/lib/storage-events';
import { installSyncedStorage } from './synced-storage';

const KEYS = ['voicestudio.theme.v2', 'voicestudio.clone.settings.v1', 'voicestudio.locale'];
const RAW = ['voicestudio.locale'];
let uninstall: (() => void) | undefined;
afterEach(() => {
  uninstall?.();
  uninstall = undefined;
  localStorage.clear();
});

function install() {
  const post = vi.fn(async (_key: string, _value: unknown) => {});
  const synced = installSyncedStorage({ storage: localStorage, keys: KEYS, rawKeys: RAW, post });
  uninstall = synced.uninstall;
  return { post, synced };
}

it('posts each change to a synced key once, in the format the allowlist declares', () => {
  const { post } = install();
  localStorage.setItem('voicestudio.clone.settings.v1', JSON.stringify({ steps: 16 }));
  localStorage.setItem('voicestudio.locale', 'true'); // a raw key stays a string even if it parses
  localStorage.removeItem('voicestudio.theme.v2');
  expect(post.mock.calls).toEqual([
    ['voicestudio.clone.settings.v1', { steps: 16 }],
    ['voicestudio.locale', 'true'],
    ['voicestudio.theme.v2', null],
  ]);
});

it('never posts a JSON key whose stored text cannot be parsed', () => {
  const { post } = install();
  localStorage.setItem('voicestudio.theme.v2', 'not json');
  expect(post).not.toHaveBeenCalled();
});

it('ignores keys that never sync and still stores them', () => {
  const { post } = install();
  localStorage.setItem('voicestudio.workspace-layout', 'x');
  expect(post).not.toHaveBeenCalled();
  expect(localStorage.getItem('voicestudio.workspace-layout')).toBe('x');
});

it('applies a pulled value without echoing it and tells the app it changed', () => {
  const { post, synced } = install();
  const seen: (string | null)[] = [];
  const stops = KEYS.map((key) => onStorageKey(key, () => seen.push(key)));
  synced.apply('voicestudio.locale', 'de');
  synced.apply('voicestudio.clone.settings.v1', { steps: 24 });
  synced.apply('voicestudio.theme.v2', null);
  stops.forEach((stop) => stop());
  expect(post).not.toHaveBeenCalled();
  expect(localStorage.getItem('voicestudio.locale')).toBe('de');
  expect(localStorage.getItem('voicestudio.clone.settings.v1')).toBe('{"steps":24}');
  expect(localStorage.getItem('voicestudio.theme.v2')).toBeNull();
  expect(seen).toEqual([
    'voicestudio.locale',
    'voicestudio.clone.settings.v1',
    'voicestudio.theme.v2',
  ]);
});

it('reads the current values of every synced key for the first mirror', () => {
  localStorage.setItem('voicestudio.locale', 'de');
  localStorage.setItem('voicestudio.theme.v2', '{"mode":"dark"}');
  const { synced } = install();
  expect(synced.snapshot()).toEqual([
    ['voicestudio.theme.v2', { mode: 'dark' }],
    ['voicestudio.locale', 'de'],
  ]);
});

it('uninstall restores the original storage methods', () => {
  const original = Storage.prototype.setItem;
  const { post } = install();
  uninstall!();
  uninstall = undefined;
  expect(Storage.prototype.setItem).toBe(original);
  localStorage.setItem('voicestudio.locale', 'de');
  expect(post).not.toHaveBeenCalled();
});

it('a failing post never breaks the write', () => {
  const post = vi.fn(async () => {
    throw new Error('backend down');
  });
  const synced = installSyncedStorage({ storage: localStorage, keys: KEYS, rawKeys: RAW, post });
  uninstall = synced.uninstall;
  expect(() => localStorage.setItem('voicestudio.locale', 'de')).not.toThrow();
  expect(localStorage.getItem('voicestudio.locale')).toBe('de');
});

it('a pulled value for a partly-synced key merges in and keeps device-local fields (the draft)', () => {
  const post = vi.fn(async () => {});
  const synced = installSyncedStorage({
    storage: localStorage,
    keys: KEYS,
    rawKeys: RAW,
    partialKeys: ['voicestudio.clone.settings.v1'],
    post,
  });
  uninstall = synced.uninstall;
  localStorage.setItem(
    'voicestudio.clone.settings.v1',
    JSON.stringify({ steps: 16, text: 'my draft', refText: 'ref', selectedProfileId: 'ab12cd34' }),
  );
  synced.apply('voicestudio.clone.settings.v1', { steps: 24, speed: 1.1 });
  expect(JSON.parse(localStorage.getItem('voicestudio.clone.settings.v1')!)).toEqual({
    steps: 24,
    speed: 1.1,
    text: 'my draft',
    refText: 'ref',
    selectedProfileId: 'ab12cd34',
  });
});
