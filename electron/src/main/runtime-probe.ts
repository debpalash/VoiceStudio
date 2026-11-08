import { spawn, spawnSync } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { runtimeLaunchRefusal } from './runtime-launch-refusal';

export const WINDOWS_ROCM_IMPORT_TIMEOUT_MS = 180_000;
export interface RuntimeProbeOptions {
  timeoutMs: number;
  signal?: AbortSignal;
  onSpawn?: (child: ChildProcess) => void;
  onClose?: (child: ChildProcess) => void;
  /** Resolve with stderr appended to stdout (uv writes its plan and debug log there). */
  captureStderr?: boolean;
}

/** Terminate the probe tree; its close event remains the cleanup/settlement gate. */
function terminateProbe(child: ChildProcess): void {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], {
      windowsHide: true,
      stdio: 'ignore',
      timeout: 5000,
    });
  } else {
    try {
      process.kill(-child.pid, 'SIGKILL');
    } catch {
      /* Process group already exited; direct kill below is the fallback. */
    }
  }
  child.kill('SIGKILL');
}

/** A deadline must release native DLLs before another install/repair can start. */
export function runRuntimeProbe(
  command: string,
  args: string[],
  cwd: string,
  env: NodeJS.ProcessEnv,
  options: RuntimeProbeOptions,
): Promise<string> {
  options.signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    let child: ChildProcess;
    try {
      child = spawn(command, args, {
        cwd,
        env,
        windowsHide: true,
        detached: process.platform !== 'win32',
        stdio: ['pipe', 'pipe', 'pipe'],
      });
    } catch (error) {
      // Windows throws a refused launch here instead of emitting `error`.
      // Keep the OS code: setup maps EACCES/EPERM to its access hint.
      const refusal = runtimeLaunchRefusal(command, error);
      reject(
        refusal ? Object.assign(refusal, { code: (error as NodeJS.ErrnoException).code }) : error,
      );
      return;
    }
    let failure: Error | undefined;
    let stdout = '';
    const stop = (error: Error) => {
      failure ??= error;
      terminateProbe(child);
    };
    const timer = setTimeout(
      () =>
        stop(
          Object.assign(
            new Error(`Runtime native import timed out after ${options.timeoutMs} ms`),
            { code: 'ETIMEDOUT' },
          ),
        ),
      options.timeoutMs,
    );
    const abort = () =>
      stop(
        options.signal?.reason instanceof Error
          ? options.signal.reason
          : new Error('Runtime probe cancelled'),
      );
    options.signal?.addEventListener('abort', abort, { once: true });
    child.stdin?.on('error', () => {});
    child.stdout?.on('data', (chunk: Buffer | string) => {
      stdout = (stdout + chunk.toString()).slice(-65_536);
    });
    let stderr = '';
    if (options.captureStderr)
      child.stderr?.on('data', (chunk: Buffer | string) => {
        // A verbose uv dry run lists hundreds of packages; keep enough for all of them.
        stderr = (stderr + chunk.toString()).slice(-4_194_304);
      });
    child.once('error', (error) => {
      failure ??= error;
    });
    child.once('close', (code) => {
      clearTimeout(timer);
      options.signal?.removeEventListener('abort', abort);
      options.onClose?.(child);
      // Never reject on the timer alone: close proves the child and its stdio
      // have actually exited, so retry cannot race the old native process.
      if (failure || code !== 0)
        reject(
          Object.assign(failure ?? new Error(`Runtime setup exited with code ${code}`), {
            stdout,
            code: failure && 'code' in failure ? failure.code : code,
          }),
        );
      else resolve(options.captureStderr ? `${stdout}\n${stderr}` : stdout);
    });
    options.onSpawn?.(child);
    if (options.signal?.aborted) abort();
  });
}
