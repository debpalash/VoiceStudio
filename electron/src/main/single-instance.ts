import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

/**
 * Development restarts overlap. After a main-process rebuild, electron-vite
 * signals the running Electron and spawns the next one at once, while the old
 * one still holds the single-instance lock as it stops its backend. Asking for
 * the lock then fails, and Electron never emits `ready` in a process whose
 * request failed, so retrying is no help; quitting ends the dev session because
 * electron-vite exits with its child. Each development instance therefore
 * records its pid, and the next one waits for that process to exit before it
 * asks for the lock. Packaged builds never take this path.
 */
export const DEV_RESTART_WAIT_MS = 20_000;
const POLL_INTERVAL_MS = 100;

export function recordDevInstance(pidFile: string, pid: number = process.pid): void {
  try {
    mkdirSync(dirname(pidFile), { recursive: true });
    writeFileSync(pidFile, String(pid), 'utf8');
  } catch {
    /* Best effort: without the file the next restart just takes the old path. */
  }
}

function processIsAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    // ESRCH: gone. EPERM: the pid was reused by another user's process, not ours.
    return false;
  }
}

function readPid(pidFile: string): number | null {
  try {
    const pid = Number(readFileSync(pidFile, 'utf8').trim());
    return Number.isInteger(pid) && pid > 0 ? pid : null;
  } catch {
    return null;
  }
}

/** Resolves once the previously recorded development instance has exited (or the wait times out). */
export async function waitForPreviousDevInstance(
  pidFile: string,
  {
    timeoutMs = DEV_RESTART_WAIT_MS,
    ownPid = process.pid,
    isAlive = processIsAlive,
    now = Date.now,
    sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
  }: {
    timeoutMs?: number;
    ownPid?: number;
    isAlive?: (pid: number) => boolean;
    now?: () => number;
    sleep?: (ms: number) => Promise<void>;
  } = {},
): Promise<void> {
  const previous = readPid(pidFile);
  if (previous === null || previous === ownPid) return;
  const deadline = now() + timeoutMs;
  while (isAlive(previous) && now() < deadline) await sleep(POLL_INTERVAL_MS);
}
