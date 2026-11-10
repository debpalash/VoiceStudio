import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

/**
 * Hand the single-instance lock from one development Electron to the next (#3813).
 *
 * electron-vite restarts Electron on a main-process edit by starting the new
 * process while the old one is still shutting down and holding the lock. The
 * new process then fails `requestSingleInstanceLock()`, quits, and electron-vite
 * exits with it, ending the dev session. Retrying the lock cannot help: Electron
 * never emits `ready` in a process whose lock request failed. So each development
 * instance records its pid, and the next one waits for that pid to exit before
 * asking for the lock. Packaged builds never wait.
 */
export const DEV_INSTANCE_WAIT_MS = 20_000;

export function devInstanceFile(userData: string): string {
  return join(userData, 'dev-instance.pid');
}

export function processAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    // EPERM: it exists but belongs to someone else.
    return (error as NodeJS.ErrnoException).code === 'EPERM';
  }
}

function blockingSleep(ms: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

/**
 * Block until the previously recorded development instance has exited, up to
 * `timeoutMs`. Returns true when the lock should now be free. Runs before
 * `app.whenReady()`, so blocking the main thread delays nothing else.
 */
export function waitForPreviousDevInstance({
  file,
  self = process.pid,
  timeoutMs = DEV_INSTANCE_WAIT_MS,
  alive = processAlive,
  sleep = blockingSleep,
  now = Date.now,
}: {
  file: string;
  self?: number;
  timeoutMs?: number;
  alive?: (pid: number) => boolean;
  sleep?: (ms: number) => void;
  now?: () => number;
}): boolean {
  let pid: number;
  try {
    pid = Number(readFileSync(file, 'utf8').trim());
  } catch {
    return true;
  }
  if (!Number.isSafeInteger(pid) || pid <= 0 || pid === self) return true;
  const deadline = now() + timeoutMs;
  while (alive(pid)) {
    if (now() >= deadline) return false;
    sleep(100);
  }
  return true;
}

/** Record this instance once it holds the lock. Best effort: dev convenience only. */
export function recordDevInstance(file: string, pid = process.pid): void {
  try {
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, String(pid), 'utf8');
  } catch {
    // Without the record the next restart simply behaves as before.
  }
}
