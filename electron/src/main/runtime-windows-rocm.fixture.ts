import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { afterEach, beforeEach, vi } from 'vitest';
import { runtimePython } from './runtime-project';
vi.mock('node:child_process', async (importOriginal) => ({
  ...(await importOriginal<typeof import('node:child_process')>()),
  execFile: vi.fn(),
}));
vi.mock('node:fs/promises', async (importOriginal) => ({
  ...(await importOriginal<typeof import('node:fs/promises')>()),
  // 25 Mi blocks of 4 KiB: roughly 100 GiB free, so disk checks never block.
  statfs: vi.fn(async () => ({ bavail: 25 * 1024 * 1024, bsize: 4096 })),
}));
const roots: string[] = [];
const cleanups: (() => Promise<void>)[] = [];
export function beforeFixtureRemoval(cleanup: () => Promise<void>) {
  cleanups.push(cleanup);
}
export const recipe = '{"python":{"version":"3.12","abi":"cp312","platform":"win-amd64"}}';
export const report = JSON.stringify({
  ready: true,
  scope: 'model-free',
  abi: 'cp312',
  architecture: 'win-amd64',
  recipe_sha256: createHash('sha256').update(recipe).digest('hex'),
});
beforeEach(() => {
  vi.spyOn(process, 'platform', 'get').mockReturnValue('win32');
  vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'rocm');
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true })),
  );
  vi.mocked(execFile).mockImplementation(((
    _c: unknown,
    _a: unknown,
    _o: unknown,
    callback: (error: Error | null, out: string, err: string) => void,
  ) => callback(null, report, '')) as never);
});
afterEach(async () => {
  // Supervisors must finish queued setup-log writes before their directories disappear.
  // A failed shutdown is still reported, after the rest of the teardown has run.
  const failures: unknown[] = [];
  for (const cleanup of cleanups.splice(0).reverse()) {
    try {
      await cleanup();
    } catch (error) {
      failures.push(error);
    }
  }
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
  if (failures.length) throw new AggregateError(failures, 'Fixture cleanup failed');
});
export async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'vs-win-rocm-'));
  roots.push(root);
  const bundle = join(root, 'bundle'),
    project = join(root, 'runtime');
  for (const name of ['backend', 'config', 'omnivoice', 'scripts'])
    await mkdir(join(bundle, name), { recursive: true });
  for (const name of ['pyproject.toml', 'README.md', 'LICENSE'])
    await writeFile(join(bundle, name), name);
  await writeFile(join(bundle, 'uv.lock'), 'name = "nvidia-cudnn-cu12"\nname = "nvidia-ml-py"\n');
  await writeFile(join(bundle, 'scripts/windows-rocm-recipe.json'), recipe);
  await writeFile(join(bundle, 'scripts/smoke_windows_rocm.py'), '# local smoke');
  return { bundle, project };
}
export async function interpreter(project: string) {
  await mkdir(dirname(runtimePython(project)), { recursive: true });
  await writeFile(runtimePython(project), 'python');
  await writeFile(join(project, '.venv/pyvenv.cfg'), 'home = managed');
}
export function runner(project: string, wrongAbi = false) {
  return vi.fn(async (_command: string, args: string[], _cwd: string, _env?: NodeJS.ProcessEnv) => {
    if (args[0] === 'sync') await interpreter(project);
    if (wrongAbi && args[0] === '-c' && args[1]?.includes('validate_target()'))
      throw Error('wrong ABI');
    return report;
  });
}
