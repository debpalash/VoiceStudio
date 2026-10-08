import { expect, it } from 'vitest';
import { DEFAULT_MODEL_DISK_HEADROOM_GB, modelDiskShortfall } from './model-disk-space';

it('names the download, the kept headroom and their total when the disk is short (#2597)', () => {
  // The reported case: a 4.8 GB pack with 10.2 GB free is refused because the
  // backend also keeps 10 GB free, so the warning must say so.
  expect(modelDiskShortfall(4.8, 10.2, 10)).toEqual({
    need: '4.8',
    headroom: 10,
    total: '14.8',
    free: 10.2,
  });
});

it('matches the backend rule exactly at the boundary', () => {
  expect(modelDiskShortfall(4, 14, 10)).toBeNull();
  expect(modelDiskShortfall(4, 13.9, 10)).not.toBeNull();
});

it('uses the backend headroom when provided and the default otherwise', () => {
  expect(modelDiskShortfall(2, 6, 3)).toBeNull();
  expect(modelDiskShortfall(2, 6, undefined)?.headroom).toBe(DEFAULT_MODEL_DISK_HEADROOM_GB);
});

it('stays silent without a download or a known free size', () => {
  expect(modelDiskShortfall(0, 1, 10)).toBeNull();
  expect(modelDiskShortfall(5, undefined, 10)).toBeNull();
  expect(modelDiskShortfall(5, null, 10)).toBeNull();
});
