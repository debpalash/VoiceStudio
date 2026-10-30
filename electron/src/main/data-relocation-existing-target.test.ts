// @vitest-environment node
import { existsSync, type BigIntStats } from 'node:fs';
import {
  appendFile,
  chmod,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
  stat,
  symlink,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, sep } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import {
  prepareDataRelocation,
  inspectDataRelocation,
  readDataDirectorySetting,
  relocateDataDirectory,
} from './data-relocation';

const constraints = vi.hoisted(() => ({
  target: '',
  parent: '',
  checkCapacity: false,
  zeroStagingIdentity: false,
  mutableBirthtime: false,
  stableBirthtime: false,
  stableBirthtimes: new Map<string, bigint>(),
  staleProbes: 0,
  staleProbeTarget: '',
  copyCalls: 0,
  beforeReadFile: null as ((path: string) => Promise<void>) | null,
  beforeOpen: null as ((path: string) => Promise<void>) | null,
  afterCopy: null as ((path: string) => Promise<void>) | null,
  collisionDirectory: '',
  failRead: '',
  unsupportedPath: '',
  largeIdentityPath: '',
  identityVersion: 0,
  collisionBirthtime: 0n,
  collisionCtime: null as bigint | null,
  reuseEnabled: false,
  reuseAllMetadata: true,
  reusedIdentityPath: '',
  reusedStats: null as BigIntStats | null,
}));
vi.mock('node:fs/promises', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs/promises')>();
  return {
    ...actual,
    cp: async (...args: Parameters<typeof actual.cp>) => {
      constraints.copyCalls++;
      await actual.cp(...args);
      await constraints.afterCopy?.(String(args[1]));
    },
    mkdir: async (...args: Parameters<typeof actual.mkdir>) => {
      if (constraints.parent && dirname(String(args[0])) === constraints.parent) {
        throw Object.assign(new Error('parent is not writable'), { code: 'EACCES' });
      }
      if (String(args[0]) === constraints.collisionDirectory) {
        await actual.mkdir(...args);
        await actual.writeFile(join(String(args[0]), 'concurrent.txt'), 'keep');
      }
      return actual.mkdir(...args);
    },
    rmdir: async (...args: Parameters<typeof actual.rmdir>) => {
      if (String(args[0]) === constraints.target) {
        throw Object.assign(new Error('destination is a mount point'), { code: 'EBUSY' });
      }
      return actual.rmdir(...args);
    },
    stat: async (...args: Parameters<typeof actual.stat>) => {
      const info = await actual.stat(...args);
      if (constraints.stableBirthtime && info.isDirectory() && 'ctimeNs' in info) {
        const key = `${info.dev}:${info.ino}`;
        if (!constraints.stableBirthtimes.has(key))
          constraints.stableBirthtimes.set(key, info.ctimeNs);
        return Object.assign(Object.create(info), {
          birthtimeNs: constraints.stableBirthtimes.get(key),
        });
      }
      return constraints.mutableBirthtime && info.isDirectory() && 'ctimeNs' in info
        ? Object.assign(Object.create(info), { birthtimeNs: info.ctimeNs })
        : info;
    },
    lstat: async (...args: Parameters<typeof actual.lstat>) => {
      const info = await actual.lstat(...args);
      if (constraints.stableBirthtime && info.isDirectory() && 'ctimeNs' in info) {
        const key = `${info.dev}:${info.ino}`;
        if (!constraints.stableBirthtimes.has(key))
          constraints.stableBirthtimes.set(key, info.ctimeNs);
        const birthtimeNs = constraints.stableBirthtimes.get(key);
        // A coarse kernel clock leaves ctime unchanged across a same-tick probe.
        if (String(args[0]) === constraints.staleProbeTarget) {
          constraints.staleProbeTarget = '';
          return Object.assign(Object.create(info), { birthtimeNs, ctimeNs: birthtimeNs });
        }
        return Object.assign(Object.create(info), { birthtimeNs });
      }
      if (constraints.mutableBirthtime && info.isDirectory() && 'ctimeNs' in info)
        return Object.assign(Object.create(info), { birthtimeNs: info.ctimeNs });
      if (constraints.zeroStagingIdentity && String(args[0]).includes('.voicestudio-moving-'))
        return Object.assign(Object.create(info), { birthtimeNs: 0n });
      if (
        constraints.reuseEnabled &&
        String(args[0]) === constraints.reusedIdentityPath &&
        constraints.reusedStats
      ) {
        const prior = constraints.reusedStats;
        if (!constraints.reuseAllMetadata)
          return Object.assign(Object.create(info), {
            dev: typeof info.dev === 'bigint' ? prior.dev : Number(prior.dev),
            ino: typeof info.ino === 'bigint' ? prior.ino : Number(prior.ino),
          });
        return Object.assign(Object.create(info), {
          dev: typeof info.dev === 'bigint' ? prior.dev : Number(prior.dev),
          ino: typeof info.ino === 'bigint' ? prior.ino : Number(prior.ino),
          birthtimeNs: constraints.collisionBirthtime,
          ctimeNs: prior.ctimeNs,
          size: typeof info.size === 'bigint' ? prior.size : Number(prior.size),
          mode: typeof info.mode === 'bigint' ? prior.mode : Number(prior.mode),
        });
      }
      if (String(args[0]) === constraints.largeIdentityPath) {
        const ino = 9007199254740992n + BigInt(constraints.identityVersion);
        return Object.assign(Object.create(info), {
          ino: typeof info.ino === 'bigint' ? ino : Number(ino),
        });
      }
      return String(args[0]) === constraints.unsupportedPath
        ? Object.assign(Object.create(info), { isFile: () => false })
        : info;
    },
    readFile: async (...args: Parameters<typeof actual.readFile>) => {
      await constraints.beforeReadFile?.(String(args[0]));
      return actual.readFile(...args);
    },
    open: async (...args: Parameters<typeof actual.open>) => {
      await constraints.beforeOpen?.(String(args[0]));
      if (constraints.staleProbes > 0 && String(args[0]).includes('.voicestudio-identity-')) {
        constraints.staleProbes--;
        constraints.staleProbeTarget = dirname(String(args[0]));
      }
      const handle = await actual.open(...args);
      if (String(args[0]) === constraints.reusedIdentityPath && args[1] === 'wx') {
        const originalStat = handle.stat.bind(handle);
        handle.stat = (async (options: Parameters<typeof handle.stat>[0]) => {
          const info = await originalStat(options);
          if (!('ctimeNs' in info)) return info;
          const snapshot = constraints.reuseAllMetadata
            ? Object.assign(Object.create(info), {
                birthtimeNs: constraints.collisionBirthtime,
                ctimeNs: constraints.collisionCtime ?? info.ctimeNs,
              })
            : info;
          constraints.reusedStats = snapshot;
          return snapshot;
        }) as typeof handle.stat;
      }
      return handle;
    },
    statfs: async (...args: Parameters<typeof actual.statfs>) => {
      if (constraints.checkCapacity) {
        return { bavail: String(args[0]) === constraints.target ? 1024 ** 3 : 0, bsize: 1 };
      }
      return actual.statfs(...args);
    },
  };
});

vi.mock('node:fs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs')>();
  const { Readable } = await import('node:stream');
  return {
    ...actual,
    createReadStream: (...args: Parameters<typeof actual.createReadStream>) => {
      if (String(args[0]) === constraints.failRead) {
        return Readable.from(
          (async function* () {
            yield 'partial';
            throw Object.assign(new Error('read interrupted'), { code: 'EIO' });
          })(),
        );
      }
      return actual.createReadStream(...args);
    },
  };
});

const created: string[] = [];
const readOnlyDirectories: string[] = [];
afterEach(async () => {
  await Promise.all(
    readOnlyDirectories.splice(0).map((path) => chmod(path, 0o755).catch(() => undefined)),
  );
  constraints.target = constraints.parent = '';
  constraints.checkCapacity = false;
  constraints.zeroStagingIdentity = false;
  constraints.mutableBirthtime = false;
  constraints.stableBirthtime = false;
  constraints.stableBirthtimes.clear();
  constraints.staleProbes = 0;
  constraints.staleProbeTarget = '';
  constraints.copyCalls = 0;
  constraints.beforeOpen = constraints.beforeReadFile = constraints.afterCopy = null;
  constraints.largeIdentityPath = '';
  constraints.identityVersion = 0;
  constraints.reusedIdentityPath = '';
  constraints.reuseEnabled = false;
  constraints.collisionBirthtime = 0n;
  constraints.collisionCtime = null;
  constraints.reuseAllMetadata = true;
  constraints.reusedStats = null;
  constraints.collisionDirectory = constraints.failRead = constraints.unsupportedPath = '';
  await Promise.all(created.splice(0).map((path) => rm(path, { recursive: true, force: true })));
});

async function fixture() {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'VoiceStudio-existing-target-')));
  created.push(root);
  const source = join(root, 'old', 'data');
  const target = join(root, 'mounted', 'data');
  await mkdir(join(source, 'voices'), { recursive: true });
  await mkdir(target, { recursive: true });
  await writeFile(join(source, 'voices', 'sample.wav'), 'voice');
  await writeFile(join(source, 'omnivoice.db'), 'database');
  return { root, source, target };
}

function incomplete(cause: string | Record<string, unknown>) {
  return {
    message: 'rollback_incomplete',
    cause: expect.objectContaining(typeof cause === 'string' ? { message: cause } : cause),
  };
}

it.each([true, false])(
  'preserves a writable mounted destination beneath a protected parent (activation: %s)',
  async (activate) => {
    const { root, source, target } = await fixture();
    const environmentPath = join(root, 'env');
    await writeFile(environmentPath, `OMNIVOICE_DATA_DIR=${source}\n`);
    const inode = (await lstat(target)).ino;
    constraints.target = target;
    constraints.parent = dirname(target);
    const move = relocateDataDirectory(source, target, {
      environmentPath,
      stop: async () => undefined,
      start: async () => undefined,
      verify: async () => {
        expect(await readFile(join(target, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
        await writeFile(join(target, 'personal.txt'), 'keep');
        return activate;
      },
      progress: () => undefined,
    });
    if (activate) await expect(move).resolves.toMatchObject({ removed_source: true });
    else await expect(move).rejects.toMatchObject(incomplete('backend_verification_failed'));
    expect((await lstat(target)).ino).toBe(inode);
    expect(await readFile(join(target, 'personal.txt'), 'utf8')).toBe('keep');
    expect(existsSync(source)).toBe(!activate);
    expect(await readDataDirectorySetting(environmentPath)).toBe(activate ? target : source);
    if (!activate) expect(await readdir(target)).toEqual(['personal.txt']);
  },
);

it('checks capacity on the selected existing filesystem rather than its parent', async () => {
  const { source, target } = await fixture();
  constraints.target = target;
  constraints.checkCapacity = true;
  await expect(inspectDataRelocation(source, target)).resolves.toMatchObject({ target });
});

it('refuses a concurrently created file and rolls back only its earlier entries', async () => {
  const { source, target } = await fixture();
  constraints.beforeOpen = async (path) => {
    if (path === join(target, 'voices', 'sample.wav')) await writeFile(path, 'concurrent');
  };
  await expect(prepareDataRelocation(source, target)).rejects.toMatchObject(
    incomplete({ code: 'EEXIST' }),
  );
  expect(await readFile(join(target, 'voices', 'sample.wav'), 'utf8')).toBe('concurrent');
  expect(await readdir(target)).toEqual(['voices']);
  expect(await readFile(join(source, 'omnivoice.db'), 'utf8')).toBe('database');
});

it('refuses a concurrently created directory without merging into it', async () => {
  const { source, target } = await fixture();
  constraints.collisionDirectory = join(target, 'voices');
  await expect(prepareDataRelocation(source, target)).rejects.toMatchObject(
    incomplete({ code: 'EEXIST' }),
  );
  expect(await readdir(target)).toEqual(['voices']);
  expect(await readdir(join(target, 'voices'))).toEqual(['concurrent.txt']);
});

it('cleans a partial exclusive copy after a read failure and keeps the source', async () => {
  const { source, target } = await fixture();
  constraints.failRead = join(source, 'voices', 'sample.wav');
  await expect(prepareDataRelocation(source, target)).rejects.toThrow('read interrupted');
  expect(await readdir(target)).toEqual([]);
  expect(await readFile(join(source, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
});

it('rollback keeps replacements and new files inside an owned directory', async () => {
  const { root, source, target } = await fixture();
  const prepared = await prepareDataRelocation(source, target);
  await writeFile(join(target, 'voices', 'concurrent.txt'), 'keep nested');
  const replacement = join(root, 'replacement');
  await writeFile(replacement, 'keep replacement');
  await rename(replacement, join(target, 'omnivoice.db'));
  await prepared.rollback();
  expect(await readdir(target)).toEqual(['omnivoice.db', 'voices']);
  expect(await readFile(join(target, 'omnivoice.db'), 'utf8')).toBe('keep replacement');
  expect(await readdir(join(target, 'voices'))).toEqual(['concurrent.txt']);
  expect(existsSync(source)).toBe(true);
});

it('failed activation rolls back copied longform cache entries before advisory writes', async () => {
  const { root, source, target } = await fixture();
  const cache = join(source, 'outputs', 'longform_cache');
  await mkdir(cache, { recursive: true });
  await writeFile(join(cache, 'voices_roots.json'), JSON.stringify(['/previous/voices']));
  await expect(
    relocateDataDirectory(source, target, {
      environmentPath: join(root, 'env'),
      stop: async () => undefined,
      start: async () => undefined,
      verify: async () => false,
      progress: () => undefined,
    }),
  ).rejects.toThrow('backend_verification_failed');
  expect(await readdir(target)).toEqual([]);
  expect(await readFile(join(cache, 'voices_roots.json'), 'utf8')).toBe(
    JSON.stringify(['/previous/voices']),
  );
});

it('rollback never traverses a directory replaced concurrently', async () => {
  const { root, source, target } = await fixture();
  const prepared = await prepareDataRelocation(source, target);
  await rename(join(target, 'voices'), join(root, 'moved-voices'));
  await mkdir(join(target, 'voices'));
  await writeFile(join(target, 'voices', 'sample.wav'), 'unrelated');
  await prepared.rollback();
  expect(await readdir(target)).toEqual(['voices']);
  expect(await readFile(join(target, 'voices', 'sample.wav'), 'utf8')).toBe('unrelated');
});

it('rejects unsupported source entries rather than silently dropping them', async () => {
  const { source, target } = await fixture();
  constraints.unsupportedPath = join(source, 'voices', 'sample.wav');
  await expect(prepareDataRelocation(source, target)).rejects.toThrow('unsupported_entry');
  expect(await readdir(target)).toEqual([]);
  expect(await readFile(join(source, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
});

// Windows does not enforce POSIX directory modes; root bypasses write checks.
it.skipIf(process.platform === 'win32' || process.getuid?.() === 0)(
  'copies read-only model directories and files, preserves modes, and can roll back',
  async () => {
    const { source, target } = await fixture();
    const sourceVoices = join(source, 'voices');
    const targetVoices = join(target, 'voices');
    readOnlyDirectories.push(sourceVoices, targetVoices);
    await chmod(sourceVoices, 0o555);
    await chmod(join(sourceVoices, 'sample.wav'), 0o444);
    const prepared = await prepareDataRelocation(source, target);
    expect(await readFile(join(targetVoices, 'sample.wav'), 'utf8')).toBe('voice');
    expect((await lstat(targetVoices)).mode & 0o777).toBe(0o555);
    expect((await lstat(join(targetVoices, 'sample.wav'))).mode & 0o777).toBe(0o444);
    await prepared.rollback();
    expect(await readdir(target)).toEqual([]);
    expect((await lstat(sourceVoices)).mode & 0o777).toBe(0o555);
  },
);

it.skipIf(process.platform === 'win32' || process.getuid?.() === 0)(
  'failed activation removes owned files from read-only directories but restores modes on surviving data',
  async () => {
    const { root, source, target } = await fixture();
    const sourceVoices = join(source, 'voices');
    const targetVoices = join(target, 'voices');
    readOnlyDirectories.push(sourceVoices, targetVoices);
    await chmod(sourceVoices, 0o555);
    await expect(
      relocateDataDirectory(source, target, {
        environmentPath: join(root, 'env'),
        stop: async () => undefined,
        start: async () => undefined,
        verify: async () => {
          expect((await lstat(targetVoices)).mode & 0o777).toBe(0o555);
          await chmod(targetVoices, 0o755);
          await writeFile(join(targetVoices, 'concurrent.txt'), 'keep');
          await chmod(targetVoices, 0o555);
          return false;
        },
        progress: () => undefined,
      }),
    ).rejects.toMatchObject(incomplete('backend_verification_failed'));
    expect(await readdir(target)).toEqual(['voices']);
    expect(await readdir(targetVoices)).toEqual(['concurrent.txt']);
    expect((await lstat(targetVoices)).mode & 0o777).toBe(0o555);
    expect((await lstat(sourceVoices)).mode & 0o777).toBe(0o555);
  },
);

it('does not claim a replacement directory whose 64-bit file ID rounds to the same Number', async () => {
  const { root, source, target } = await fixture();
  const voices = join(target, 'voices');
  constraints.largeIdentityPath = voices;
  const prepared = await prepareDataRelocation(source, target);
  await rename(voices, join(root, 'moved-voices'));
  await mkdir(voices);
  constraints.identityVersion = 1;
  // These distinct filesystem IDs collide if read through numeric Stats.
  expect(Number(9007199254740992n)).toBe(Number(9007199254740993n));
  await prepared.rollback();
  expect(existsSync(voices)).toBe(true);
});

it.skipIf(process.platform === 'win32' || process.getuid?.() === 0)(
  'restores read-only modes on surviving concurrent data after a copy failure',
  async () => {
    const { source, target } = await fixture();
    const sourceVoices = join(source, 'voices');
    const targetVoices = join(target, 'voices');
    readOnlyDirectories.push(sourceVoices, targetVoices);
    await chmod(sourceVoices, 0o555);
    constraints.beforeOpen = async (path) => {
      if (path === join(targetVoices, 'sample.wav')) {
        await writeFile(join(targetVoices, 'concurrent.txt'), 'keep');
        throw Object.assign(new Error('copy interrupted'), { code: 'EIO' });
      }
    };
    await expect(prepareDataRelocation(source, target)).rejects.toMatchObject(
      incomplete('copy interrupted'),
    );
    expect(await readdir(targetVoices)).toEqual(['concurrent.txt']);
    expect((await lstat(targetVoices)).mode & 0o777).toBe(0o555);
  },
);

async function makeLongformCache(source: string, roots: unknown[] | null) {
  const cache = join(source, 'outputs', 'longform_cache');
  await mkdir(cache, { recursive: true });
  if (roots !== null) await writeFile(join(cache, 'voices_roots.json'), JSON.stringify(roots));
}

// Models the startup writer's observable atomic replace (longform_render.py),
// including filtering, ordering, and its history cap.
async function backendStartupIndex(target: string) {
  const cache = join(target, 'outputs', 'longform_cache');
  if (!existsSync(cache)) return;
  const file = join(cache, 'voices_roots.json');
  let seen: string[] = [];
  try {
    const parsed: unknown = JSON.parse(await readFile(file, 'utf8'));
    if (Array.isArray(parsed))
      seen = parsed.filter((root): root is string => typeof root === 'string' && root.length > 0);
  } catch {
    /* Same best-effort absent/invalid-file behavior as startup. */
  }
  const current = target + sep + 'voices';
  if (seen[0] === current) return;
  const temporary = file + '.backend-start.tmp';
  await writeFile(
    temporary,
    JSON.stringify([current, ...seen.filter((root) => root !== current)].slice(0, 8)),
  );
  await rename(temporary, file);
}

it.each([
  { name: 'existing index', roots: ['/previous/voices'] },
  { name: 'empty cache', roots: null },
  {
    name: 'filtered and capped history',
    roots: [null, 12, '', ...Array.from({ length: 12 }, (_, index) => `/previous/${index}/voices`)],
  },
])('rolls back the backend startup index in an owned $name', async ({ roots }) => {
  const { root, source, target } = await fixture();
  await makeLongformCache(source, roots);
  let starts = 0;
  await expect(
    relocateDataDirectory(source, target, {
      environmentPath: join(root, 'env'),
      stop: async () => undefined,
      start: async () => {
        if (++starts === 1) await backendStartupIndex(target);
      },
      verify: async () => false,
      progress: () => undefined,
    }),
  ).rejects.toThrow('backend_verification_failed');
  expect(await readdir(target)).toEqual([]);
  expect(existsSync(source)).toBe(true);
});

it('rolls back a startup index even when start rejects after writing it', async () => {
  const { root, source, target } = await fixture();
  await makeLongformCache(source, ['/previous/voices']);
  let starts = 0;
  await expect(
    relocateDataDirectory(source, target, {
      environmentPath: join(root, 'env'),
      stop: async () => undefined,
      start: async () => {
        if (++starts === 1) {
          await backendStartupIndex(target);
          throw new Error('startup failed');
        }
      },
      verify: async () => true,
      progress: () => undefined,
    }),
  ).rejects.toThrow('startup failed');
  expect(await readdir(target)).toEqual([]);
});

it('recognizes the backend target spelling through a directory alias', async () => {
  const { root, source, target } = await fixture();
  await makeLongformCache(source, ['/previous/voices']);
  const alias = join(root, 'mount-alias');
  await symlink(dirname(target), alias, process.platform === 'win32' ? 'junction' : 'dir');
  const nativeTarget = join(alias, 'data');
  const requested =
    process.platform === 'win32' ? nativeTarget.replaceAll('\\', '/') : nativeTarget;
  let starts = 0;
  await expect(
    relocateDataDirectory(source, requested, {
      environmentPath: join(root, 'env'),
      stop: async () => undefined,
      start: async () => {
        if (++starts === 1) await backendStartupIndex(requested);
      },
      verify: async () => false,
      progress: () => undefined,
    }),
  ).rejects.toThrow('backend_verification_failed');
  expect(await readdir(target)).toEqual([]);
});

it.each(['during start', 'during verification'])(
  'preserves an unrelated cache-index replacement %s',
  async (when) => {
    const { root, source, target } = await fixture();
    await makeLongformCache(source, ['/previous/voices']);
    const index = join(target, 'outputs', 'longform_cache', 'voices_roots.json');
    const replace = async () => {
      await writeFile(index + '.external', JSON.stringify(['/unrelated/user/history']));
      await rename(index + '.external', index);
    };
    let starts = 0;
    await expect(
      relocateDataDirectory(source, target, {
        environmentPath: join(root, 'env'),
        stop: async () => undefined,
        start: async () => {
          if (++starts === 1) {
            await backendStartupIndex(target);
            if (when === 'during start') await replace();
          }
        },
        verify: async () => {
          if (when === 'during verification') await replace();
          return false;
        },
        progress: () => undefined,
      }),
    ).rejects.toMatchObject(incomplete('backend_verification_failed'));
    expect(JSON.parse(await readFile(index, 'utf8'))).toEqual(['/unrelated/user/history']);
    expect(await readdir(target)).toEqual(['outputs']);
    expect(existsSync(source)).toBe(true);
  },
);

it('does not create an index when no cache was copied', async () => {
  const { root, source, target } = await fixture();
  let starts = 0;
  await expect(
    relocateDataDirectory(source, target, {
      environmentPath: join(root, 'env'),
      stop: async () => undefined,
      start: async () => {
        if (++starts === 1) await backendStartupIndex(target);
      },
      verify: async () => false,
      progress: () => undefined,
    }),
  ).rejects.toThrow('backend_verification_failed');
  expect(await readdir(target)).toEqual([]);
});

it('keeps the original failure and the live target if stopping the failed backend fails', async () => {
  const { root, source, target } = await fixture();
  await makeLongformCache(source, ['/previous/voices']);
  let stops = 0;
  let starts = 0;
  await expect(
    relocateDataDirectory(source, target, {
      environmentPath: join(root, 'env'),
      stop: async () => {
        if (++stops === 2) throw new Error('cannot stop');
      },
      start: async () => {
        starts += 1;
        await backendStartupIndex(target);
      },
      verify: async () => false,
      progress: () => undefined,
    }),
  ).rejects.toMatchObject(incomplete('backend_verification_failed'));
  expect(starts).toBe(1);
  expect(await readFile(join(target, 'omnivoice.db'), 'utf8')).toBe('database');
  expect(existsSync(source)).toBe(true);
});

it('preserves an unreadable startup replacement without masking the verification failure', async () => {
  const { root, source, target } = await fixture();
  await makeLongformCache(source, ['/previous/voices']);
  const index = join(target, 'outputs', 'longform_cache', 'voices_roots.json');
  let starts = 0;
  let failCapture = false;
  constraints.beforeOpen = async (path) => {
    if (failCapture && path === index) throw new Error('capture denied');
  };
  await expect(
    relocateDataDirectory(source, target, {
      environmentPath: join(root, 'env'),
      stop: async () => undefined,
      start: async () => {
        if (++starts === 1) await backendStartupIndex(target);
      },
      verify: async () => {
        failCapture = true;
        return false;
      },
      progress: () => undefined,
    }),
  ).rejects.toMatchObject(incomplete('backend_verification_failed'));
  expect(existsSync(index)).toBe(true);
  expect(existsSync(source)).toBe(true);
});

it('does not learn expected startup history from an unrelated pre-start replacement', async () => {
  const { root, source, target } = await fixture();
  await makeLongformCache(source, ['/previous/voices']);
  const index = join(target, 'outputs', 'longform_cache', 'voices_roots.json');
  let replaced = false;
  constraints.beforeReadFile = async (path) => {
    if (path === index && !replaced) {
      replaced = true;
      await writeFile(index + '.external', JSON.stringify(['/unrelated/pre-start/history']));
      await rename(index + '.external', index);
    }
  };
  let starts = 0;
  await expect(
    relocateDataDirectory(source, target, {
      environmentPath: join(root, 'env'),
      stop: async () => undefined,
      start: async () => {
        if (++starts === 1) await backendStartupIndex(target);
      },
      verify: async () => false,
      progress: () => undefined,
    }),
  ).rejects.toMatchObject(incomplete('backend_verification_failed'));
  expect(JSON.parse(await readFile(index, 'utf8'))).toContain('/unrelated/pre-start/history');
});

it.each(['before activation', 'during verification'])(
  'rejects a selected directory replacement %s and keeps the source',
  async (when) => {
    const { root, source, target } = await fixture();
    const environmentPath = join(root, 'env');
    await writeFile(environmentPath, `OMNIVOICE_DATA_DIR=${source}\n`);
    const moved = join(root, 'moved-copy');
    const replace = async () => {
      await rename(target, moved);
      await mkdir(target);
      await writeFile(join(target, 'personal.txt'), 'unrelated');
    };
    let replaceOnEnvironmentRead = false;
    constraints.beforeReadFile = async (path) => {
      if (replaceOnEnvironmentRead && path === environmentPath) {
        replaceOnEnvironmentRead = false;
        await replace();
      }
    };
    let activatedReplacement = false;
    const outcome = await relocateDataDirectory(source, target, {
      environmentPath,
      stop: async () => undefined,
      start: async () => {
        if (existsSync(moved) && (await readDataDirectorySetting(environmentPath)) === target)
          activatedReplacement = true;
      },
      verify: async () => {
        if (when === 'during verification') await replace();
        return true;
      },
      progress: (stage) => {
        if (when === 'before activation' && stage === 'switching') replaceOnEnvironmentRead = true;
      },
    }).then(
      (result) => ({ result }),
      (error: Error) => ({ error }),
    );
    expect(outcome).toMatchObject({
      error: incomplete('target_changed'),
    });
    expect(activatedReplacement).toBe(false);
    expect(await readFile(join(source, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
    expect(await readFile(join(target, 'personal.txt'), 'utf8')).toBe('unrelated');
    expect(await readFile(join(moved, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
  },
);

it('rejects a symlink substituted for the selected directory without traversing it', async () => {
  const { root, source, target } = await fixture();
  const moved = join(root, 'moved-copy');
  await expect(
    relocateDataDirectory(source, target, {
      environmentPath: join(root, 'env'),
      stop: async () => undefined,
      start: async () => undefined,
      verify: async () => {
        await rename(target, moved);
        await symlink(moved, target, process.platform === 'win32' ? 'junction' : 'dir');
        return true;
      },
      progress: () => undefined,
    }),
  ).rejects.toMatchObject(incomplete('target_changed'));
  expect(await readFile(join(source, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
  expect(await readFile(join(moved, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
  expect((await lstat(target)).isSymbolicLink()).toBe(true);
});

it.each([
  { mutation: 'added file', failCopy: false },
  { mutation: 'replaced record', failCopy: false },
  { mutation: 'replaced directory', failCopy: false },
  { mutation: 'added file', failCopy: true },
  { mutation: 'replaced record', failCopy: true },
  { mutation: 'replaced directory', failCopy: true },
])(
  'preserves $mutation in scratch cleanup (copy failure: $failCopy)',
  async ({ mutation, failCopy }) => {
    const { root, source, target } = await fixture();
    let personal = '';
    constraints.beforeOpen = async (path) => {
      if (path !== join(target, 'voices', 'sample.wav')) return;
      const name = (await readdir(target)).find((entry) =>
        entry.startsWith('.voicestudio-moving-'),
      )!;
      const scratch = join(target, name);
      if (mutation === 'replaced directory') {
        await rename(scratch, join(root, 'moved-scratch'));
        await mkdir(scratch);
      }
      personal = join(scratch, mutation === 'replaced record' ? 'manifest.json' : 'personal.txt');
      await writeFile(personal + '.external', 'unrelated');
      await rename(personal + '.external', personal);
      if (failCopy) throw new Error('copy interrupted');
    };
    if (failCopy)
      await expect(prepareDataRelocation(source, target)).rejects.toMatchObject(
        incomplete('copy interrupted'),
      );
    else await prepareDataRelocation(source, target);
    expect(personal).not.toBe('');
    expect(await readFile(personal, 'utf8')).toBe('unrelated');
    expect(existsSync(source)).toBe(true);
  },
);

it('rejects a requested directory alias retargeted after copying', async () => {
  const { root, source, target } = await fixture();
  const alias = join(root, 'target-alias');
  await symlink(dirname(target), alias, process.platform === 'win32' ? 'junction' : 'dir');
  const requested = join(alias, 'data');
  const foreignParent = join(root, 'foreign');
  await mkdir(join(foreignParent, 'data'), { recursive: true });
  await writeFile(join(foreignParent, 'data', 'personal.txt'), 'unrelated');
  await expect(
    relocateDataDirectory(source, requested, {
      environmentPath: join(root, 'env'),
      stop: async () => undefined,
      start: async () => undefined,
      verify: async () => {
        await rename(alias, join(root, 'previous-alias'));
        await symlink(foreignParent, alias, process.platform === 'win32' ? 'junction' : 'dir');
        return true;
      },
      progress: () => undefined,
    }),
  ).rejects.toMatchObject(incomplete('target_changed'));
  expect(await readFile(join(source, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
  expect(await readFile(join(target, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
  expect(await readFile(join(foreignParent, 'data', 'personal.txt'), 'utf8')).toBe('unrelated');
});

it.each([
  { parts: ['omnivoice.db'], replacement: 'intruder', birth: 0n, change: null },
  { parts: ['voices', 'sample.wav'], replacement: 'other', birth: 0n, change: null },
  { parts: ['omnivoice.db'], replacement: 'intruder', birth: 100n, change: 200n },
  { parts: ['voices', 'sample.wav'], replacement: 'other', birth: 100n, change: 200n },
])(
  'preserves changed content when a replacement reuses all ambiguous metadata at $parts',
  async ({ parts, replacement, birth, change }) => {
    constraints.collisionBirthtime = birth;
    constraints.collisionCtime = change;
    const { source, target } = await fixture();
    const path = join(target, ...parts);
    constraints.reusedIdentityPath = path;
    const prepared = await prepareDataRelocation(source, target);
    constraints.reusedStats = Object.assign(Object.create(await lstat(path, { bigint: true })), {
      birthtimeNs: birth,
      ctimeNs: change ?? (await lstat(path, { bigint: true })).ctimeNs,
    });
    constraints.reuseEnabled = true;
    const temporary = path + '.external';
    await writeFile(temporary, replacement);
    await rename(temporary, path);
    await prepared.rollback();
    expect(await readFile(path, 'utf8')).toBe(replacement);
    expect(existsSync(source)).toBe(true);
  },
);

it('preserves a replacement directory when its inode is reused with a new generation', async () => {
  const { root, source, target } = await fixture();
  const path = join(target, 'voices');
  constraints.reusedIdentityPath = path;
  constraints.reuseAllMetadata = false;
  const prepared = await prepareDataRelocation(source, target);
  constraints.reusedStats = await lstat(path, { bigint: true });
  await rename(path, join(root, 'previous-voices'));
  await mkdir(path, { mode: 0o700 });
  const replacement = await stat(path, { bigint: true });
  constraints.reuseEnabled = true;
  await prepared.rollback();
  // Windows reports every directory as 0o666, so compare the real inode, which
  // `stat` (unlike the reuse-faking `lstat` above) reports on every OS.
  expect((await stat(path, { bigint: true })).ino).toBe(replacement.ino);
});

it('preserves a scratch record replacement when all ambiguous metadata is reused', async () => {
  const { source, target } = await fixture();
  let record = '';
  constraints.beforeOpen = async (path) => {
    if (path.endsWith('manifest.json') && path.includes('.voicestudio-moving-')) {
      record = path;
      constraints.reusedIdentityPath = path;
    }
    if (path === join(target, 'voices', 'sample.wav')) {
      constraints.reusedStats = Object.assign(
        Object.create(await lstat(record, { bigint: true })),
        { birthtimeNs: 0n },
      );
      constraints.reuseEnabled = true;
      await writeFile(record + '.external', 'x'.repeat(Number(constraints.reusedStats!.size)));
      await rename(record + '.external', record);
    }
  };
  await prepareDataRelocation(source, target);
  expect(await readFile(record, 'utf8')).toMatch(/^x+$/);
});

async function backendLogStartup(target: string, rotate: boolean) {
  const log = join(target, 'omnivoice.log');
  if (rotate) {
    await rm(log + '.3', { force: true });
    for (let index = 2; index >= 1; index -= 1)
      await rename(log + '.' + index, log + '.' + (index + 1));
    await rename(log, log + '.1');
    await writeFile(log, 'backend startup\n');
  } else await appendFile(log, 'backend startup\n');
}

it.each([false, true])(
  'reports incomplete cleanup while preserving uncertain backend logs (rotation: %s)',
  async (rotate) => {
    const { root, source, target } = await fixture();
    const original = rotate ? 'x'.repeat(2 * 1024 * 1024 - 1) + '\n' : 'startup history\n';
    await writeFile(join(source, 'omnivoice.log'), original);
    for (let index = 1; index <= 3; index += 1)
      await writeFile(join(source, 'omnivoice.log.' + index), 'previous ' + index);
    let starts = 0;
    await expect(
      relocateDataDirectory(source, target, {
        environmentPath: join(root, 'env'),
        stop: async () => undefined,
        start: async () => {
          if (++starts === 1) await backendLogStartup(target, rotate);
        },
        verify: async () => false,
        progress: () => undefined,
      }),
    ).rejects.toMatchObject(incomplete('backend_verification_failed'));
    expect(await readFile(join(source, 'omnivoice.log'), 'utf8')).toBe(original);
    expect(await readFile(join(target, 'omnivoice.log'), 'utf8')).toContain('backend startup');
    if (rotate) expect(await readFile(join(target, 'omnivoice.log.1'), 'utf8')).toBe(original);
    expect(existsSync(join(target, 'omnivoice.db'))).toBe(false);
  },
);

it('reports incomplete rollback if restarting the original backend fails', async () => {
  const { root, source, target } = await fixture();
  let starts = 0;
  await expect(
    relocateDataDirectory(source, target, {
      environmentPath: join(root, 'env'),
      stop: async () => undefined,
      start: async () => {
        if (++starts === 2) throw new Error('original restart failed');
      },
      verify: async () => false,
      progress: () => undefined,
    }),
  ).rejects.toMatchObject(incomplete('backend_verification_failed'));
  expect(await readdir(target)).toEqual([]);
  expect(existsSync(source)).toBe(true);
});

it('rejects a zero-birthtime selected target as unverifiable before writing', async () => {
  const { source, target } = await fixture();
  constraints.reusedStats = await lstat(target, { bigint: true });
  constraints.reusedIdentityPath = target;
  constraints.reuseEnabled = true;
  await expect(prepareDataRelocation(source, target)).rejects.toThrow('identity_unavailable');
  expect(await readdir(target)).toEqual([]);
  expect(existsSync(source)).toBe(true);
});

it.each([true, false])(
  'keeps both copies when restoring the environment fails (existing target: %s)',
  async (existing) => {
    const { root, source, target } = await fixture();
    if (!existing) await rm(target, { recursive: true });
    const environmentPath = join(root, 'env');
    await writeFile(environmentPath, `OMNIVOICE_DATA_DIR=${source}\n`);
    let starts = 0;
    await expect(
      relocateDataDirectory(source, target, {
        environmentPath,
        stop: async () => undefined,
        start: async () => {
          starts++;
        },
        verify: async () => {
          constraints.beforeReadFile = async (path) => {
            if (path === environmentPath)
              throw Object.assign(new Error('environment unavailable'), { code: 'EIO' });
          };
          return false;
        },
        progress: () => undefined,
      }),
    ).rejects.toMatchObject(incomplete('backend_verification_failed'));
    constraints.beforeReadFile = null;
    expect(await readDataDirectorySetting(environmentPath)).toBe(target);
    expect(await readFile(join(source, 'omnivoice.db'), 'utf8')).toBe('database');
    expect(await readFile(join(target, 'omnivoice.db'), 'utf8')).toBe('database');
    expect(starts).toBe(1);
  },
);

it('rejects an unverifiable new destination before copying and cleans empty staging', async () => {
  const { source, target } = await fixture();
  await rm(target, { recursive: true });
  constraints.zeroStagingIdentity = true;
  const failure = await prepareDataRelocation(source, target).catch((error) => error);
  expect(constraints.copyCalls).toBe(0);
  expect(failure).toMatchObject({ message: 'identity_unavailable' });
  expect(existsSync(target)).toBe(false);
  expect(await readdir(dirname(target))).toEqual([]);
  expect(await readFile(join(source, 'omnivoice.db'), 'utf8')).toBe('database');
});

it.each([true, false])(
  'rejects mutable creation identity before copying (existing target: %s)',
  async (existing) => {
    const { source, target } = await fixture();
    if (!existing) await rm(target, { recursive: true });
    constraints.mutableBirthtime = true;
    const failure = await prepareDataRelocation(source, target).catch((error) => error);
    expect(constraints.copyCalls).toBe(0);
    expect(failure).toMatchObject({ message: 'identity_unavailable' });
    expect(existsSync(target)).toBe(existing);
    expect(await readdir(existing ? target : dirname(target))).toEqual([]);
    expect(await readFile(join(source, 'omnivoice.db'), 'utf8')).toBe('database');
  },
);

it.each([true, false])(
  'accepts stable creation time initially equal to change time (existing target: %s)',
  async (existing) => {
    const { source, target } = await fixture();
    if (!existing) await rm(target, { recursive: true });
    constraints.stableBirthtime = true;
    const prepared = await prepareDataRelocation(source, target);
    expect(await readFile(join(target, 'omnivoice.db'), 'utf8')).toBe('database');
    expect(await prepared.rollback()).toBe(true);
    expect(await readdir(existing ? target : dirname(target))).toEqual([]);
    expect(await readFile(join(source, 'omnivoice.db'), 'utf8')).toBe('database');
  },
);

it.each([true, false])(
  'probes again when a coarse clock hides the first identity probe (existing target: %s)',
  async (existing) => {
    const { source, target } = await fixture();
    if (!existing) await rm(target, { recursive: true });
    constraints.stableBirthtime = true;
    constraints.staleProbes = 1;
    const prepared = await prepareDataRelocation(source, target);
    expect(constraints.staleProbes).toBe(0);
    expect(await readFile(join(target, 'omnivoice.db'), 'utf8')).toBe('database');
    expect(await prepared.rollback()).toBe(true);
    expect(await readFile(join(source, 'omnivoice.db'), 'utf8')).toBe('database');
  },
);

it.each([true, false])(
  'rejects creation identity a probe never sees change (existing target: %s)',
  async (existing) => {
    const { source, target } = await fixture();
    if (!existing) await rm(target, { recursive: true });
    constraints.stableBirthtime = true;
    constraints.staleProbes = Number.POSITIVE_INFINITY;
    const failure = await prepareDataRelocation(source, target).catch((error) => error);
    expect(constraints.copyCalls).toBe(0);
    expect(failure).toMatchObject({ message: 'identity_unavailable' });
    expect(await readdir(existing ? target : dirname(target))).toEqual([]);
    expect(await readFile(join(source, 'omnivoice.db'), 'utf8')).toBe('database');
  },
);

it.each(['probe', 'copy'])(
  'preserves a replacement staging directory after %s failure',
  async (phase) => {
    const { root, source, target } = await fixture();
    await rm(target, { recursive: true });
    let foreign = '';
    const replace = async (staging: string) => {
      await rename(staging, join(root, 'original-staging'));
      await mkdir(staging);
      foreign = join(staging, 'foreign.txt');
      await writeFile(foreign, 'unrelated user data');
    };
    if (phase === 'probe') {
      constraints.stableBirthtime = true;
      constraints.beforeOpen = async (path) => {
        if (path.includes('.voicestudio-identity-')) await replace(dirname(path));
      };
    } else {
      constraints.afterCopy = async (path) => {
        await replace(dirname(path));
        throw new Error('copy interrupted');
      };
    }
    await expect(prepareDataRelocation(source, target)).rejects.toMatchObject({
      message: 'rollback_incomplete',
    });
    expect(foreign).not.toBe('');
    expect(await readFile(foreign, 'utf8')).toBe('unrelated user data');
    expect(await readFile(join(source, 'omnivoice.db'), 'utf8')).toBe('database');
    expect(existsSync(target)).toBe(false);
  },
);
