import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

/**
 * The dev launch imports torch and friends in a throwaway interpreter before
 * spawning the backend, which then imports them again. The probe only proves
 * the environment is complete, and that cannot change unless the lockfile, the
 * project metadata, the interpreter or site-packages did, so a matching stamp
 * lets the next launch skip it.
 */
const STAMP_FILE = '.voicestudio-dev-probe';

function fingerprint(path: string): string {
  try {
    const info = statSync(path);
    return `${path}:${info.size}:${Math.round(info.mtimeMs)}`;
  } catch {
    return `${path}:absent`;
  }
}

function sitePackages(venv: string): string[] {
  const windows = join(venv, 'Lib', 'site-packages');
  if (existsSync(windows)) return [windows];
  try {
    return readdirSync(join(venv, 'lib'))
      .filter((name) => name.startsWith('python'))
      .map((name) => join(venv, 'lib', name, 'site-packages'));
  } catch {
    return [];
  }
}

export function devProbeStamp(root: string, python: string): string {
  const venv = join(root, '.venv');
  return [
    join(root, 'uv.lock'),
    join(root, 'pyproject.toml'),
    join(venv, 'pyvenv.cfg'),
    python,
    ...sitePackages(venv),
  ]
    .map(fingerprint)
    .join('\n');
}

export function devProbeStamped(root: string, python: string): boolean {
  try {
    return readFileSync(join(root, '.venv', STAMP_FILE), 'utf8') === devProbeStamp(root, python);
  } catch {
    return false;
  }
}

export function writeDevProbeStamp(root: string, python: string): void {
  try {
    writeFileSync(join(root, '.venv', STAMP_FILE), devProbeStamp(root, python));
  } catch {
    // A read-only checkout only loses the shortcut.
  }
}
