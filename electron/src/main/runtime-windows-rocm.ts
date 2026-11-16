import { AsyncLocalStorage } from 'node:async_hooks';
import { execFile } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { runtimeLaunchRefusal } from './runtime-launch-refusal';
export { WINDOWS_ROCM_ABI_PROBE } from './runtime-windows-rocm-abi';

export const WINDOWS_ROCM_PYTHON_REQUEST = 'cpython-3.12-windows-x86_64';
export const WINDOWS_ROCM_MODULE = 'backend.core.windows_rocm.install';
export const WINDOWS_ROCM_IMPORT_PREFIX =
  'from backend.core.windows_rocm.runtime import initialize_dll_search; initialize_dll_search(); ';

export function windowsRocmSelected(
  variant = process.env.OMNIVOICE_TORCH_VARIANT?.trim().toLowerCase(),
  platform = process.platform,
  arch: string = process.arch,
): boolean {
  if (platform !== 'win32' || variant !== 'rocm') return false;
  if (arch !== 'x64') throw new Error('Windows ROCm requires Windows x64 Python 3.12 (cp312).');
  return true;
}

/**
 * Non-throwing form of {@link windowsRocmSelected} for decisions that must not
 * crash: true only when ROCm was asked for on a host that can run it.
 */
export function windowsRocmRequested(
  variant = process.env.OMNIVOICE_TORCH_VARIANT?.trim().toLowerCase(),
  platform = process.platform,
  arch: string = process.arch,
): boolean {
  return platform === 'win32' && variant === 'rocm' && arch === 'x64';
}

/** Records which torch build the last successful install produced. */
export const RUNTIME_TORCH_MARKER = '.runtime-torch';

/** True once an install has completed with the Windows ROCm stack. */
export async function windowsRocmInstalled(project: string): Promise<boolean> {
  try {
    return (await readFile(join(project, RUNTIME_TORCH_MARKER), 'utf8')).trim() === 'windows-rocm';
  } catch {
    return false;
  }
}

/**
 * Hash input identifying the Windows ROCm recipe a project was installed from:
 * the exact recipe bytes.
 *
 * It used to prepend a hardcoded `:python=cp312:win-amd64:` triple, which read
 * as if the interpreter were being verified. Both sides of
 * {@link windowsRocmRecipeMatches} call this function, so that constant appeared
 * on both sides and cancelled out: a recipe bumped to another CPython ABI still
 * compared equal, and nothing could detect it. Whether the staged interpreter
 * really is the pinned one is settled by the backend's own verification
 * (`backend.core.windows_rocm.install --verify-only`, reached through
 * {@link windowsRocmReady}), which fails closed; this stamp only answers "same
 * recipe bytes as the bundle?".
 */
/**
 * Stable salt for the dependency stamp of a Windows ROCm install.
 *
 * `dependencyStamp` folds `windowsRocmStamp` into the hash that
 * `runtimeCompatible` compares against the stamp saved at install time, so these
 * bytes are part of an on-disk contract: changing them marks every existing
 * install incompatible and forces its owner through setup again. Treat the value
 * as frozen and bump it only with a migration.
 *
 * It is not what verifies the interpreter. `windowsRocmReady` runs the backend's
 * `--verify-only` check, which reports the ABI and architecture it actually ran
 * on, and compares those with the recipe's `python` pin.
 */
const WINDOWS_ROCM_STAMP_SALT = ':python=cp312:win-amd64:';

export async function windowsRocmStamp(bundle: string): Promise<Buffer> {
  return Buffer.concat([
    Buffer.from(WINDOWS_ROCM_STAMP_SALT),
    await readFile(join(bundle, 'scripts/windows-rocm-recipe.json')),
  ]);
}

export async function windowsRocmRecipeMatches(bundle: string, project: string): Promise<boolean> {
  const [expected, staged] = await Promise.all([
    windowsRocmStamp(bundle),
    windowsRocmStamp(project),
  ]);
  return expected.equals(staged);
}

/** The interpreter triple `scripts/windows-rocm-recipe.json` pins, or null. */
async function pinnedInterpreter(
  root: string,
): Promise<{ abi: string; platform: string } | null> {
  try {
    const parsed = JSON.parse(
      await readFile(join(root, 'scripts/windows-rocm-recipe.json'), 'utf8'),
    ) as { python?: { abi?: unknown; platform?: unknown } };
    const abi = String(parsed.python?.abi ?? '').trim();
    const platform = String(parsed.python?.platform ?? '').trim();
    return abi && platform ? { abi, platform } : null;
  } catch {
    return null; // an unreadable recipe cannot certify an environment
  }
}

// A result lives only for one read-only readiness decision. Install/repair and
// independently called readiness checks always execute a new native probe.
const readinessDecision = new AsyncLocalStorage<Map<string, Promise<boolean>>>();
export function withWindowsRocmReadinessDecision(check: () => Promise<boolean>): Promise<boolean> {
  return readinessDecision.run(new Map(), check);
}

export async function windowsRocmReady(
  project: string,
  onFailure?: (detail: string) => void,
): Promise<boolean> {
  const decision = readinessDecision.getStore();
  let result = decision?.get(project);
  if (!result) {
    result = probeWindowsRocmReady(project);
    decision?.set(project, result);
  }
  const ready = await result;
  if (!ready)
    onFailure?.('Windows ROCm verification failed; run setup again and inspect its output.');
  return ready;
}

/** Verification fails closed; launch refusals propagate without triggering a reinstall. */
async function probeWindowsRocmReady(project: string): Promise<boolean> {
  // The interpreter triple the recipe pins. `windowsRocmRecipeMatches` has
  // already shown the staged recipe equals the bundle's, so the check below can
  // read the staged copy and still speak for what was shipped.
  const pinned = await pinnedInterpreter(project);
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    HF_HUB_OFFLINE: '1',
    TRANSFORMERS_OFFLINE: '1',
    PYTHONNOUSERSITE: '1',
  };
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;
  return new Promise((resolve, reject) => {
    const python = join(project, '.venv/Scripts/python.exe');
    const complete = (error: unknown, stdout = '') => {
      const refusal = runtimeLaunchRefusal(python, error);
      if (refusal) {
        reject(refusal);
        return;
      }
      let ready = false;
      try {
        const report = JSON.parse(stdout.trim().split(/\r?\n/).at(-1) ?? '');
        // The verifier reports the interpreter it actually ran on; compare it
        // with the recipe instead of trusting `ready` alone, so a project built
        // against another CPython ABI is not certified for this recipe.
        ready =
          !error &&
          report.ready === true &&
          report.scope === 'model-free' &&
          pinned !== null &&
          report.abi === pinned.abi &&
          report.architecture === pinned.platform;
      } catch {
        /* Missing/malformed output cannot certify a native environment. */
      }
      resolve(ready);
    };
    try {
      execFile(
        python,
        ['-m', WINDOWS_ROCM_MODULE, '--verify-only'],
        { cwd: project, env, windowsHide: true, timeout: 120_000, maxBuffer: 256 * 1024 },
        complete,
      );
    } catch (error) {
      const refusal = runtimeLaunchRefusal(python, error);
      if (refusal) reject(refusal);
      else if ((error as NodeJS.ErrnoException)?.code === 'ENOENT') complete(error);
      else reject(error);
    }
  });
}
