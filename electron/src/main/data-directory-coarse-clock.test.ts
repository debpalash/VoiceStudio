// @vitest-environment node
import { type BigIntStats } from 'node:fs';
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { prepareDataRelocation } from './data-relocation';

const clock = vi.hoisted(() => ({
  probes: 0,
  mutable: false,
  frozen: false,
  copies: 0,
  elapsed: 0,
  advanceAfter: 0,
}));

vi.mock('node:timers/promises', () => ({
  setTimeout: async (milliseconds: number) => {
    clock.elapsed += milliseconds;
  },
}));

vi.mock('node:fs/promises', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs/promises')>();
  const coarse = (info: BigIntStats) => {
    if (!info.isDirectory()) return info;
    const changed =
      !clock.frozen &&
      (clock.advanceAfter ? clock.elapsed >= clock.advanceAfter : clock.probes >= 2);
    const ctimeNs = changed ? 200n : 100n;
    return Object.assign(Object.create(info), {
      birthtimeNs: clock.mutable ? ctimeNs : 100n,
      ctimeNs,
    });
  };
  return {
    ...actual,
    lstat: async (...args: Parameters<typeof actual.lstat>) => {
      const info = await actual.lstat(...args);
      return 'ctimeNs' in info ? coarse(info) : info;
    },
    stat: async (...args: Parameters<typeof actual.stat>) => {
      const info = await actual.stat(...args);
      return 'ctimeNs' in info ? coarse(info) : info;
    },
    open: async (...args: Parameters<typeof actual.open>) => {
      if (String(args[0]).includes('.voicestudio-identity-')) clock.probes++;
      return actual.open(...args);
    },
    cp: async (...args: Parameters<typeof actual.cp>) => {
      clock.copies++;
      return actual.cp(...args);
    },
  };
});

const roots: string[] = [];
afterEach(async () => {
  clock.probes = clock.copies = 0;
  clock.mutable = clock.frozen = false;
  clock.elapsed = clock.advanceAfter = 0;
  await Promise.all(roots.splice(0).map((path) => rm(path, { recursive: true, force: true })));
});

async function fixture(existing: boolean) {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'VoiceStudio-coarse-clock-')));
  roots.push(root);
  const source = join(root, 'source');
  const target = join(root, 'target');
  await mkdir(source);
  if (existing) await mkdir(target);
  await writeFile(join(source, 'omnivoice.db'), 'database');
  return { source, target };
}

it.each([true, false])(
  'relocates safely when the first probe stays in one clock tick (%s)',
  async (existing) => {
    const { source, target } = await fixture(existing);
    const move = await prepareDataRelocation(source, target);
    expect(clock.probes).toBeGreaterThanOrEqual(2);
    expect(await readFile(join(target, 'omnivoice.db'), 'utf8')).toBe('database');
    expect(await move.rollback()).toBe(true);
    expect(await readFile(join(source, 'omnivoice.db'), 'utf8')).toBe('database');
  },
);

it('rejects a mutable birthtime after the clock advances without copying', async () => {
  const { source, target } = await fixture(true);
  clock.mutable = true;
  await expect(prepareDataRelocation(source, target)).rejects.toThrow('identity_unavailable');
  expect(clock.copies).toBe(0);
  expect(await readFile(join(source, 'omnivoice.db'), 'utf8')).toBe('database');
});

it.each([
  [true, 100],
  [false, 100],
  [true, 1000],
  [false, 1000],
] as const)(
  'relocates safely across a slower clock tick (existing=%s, tick=%s ms)',
  async (existing, tick) => {
    const { source, target } = await fixture(existing);
    clock.advanceAfter = tick;
    const move = await prepareDataRelocation(source, target);
    expect(clock.elapsed).toBeGreaterThanOrEqual(tick);
    expect(clock.elapsed).toBeLessThan(1500);
    expect(await readFile(join(target, 'omnivoice.db'), 'utf8')).toBe('database');
    expect(await move.rollback()).toBe(true);
    expect(await readFile(join(source, 'omnivoice.db'), 'utf8')).toBe('database');
  },
);

it('rejects an unverifiable clock within a bounded number of probes', async () => {
  const { source, target } = await fixture(true);
  clock.frozen = true;
  await expect(prepareDataRelocation(source, target)).rejects.toThrow('identity_unavailable');
  expect(clock.probes).toBeLessThanOrEqual(5);
  expect(clock.copies).toBe(0);
});
