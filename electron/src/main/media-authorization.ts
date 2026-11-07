import { randomBytes } from 'node:crypto';
import { execFile } from 'node:child_process';
import { mkdir, realpath, rm, stat, writeFile } from 'node:fs/promises';
import { isAbsolute, join } from 'node:path';
import { promisify } from 'node:util';

export type MediaTool = 'ffmpeg' | 'ffprobe';
export function assertMediaTool(value: unknown): MediaTool {
  if (value !== 'ffmpeg' && value !== 'ffprobe') throw new Error('Invalid media tool');
  return value;
}

async function writeAuthorization(dataDir: string, kind: string, path: string, scope?: string) {
  await mkdir(dataDir, { recursive: true, mode: 0o700 });
  const directory = join(await realpath(dataDir), '.path-authorizations');
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const authorization = randomBytes(32).toString('hex');
  await writeFile(
    join(directory, authorization + '.json'),
    JSON.stringify(scope ? { token: authorization, kind, path, scope } : { token: authorization, kind, path }),
    { flag: 'wx', mode: 0o600 },
  );
  return { authorization, path };
}

export async function authorizeMediaTool(dataDir: string, selected: string, kind: MediaTool) {
  assertMediaTool(kind);
  if (!isAbsolute(dataDir) || !isAbsolute(selected)) throw new Error('Expected absolute paths');
  const path = await realpath(selected);
  if (!(await stat(path)).isFile()) throw new Error('Expected an executable file');
  const { stdout, stderr } = await promisify(execFile)(path, ['-version'], {
    windowsHide: true,
    timeout: 5000,
    maxBuffer: 128 * 1024,
  });
  if (!(stdout + stderr).trimStart().startsWith(kind + ' version ')) {
    throw new Error('Selected executable does not match the media tool');
  }
  return writeAuthorization(dataDir, kind, path);
}

async function resolveModelsDirectory(selected: string) {
  if (!isAbsolute(selected)) throw new Error('Expected an absolute models directory');
  const path = await realpath(selected);
  if (!(await stat(path)).isDirectory()) throw new Error('Expected a directory');
  return path;
}

/** Authorize a directory for operations that may write model files. */
export async function authorizeModelsDirectory(dataDir: string, selected: string) {
  if (!isAbsolute(dataDir)) throw new Error('Expected an absolute data directory');
  if (selected === '') return writeAuthorization(dataDir, 'models_dir', '');

  if (!isAbsolute(selected)) throw new Error('Expected an absolute models directory');
  await mkdir(selected, { recursive: true });
  const path = await resolveModelsDirectory(selected);
  const probe = join(path, `.voicestudio-write-test-${randomBytes(8).toString('hex')}`);
  try {
    await writeFile(probe, 'ok', { flag: 'wx', mode: 0o600 });
  } catch (error) {
    throw new Error('Directory is not writable', { cause: error });
  } finally {
    await rm(probe, { force: true }).catch(() => undefined);
  }
  return writeAuthorization(dataDir, 'models_dir', path);
}

/**
 * Authorize a model source without creating or modifying anything in it. The
 * grant is scoped to discovery for one engine: unscoped `models_dir` grants
 * set the global download folder, so a read-only pick must never be redeemable
 * there (the backend rejects any scoped grant at those endpoints).
 */
export async function authorizeModelsDirectoryReadOnly(
  dataDir: string,
  selected: string,
  engine: string,
) {
  if (!isAbsolute(dataDir)) throw new Error('Expected an absolute data directory');
  if (!/^[a-z0-9-]+$/.test(engine)) throw new Error('Invalid engine');
  return writeAuthorization(
    dataDir,
    'models_dir',
    await resolveModelsDirectory(selected),
    `local-model-discover:${engine}`,
  );
}
