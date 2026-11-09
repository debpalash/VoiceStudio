import { spawn } from 'node:child_process';
import { rm, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { app } from 'electron';
import { backendRoot } from './backend';

export function nativeCleanupHelperPath(): string {
  const name = 'voicestudio-desktop-bridge' + (process.platform === 'win32' ? '.exe' : '');
  return app.isPackaged
    ? join(process.resourcesPath, 'native', name)
    : join(backendRoot(), 'native', 'desktop-bridge', 'target', 'debug', name);
}

export async function scheduleUninstallCleanup(
  targets: Array<{ path: string; kind: 'app' | 'models' }>,
): Promise<void> {
  if (!targets.length) throw new Error('no_cleanup_targets');
  const plan = join(tmpdir(), `voicestudio-uninstall-${randomUUID()}.json`);
  await writeFile(plan, JSON.stringify({ paths: targets }), {
    encoding: 'utf8',
    flag: 'wx',
    mode: 0o600,
  });
  const child = spawn(nativeCleanupHelperPath(), ['--cleanup', String(process.pid), plan], {
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
  });
  try {
    await new Promise<void>((resolve, reject) => {
      child.once('spawn', resolve);
      child.once('error', reject);
    });
    child.unref();
  } catch (error) {
    await rm(plan, { force: true }).catch(() => undefined);
    throw error;
  }
}
