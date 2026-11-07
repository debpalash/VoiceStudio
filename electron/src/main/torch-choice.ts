/**
 * Keeps an explicit ROCm choice across launches. Setup reads the variant from the
 * environment, so a launch without the variable picked the CPU build and sent a
 * working ROCm install back to setup (#3804). It lives in the env file the backend
 * already loads, so the shell and the backend's sidecar installs agree on it.
 */

import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { rocmTorchApplies } from './runtime-project';

const KEY = 'OMNIVOICE_TORCH_VARIANT';
const ASSIGNMENT = new RegExp(`^\\s*(?:export\\s+)?${KEY}\\s*=(.*)$`);

/** Same location as `backend/core/user_env.py`. */
export function userEnvPath(env: NodeJS.ProcessEnv = process.env, home = homedir()): string {
  return env.OMNIVOICE_ENV_FILE?.trim() || join(home, '.config', 'omnivoice', 'env');
}

/** Missing is empty; any other read error throws, so a rewrite cannot drop other keys. */
function readLines(path: string): string[] {
  let text: string;
  try {
    text = readFileSync(path, 'utf8');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
    throw error;
  }
  const lines = text.split(/\r?\n/);
  if (lines.at(-1) === '') lines.pop();
  return lines;
}

/** The effective (last) saved value, as dotenv reads it. */
function savedValue(lines: string[]): string | undefined {
  const raw = lines.map((line) => ASSIGNMENT.exec(line)?.[1]).findLast((v) => v !== undefined);
  if (raw === undefined) return undefined;
  const text = raw.trim();
  const quoted = /^(['"])(.*?)\1/.exec(text);
  return (quoted ? quoted[2] : text.split(/\s+#/)[0]).trim().toLowerCase() || undefined;
}

function writeLines(path: string, lines: string[]): void {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  const temporary = `${path}.${process.pid}.tmp`;
  try {
    // The file can hold an HF token.
    writeFileSync(temporary, lines.map((line) => `${line}\n`).join(''), {
      encoding: 'utf8',
      mode: 0o600,
    });
    renameSync(temporary, path);
  } finally {
    rmSync(temporary, { force: true });
  }
}

/** Call before anything resolves the torch variant. A value set for this launch wins. */
export function applySavedTorchVariant(
  env: NodeJS.ProcessEnv = process.env,
  path: string = userEnvPath(env),
): void {
  if (env[KEY]?.trim()) return;
  try {
    const saved = savedValue(readLines(path));
    if (saved) env[KEY] = saved;
  } catch (error) {
    console.warn(`[setup] could not read the saved torch choice: ${(error as Error).name}`);
  }
}

/**
 * Call once the runtime is ready: a failed install must not make ROCm stick. Choosing
 * another build forgets a saved ROCm or CPU choice. Never throws, because saving this
 * must not stop the engine from starting.
 */
export function rememberTorchVariant(
  env: NodeJS.ProcessEnv = process.env,
  platform: NodeJS.Platform = process.platform,
  path: string = userEnvPath(env),
): void {
  const choice = env[KEY]?.trim().toLowerCase();
  if (!choice) return;
  try {
    const lines = readLines(path);
    const saved = savedValue(lines);
    if (choice === 'rocm') {
      if (saved !== 'rocm' && rocmTorchApplies(platform)) {
        writeLines(path, [...lines.filter((line) => !ASSIGNMENT.test(line)), `${KEY}=rocm`]);
      }
    } else if ((saved === 'rocm' || saved === 'cpu') && saved !== choice) {
      writeLines(
        path,
        lines.filter((line) => !ASSIGNMENT.test(line)),
      );
    }
  } catch (error) {
    console.warn(`[setup] could not save the torch choice: ${(error as Error).name}`);
  }
}

/**
 * Save the setup screen's CPU choice now: the install it starts can still fail. Returns
 * false when it could not be saved, so the caller can say so; the install still runs.
 */
export function saveTorchVariant(variant: 'cpu', path: string = userEnvPath()): boolean {
  try {
    const lines = readLines(path).filter((line) => !ASSIGNMENT.test(line));
    writeLines(path, [...lines, `${KEY}=${variant}`]);
    return true;
  } catch (error) {
    console.warn(`[setup] could not save the torch choice: ${(error as Error).name}`);
    return false;
  }
}

/** Failures the smaller CPU download can get past: not a full disk or a blocked program. */
const DOWNLOAD_FAILURES = new Set(['RUNTIME_DOWNLOAD_STALLED', 'RUNTIME_DOWNLOAD_INTERRUPTED']);
const STALL_BEFORE_OFFER_S = 300;

/**
 * Whether the setup screen offers the CPU build. The multi-GB CUDA wheel has no mirror
 * (#3748, #3720), so without this a bad connection means no working app at all.
 */
export function cpuSetupOffered(input: {
  stage: string;
  failureCode?: string;
  progress?: { downloadsComplete?: boolean; stalled?: boolean; stalledSeconds?: number };
  choice: { variant: string; explicit: boolean };
  cpuApplies: boolean;
}): boolean {
  const { stage, failureCode, progress, choice, cpuApplies } = input;
  if (!cpuApplies || choice.explicit || choice.variant !== 'default') return false;
  if (stage === 'setup_required') return DOWNLOAD_FAILURES.has(failureCode ?? '');
  return (
    stage === 'installing' &&
    progress?.downloadsComplete === false &&
    progress.stalled === true &&
    (progress.stalledSeconds ?? 0) >= STALL_BEFORE_OFFER_S
  );
}
