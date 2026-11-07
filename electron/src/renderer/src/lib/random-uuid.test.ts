import { afterEach, expect, it, vi } from 'vitest';
import { installRandomUUID } from './random-uuid';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('creates RFC 4122 version 4 UUIDs using only crypto.getRandomValues', () => {
  const getRandomValues = vi.fn((bytes: Uint8Array) => {
    bytes.fill(0xff);
    return bytes;
  });
  const mathRandom = vi.spyOn(Math, 'random');
  vi.stubGlobal('crypto', { getRandomValues });

  installRandomUUID();

  expect(crypto.randomUUID()).toBe('ffffffff-ffff-4fff-bfff-ffffffffffff');
  expect(getRandomValues).toHaveBeenCalledOnce();
  expect(getRandomValues.mock.calls[0][0]).toBeInstanceOf(Uint8Array);
  expect(getRandomValues.mock.calls[0][0]).toHaveLength(16);
  expect(mathRandom).not.toHaveBeenCalled();
});

it('leaves a native randomUUID implementation untouched', () => {
  const randomUUID = vi.fn();
  const getRandomValues = vi.fn();
  vi.stubGlobal('crypto', { randomUUID, getRandomValues });

  installRandomUUID();

  expect(crypto.randomUUID).toBe(randomUUID);
  expect(getRandomValues).not.toHaveBeenCalled();
});
