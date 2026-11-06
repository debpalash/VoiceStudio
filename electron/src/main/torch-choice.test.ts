// @vitest-environment node
import { mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { applySavedTorchVariant, rememberTorchVariant, userEnvPath } from './torch-choice';

let dir: string;
let file: string;
beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), 'torch-choice-'));
  file = join(dir, 'nested', 'env');
});
afterEach(() => rm(dir, { recursive: true, force: true }));

const line = 'OMNIVOICE_TORCH_VARIANT';

describe('userEnvPath', () => {
  it('is the file the backend loads, unless OMNIVOICE_ENV_FILE moves it', () => {
    expect(userEnvPath({}, '/home/me')).toBe(join('/home/me', '.config', 'omnivoice', 'env'));
    expect(userEnvPath({ OMNIVOICE_ENV_FILE: '/x/env' }, '/home/me')).toBe('/x/env');
  });
});

describe('applySavedTorchVariant (#3804)', () => {
  it.each([
    [`${line}=rocm\n`, 'rocm'],
    [`HF_TOKEN=abc\nexport ${line}="rocm"\n`, 'rocm'],
    [`${line}='cpu' # chosen by hand\n`, 'cpu'],
    [`${line}=cpu\n${line}=rocm\n`, 'rocm'],
  ])('a launch without the variable uses the saved choice: %j', async (text, expected) => {
    const path = join(dir, 'env');
    await writeFile(path, text);
    const env: NodeJS.ProcessEnv = {};
    applySavedTorchVariant(env, path);
    expect(env[line]).toBe(expected);
  });

  it('never replaces a value set for this launch', async () => {
    const path = join(dir, 'env');
    await writeFile(path, `${line}=rocm\n`);
    const env: NodeJS.ProcessEnv = { [line]: 'cpu' };
    applySavedTorchVariant(env, path);
    expect(env[line]).toBe('cpu');
  });

  it('leaves the variable unset without a file or a saved value', async () => {
    const env: NodeJS.ProcessEnv = {};
    applySavedTorchVariant(env, join(dir, 'missing'));
    expect(env[line]).toBeUndefined();
    const path = join(dir, 'env');
    await writeFile(path, 'HF_TOKEN=abc\n');
    applySavedTorchVariant(env, path);
    expect(env[line]).toBeUndefined();
  });
});

describe('rememberTorchVariant (#3804)', () => {
  it('saves an explicit ROCm choice for the next launches, readable back', async () => {
    rememberTorchVariant({ [line]: ' ROCm ' }, 'linux', file);
    const env: NodeJS.ProcessEnv = {};
    applySavedTorchVariant(env, file);
    expect(env[line]).toBe('rocm');
  });

  it('keeps every other line, and the file stays private', async () => {
    const path = join(dir, 'env');
    await writeFile(path, `HF_TOKEN=abc\n# note\nOMNIVOICE_CACHE_DIR='/a b'\n`);
    rememberTorchVariant({ [line]: 'rocm' }, 'linux', path);
    expect(await readFile(path, 'utf8')).toBe(
      `HF_TOKEN=abc\n# note\nOMNIVOICE_CACHE_DIR='/a b'\n${line}=rocm\n`,
    );
    if (process.platform !== 'win32') expect((await stat(path)).mode & 0o777).toBe(0o600);
  });

  it('does not rewrite the file when the choice is already saved', async () => {
    const path = join(dir, 'env');
    await writeFile(path, `export ${line}="rocm"\n`);
    rememberTorchVariant({ [line]: 'rocm' }, 'linux', path);
    expect(await readFile(path, 'utf8')).toBe(`export ${line}="rocm"\n`);
  });

  it.each(['cpu', 'cuda', 'auto'])('switching to %s forgets a saved ROCm choice', async (value) => {
    const path = join(dir, 'env');
    await writeFile(path, `HF_TOKEN=abc\n${line}=rocm\n`);
    rememberTorchVariant({ [line]: value }, 'linux', path);
    expect(await readFile(path, 'utf8')).toBe('HF_TOKEN=abc\n');
  });

  it('keeps a hand-written non-ROCm line and does nothing without a choice', async () => {
    const path = join(dir, 'env');
    await writeFile(path, `${line}=cpu\n`);
    rememberTorchVariant({ [line]: 'cpu' }, 'linux', path);
    rememberTorchVariant({}, 'linux', path);
    expect(await readFile(path, 'utf8')).toBe(`${line}=cpu\n`);
  });

  it('saves nothing on macOS, where ROCm does not apply', async () => {
    rememberTorchVariant({ [line]: 'rocm' }, 'darwin', file);
    const env: NodeJS.ProcessEnv = {};
    applySavedTorchVariant(env, file);
    expect(env[line]).toBeUndefined();
  });

  it('never throws: a read-only location must not stop the engine starting', () => {
    expect(() =>
      rememberTorchVariant({ [line]: 'rocm' }, 'linux', join(dir, 'env', 'under-a-file', 'x')),
    ).not.toThrow();
    expect(() => rememberTorchVariant({ [line]: 'rocm' }, 'linux', '\0bad')).not.toThrow();
  });
});

describe('wiring (#3804)', () => {
  const source = (name: string) => readFile(join(import.meta.dirname, name), 'utf8');

  it('loads the saved choice before the supervisor can resolve the variant', async () => {
    const index = await source('index.ts');
    expect(index.indexOf('applySavedTorchVariant()')).toBeGreaterThan(-1);
    expect(index.indexOf('applySavedTorchVariant()')).toBeLessThan(
      index.indexOf('new BackendSupervisor()'),
    );
  });

  it('saves the choice only on the path that found a ready runtime', async () => {
    expect(await source('backend.ts')).toMatch(
      /await runtimeProjectReady\(bundle, project\)\) \{\s+this\.runtimeProject = project;\s+rememberTorchVariant\(\);/,
    );
  });
});
