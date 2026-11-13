// @vitest-environment node
import { expect, it } from 'vitest';
import { join } from 'node:path';
import { scopedStorage, scopedStorageEnv } from './storage-scope';

const root = join('E:', 'VoiceStudio');

it('puts data, models and every temp variable under the chosen location (#2971)', () => {
  const s = scopedStorage(root);
  expect(scopedStorageEnv(root)).toEqual({
    OMNIVOICE_DATA_DIR: s.data,
    OMNIVOICE_CACHE_DIR: s.models,
    TEMP: s.temp,
    TMP: s.temp,
    TMPDIR: s.temp,
  });
  expect(s.data.startsWith(root) && s.models.startsWith(root) && s.temp.startsWith(root)).toBe(true);
});

it('keeps values the user already set explicitly', () => {
  const out = scopedStorageEnv(root, { OMNIVOICE_CACHE_DIR: 'D:\\models', TEMP: 'D:\\t' });
  expect(out.OMNIVOICE_CACHE_DIR).toBeUndefined();
  expect(out.TEMP).toBeUndefined();
  expect(out.OMNIVOICE_DATA_DIR).toBe(scopedStorage(root).data);
});
