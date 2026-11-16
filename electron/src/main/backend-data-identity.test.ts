// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { EventEmitter } from 'node:events';
import { mkdtemp, mkdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as filesystem from 'node:fs/promises';

const fixture = vi.hoisted(() => ({
  root: '',
  packaged: true,
  spawn: vi.fn(),
  spawnSync: vi.fn(),
}));
vi.mock('node:child_process', () => ({ spawn: fixture.spawn, spawnSync: fixture.spawnSync }));
vi.mock('node:fs/promises', async (original) => ({
  ...(await original<typeof import('node:fs/promises')>()),
}));
vi.mock('electron', () => ({
  app: {
    get isPackaged() {
      return fixture.packaged;
    },
    getPath: () => fixture.root,
    getAppPath: () => fixture.root,
  },
}));
import { BackendSupervisor } from './backend';

type Launch = { env: NodeJS.ProcessEnv; cwd: string; startedAt: number };
type Internal = {
  generation: number;
  managed: boolean;
  child: object | null;
  launchLog: Launch | null;
  prepareDataIdentity?: (launch: Launch, generation: number) => Promise<void>;
  remoteCredentials: () => Promise<string>;
  markReady: (generation: number, patch: object, supervise: boolean) => Promise<boolean>;
};
beforeEach(async () => {
  fixture.packaged = true;
  fixture.root = await mkdtemp(join(tmpdir(), 'voice-data-identity-'));
  await mkdir(join(fixture.root, 'data'));
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
});
afterEach(async () => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  await rm(fixture.root, { recursive: true, force: true });
});
async function launch(
  owned = true,
  configure?: (launch: Launch) => void,
): Promise<{
  supervisor: BackendSupervisor;
  internal: Internal;
  proof: Promise<void> | undefined;
}> {
  const supervisor = new BackendSupervisor();
  const internal = supervisor as unknown as Internal;
  internal.generation = 1;
  internal.managed = owned;
  internal.child = owned ? Object.assign(new EventEmitter(), { pid: 123, exitCode: null }) : null;
  internal.launchLog = {
    env: {
      OMNIVOICE_DATA_DIR: join(fixture.root, 'data'),
      OMNIVOICE_ENV_FILE: join(fixture.root, 'absent-env'),
      OMNIVOICE_BACKEND_CMD: process.env.OMNIVOICE_BACKEND_CMD,
    },
    cwd: fixture.root,
    startedAt: 1,
  };
  configure?.(internal.launchLog);
  vi.spyOn(internal, 'remoteCredentials').mockResolvedValue('valid');
  const proof = internal.prepareDataIdentity?.(internal.launchLog, 1);
  return { supervisor, internal, proof };
}
function identity(supervisor: BackendSupervisor): string | undefined {
  return (supervisor.status as typeof supervisor.status & { dataIdentity?: string }).dataIdentity;
}
it('publishes a stable opaque UUID only for the proven owned ready root', async () => {
  const first = await launch();
  await first.proof;
  await first.internal.markReady(1, {}, false);
  expect(identity(first.supervisor)).toMatch(/^[0-9a-f-]{36}$/);
  expect(identity(first.supervisor)).not.toContain(fixture.root);
  const next = await launch();
  await next.proof;
  await next.internal.markReady(1, {}, false);
  expect(identity(next.supervisor)).toBe(identity(first.supervisor));
});
it.each([
  'HF_ENDPOINT=https://hf-mirror.com\n',
  'OMNIVOICE_CACHE_DIR=/synthetic-model-cache\n',
  'OMNIVOICE_TORCH_VARIANT=cpu\n',
])('keeps installed data identity when saved settings do not change its root: %s', async (text) => {
  const first = await launch();
  await first.proof;
  await first.internal.markReady(1, {}, false);
  const original = identity(first.supervisor);
  expect(original).toMatch(/^[0-9a-f-]{36}$/);
  await writeFile(join(fixture.root, 'absent-env'), text);
  const next = await launch();
  await next.proof;
  await next.internal.markReady(1, {}, false);
  expect(identity(next.supervisor)).toBe(original);
});
it('allows an unrelated project dotenv without lending a different root authority', async () => {
  await writeFile(join(fixture.root, '.env'), 'LOG_LEVEL=info\n');
  const result = await launch();
  await result.proof;
  await result.internal.markReady(1, {}, false);
  expect(identity(result.supervisor)).toMatch(/^[0-9a-f-]{36}$/);
});
it('does not substitute the parent home when launch dotenv can redirect the default saved env', async () => {
  vi.stubEnv('HOME', fixture.root);
  const otherHome = join(fixture.root, 'other-home');
  await mkdir(join(otherHome, '.config', 'omnivoice'), { recursive: true });
  await writeFile(
    join(otherHome, '.config', 'omnivoice', 'env'),
    'OMNIVOICE_DATA_DIR=/different\n',
  );
  await writeFile(join(fixture.root, '.env'), `HOME=${otherHome}\n`);
  const result = await launch(true, (value) => {
    delete value.env.OMNIVOICE_ENV_FILE;
  });
  await result.proof;
  await result.internal.markReady(1, {}, false);
  expect(identity(result.supervisor)).toBeUndefined();
});
it('recognizes ordinary default-location saved settings under the actual launch home', async () => {
  const directory = join(fixture.root, '.config', 'omnivoice');
  await mkdir(directory, { recursive: true });
  await writeFile(join(directory, 'env'), 'HF_ENDPOINT=https://hf-mirror.com\n');
  const result = await launch(true, (value) => {
    delete value.env.OMNIVOICE_ENV_FILE;
    value.env.HOME = fixture.root;
    value.env.USERPROFILE = fixture.root;
  });
  await result.proof;
  await result.internal.markReady(1, {}, false);
  expect(identity(result.supervisor)).toMatch(/^[0-9a-f-]{36}$/);
});
async function defaultLaunch(settings?: string) {
  const home = join(fixture.root, 'launch-home');
  const appData = join(fixture.root, 'app-data');
  const data =
    process.platform === 'darwin'
      ? join(home, 'Library', 'Application Support', 'OmniVoice')
      : process.platform === 'win32'
        ? join(appData, 'OmniVoice')
        : join(home, '.omnivoice');
  await mkdir(data, { recursive: true });
  if (settings !== undefined) {
    await mkdir(join(home, '.config', 'omnivoice'), { recursive: true });
    await writeFile(join(home, '.config', 'omnivoice', 'env'), settings);
  }
  const result = await launch(true, (value) => {
    delete value.env.OMNIVOICE_DATA_DIR;
    delete value.env.OMNIVOICE_ENV_FILE;
    value.env.HOME = home;
    value.env.USERPROFILE = home;
    value.env.APPDATA = appData;
  });
  return { ...result, data };
}
it.each([
  undefined,
  'HF_ENDPOINT=https://hf-mirror.com\nHF_HOME=/synthetic-cache\n',
  'OMNIVOICE_CACHE_DIR=/home/fixture/models\n',
])(
  'preserves the actual default owned data root across relaunch with settings=%s',
  async (settings) => {
    const first = await defaultLaunch(settings);
    await first.proof;
    await first.internal.markReady(1, {}, false);
    expect(identity(first.supervisor)).toMatch(/^[0-9a-f-]{36}$/);
    const metadata = JSON.parse(
      await filesystem.readFile(join(first.data, '.voicestudio-data-identity.json'), 'utf8'),
    );
    expect(metadata.id).toBe(identity(first.supervisor));
    const next = await defaultLaunch(settings);
    await next.proof;
    await next.internal.markReady(1, {}, false);
    expect(identity(next.supervisor)).toBe(identity(first.supervisor));
  },
);
it.each(['HOME', 'APPDATA', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH'])(
  'does not derive a default root from settings overriding %s',
  async (key) => {
    const result = await defaultLaunch(`${key}=/different\n`);
    await result.proof;
    await result.internal.markReady(1, {}, false);
    expect(identity(result.supervisor)).toBeUndefined();
  },
);
it.each([
  'export HOME=/different\n',
  "'APPDATA'=/different\n",
  'OTHER="first\nHOME=/different\nlast"\n',
])('keeps selector assignment syntax or multiline ambiguity unknown: %s', async (settings) => {
  const result = await defaultLaunch(settings);
  await result.proof;
  await result.internal.markReady(1, {}, false);
  expect(identity(result.supervisor)).toBeUndefined();
});
it.each([
  "export OMNIVOICE_DATA_DIR='/different'\n",
  "'OMNIVOICE_DATA_DIR'=/different\n",
  'omnivoice_data_dir=/different\n',
  'OMNIVOICE_ENV_FILE=/different.env\n',
  'HF_ENDPOINT="first\nOMNIVOICE_DATA_DIR=/ambiguous\nlast"\n',
])('keeps redirected or ambiguous saved settings unknown: %s', async (text) => {
  await writeFile(join(fixture.root, 'absent-env'), text);
  const result = await launch();
  await result.proof;
  await result.internal.markReady(1, {}, false);
  expect(identity(result.supervisor)).toBeUndefined();
});
it.each(['oversized', 'invalid UTF-8', 'UTF-16', 'directory', 'symbolic link'])(
  'leaves unreadable or unsupported saved-env sources unknown: %s',
  async (kind) => {
    const path = join(fixture.root, 'absent-env');
    if (kind === 'oversized') await writeFile(path, 'x'.repeat(64 * 1024 + 1));
    else if (kind === 'invalid UTF-8') await writeFile(path, Buffer.from([0xff, 0xfe, 0xff]));
    else if (kind === 'UTF-16') await writeFile(path, 'OMNIVOICE_DATA_DIR=/different', 'utf16le');
    else if (kind === 'directory') await mkdir(path);
    else if (process.platform === 'win32') {
      const directory = join(fixture.root, 'linked-env-directory');
      await mkdir(directory);
      await symlink(directory, path, 'junction');
    } else await symlink(join(fixture.root, 'missing-target'), path);
    const result = await launch();
    await result.proof;
    await result.internal.markReady(1, {}, false);
    expect(identity(result.supervisor)).toBeUndefined();
  },
);
it.each(['attached', 'source', 'custom', 'saved-env', 'dotenv'])(
  'leaves %s data ownership unknown',
  async (kind) => {
    if (kind === 'source') fixture.packaged = false;
    if (kind === 'custom') vi.stubEnv('OMNIVOICE_BACKEND_CMD', '["custom-server"]');
    if (kind === 'saved-env')
      await writeFile(join(fixture.root, 'absent-env'), 'OMNIVOICE_DATA_DIR=/different\n');
    if (kind === 'dotenv')
      await writeFile(join(fixture.root, '.env'), 'OMNIVOICE_ENV_FILE=/different\n');
    const result = await launch(kind !== 'attached');
    await result.proof;
    await result.internal.markReady(1, {}, false);
    expect(identity(result.supervisor)).toBeUndefined();
  },
);

it('retains the same-child identity while unresponsive without getter I/O', async () => {
  const result = await launch();
  await result.proof;
  await result.internal.markReady(1, {}, false);
  const id = identity(result.supervisor);
  expect(id).toMatch(/^[0-9a-f-]{36}$/);
  await rm(join(fixture.root, 'data'), { recursive: true });
  (result.internal as Internal & { stage: string }).stage = 'unresponsive';
  expect(identity(result.supervisor)).toBe(id);
  result.internal.child = Object.assign(new EventEmitter(), { pid: 124 });
  expect(identity(result.supervisor)).toBeUndefined();
});
it('keeps different effective roots separate across port changes', async () => {
  const first = await launch();
  await first.proof;
  await first.internal.markReady(1, {}, false);
  await mkdir(join(fixture.root, 'other'));
  const other = await launch(true, (value) => {
    value.env.OMNIVOICE_DATA_DIR = join(fixture.root, 'other');
  });
  await other.proof;
  await other.internal.markReady(1, {}, false);
  expect(identity(other.supervisor)).toMatch(/^[0-9a-f-]{36}$/);
  expect(identity(other.supervisor)).not.toBe(identity(first.supervisor));
  (first.internal as Internal & { localPort: number }).localPort = 9999;
  expect(identity(first.supervisor)).not.toBe(identity(other.supervisor));
});
it.each([
  'missing explicit root',
  'whitespace root',
  'relative saved env',
  'ancestor dotenv',
  'env I/O error',
])('does not guess effective data ownership after %s', async (kind) => {
  const project = join(fixture.root, 'project');
  await mkdir(project);
  if (kind === 'relative saved env')
    await writeFile(join(project, 'settings.env'), 'OMNIVOICE_DATA_DIR=/different\n');
  if (kind === 'ancestor dotenv')
    await writeFile(join(fixture.root, '.env'), 'OMNIVOICE_ENV_FILE=/different\n');
  const originalOpen = filesystem.open;
  if (kind === 'env I/O error')
    vi.spyOn(filesystem, 'open').mockImplementation((async (
      ...args: Parameters<typeof originalOpen>
    ) => {
      if (args[0] === join(fixture.root, 'absent-env'))
        throw Object.assign(new Error('fixture'), { code: 'EACCES' });
      return originalOpen(...args);
    }) as typeof originalOpen);
  const result = await launch(true, (value) => {
    value.cwd = project;
    if (kind === 'missing explicit root') delete value.env.OMNIVOICE_DATA_DIR;
    if (kind === 'whitespace root') value.env.OMNIVOICE_DATA_DIR = ' ' + join(fixture.root, 'data');
    if (kind === 'relative saved env') value.env.OMNIVOICE_ENV_FILE = 'settings.env';
  });
  await result.proof;
  await result.internal.markReady(1, {}, false);
  expect(identity(result.supervisor)).toBeUndefined();
});
it('never waits for optional metadata or upgrades a ready generation later', async () => {
  let release!: () => void;
  let entered!: () => void;
  const started = new Promise<void>((resolve) => {
    entered = resolve;
  });
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const original = filesystem.open;
  vi.spyOn(filesystem, 'open').mockImplementation((async (...args: Parameters<typeof original>) => {
    if (args[0] === join(fixture.root, 'absent-env')) {
      entered();
      await held;
    }
    return original(...args);
  }) as typeof original);
  const result = await launch();
  await started;
  const ready = result.internal.markReady(1, {}, false);
  let finished = false;
  void ready.then(() => {
    finished = true;
  });
  try {
    await Promise.resolve();
    await Promise.resolve();
    expect(finished).toBe(true);
    expect(result.supervisor.status.stage).toBe('ready');
    expect(identity(result.supervisor)).toBeUndefined();
  } finally {
    release();
    await result.proof;
    await ready;
  }
  await result.internal.markReady(1, {}, false);
  expect(identity(result.supervisor)).toBeUndefined();
});
it('discards optional proof completed after launch generation replacement', async () => {
  const result = await launch();
  result.internal.generation++;
  await result.proof;
  expect(await result.internal.markReady(1, {}, false)).toBe(false);
  expect(identity(result.supervisor)).toBeUndefined();
});

it('starts optional proof before the actual owned spawn and publishes only its ready child', async () => {
  vi.stubEnv('OMNIVOICE_DATA_DIR', join(fixture.root, 'data'));
  vi.stubEnv('OMNIVOICE_ENV_FILE', join(fixture.root, 'absent-env'));
  vi.stubEnv('OMNIVOICE_BUNDLED_UV', join(fixture.root, 'uv'));
  const supervisor = new BackendSupervisor();
  const internal = supervisor as unknown as Internal & {
    spawnChild: (plan: { argv: string[]; cwd: string }, generation: number) => void;
  };
  internal.generation = 1;
  vi.spyOn(internal, 'remoteCredentials').mockResolvedValue('valid');
  let proof: Promise<void> | undefined;
  const original = internal.prepareDataIdentity!.bind(internal);
  vi.spyOn(internal, 'prepareDataIdentity').mockImplementation((value, generation) => {
    proof = original(value, generation);
    return proof;
  });
  const child = Object.assign(new EventEmitter(), {
    pid: 123,
    stdin: null,
    stdout: null,
    stderr: null,
    exitCode: null,
  });
  fixture.spawn.mockImplementationOnce(() => {
    expect(proof).toBeDefined();
    return child;
  });
  internal.spawnChild({ argv: ['fixture-python'], cwd: fixture.root }, 1);
  expect(supervisor.status.stage).toBe('starting');
  await proof;
  await internal.markReady(1, {}, false);
  expect(identity(supervisor)).toMatch(/^[0-9a-f-]{36}$/);
  expect(internal.child).toBe(child);
});
