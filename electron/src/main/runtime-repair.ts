import { stat } from 'node:fs/promises';
import { join } from 'node:path';

/**
 * What to do when the managed Python environment is not usable (#2988, #2948,
 * #2922, #2787, #2783, #2621). A runtime that was installed once and lost its
 * interpreter (antivirus, disk cleanup, an interrupted update) is rebuilt by the
 * same flow the setup wizard runs, once per launch; a first run still waits for
 * the user's explicit choice (download size, location, region).
 */
export type MissingRuntimeAction =
  | 'repair' // re-run setup automatically
  | 'wizard' // first run: show the setup screen and let the user start it
  | 'repair_failed' // already tried this launch: stop and show the message
  | 'setup_api'; // source checkout: the developer runs `bun run setup:api`

export interface MissingRuntimeFacts {
  packaged: boolean;
  /** The project folder holds traces of an earlier install (marker, .venv, bundle). */
  previouslyInstalled: boolean;
  /** An automatic repair has already been started during this launch. */
  repairAttempted: boolean;
  /** The platform can never resolve the runtime (Intel Mac): repairing would only fail. */
  unsupported?: boolean;
  /**
   * Windows ROCm is requested but this runtime was never installed with it.
   * Switching means a multi-GB download from new hosts, so the user starts it;
   * a working CUDA/CPU runtime is never silently replaced.
   */
  rocmNotYetInstalled?: boolean;
}

export function decideMissingRuntime(facts: MissingRuntimeFacts): MissingRuntimeAction {
  if (!facts.packaged) return 'setup_api';
  if (facts.unsupported || facts.rocmNotYetInstalled || !facts.previouslyInstalled) return 'wizard';
  return facts.repairAttempted ? 'repair_failed' : 'repair';
}

/** True when the folder shows an earlier install (never true for a fresh machine). */
export async function runtimeWasInstalled(project: string): Promise<boolean> {
  for (const name of ['.runtime-ready', '.runtime-installing', '.venv', 'pyproject.toml']) {
    try {
      await stat(join(project, name));
      return true;
    } catch {
      /* absent */
    }
  }
  return false;
}
