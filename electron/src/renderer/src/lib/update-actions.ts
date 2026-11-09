import { toast } from "sonner";
import type { TFunction } from "i18next";
import { Store } from "@tanstack/store";
import { getBridge } from "@/components/bridge";
import { dubSession, flushDubDraft } from "@/features/dub/dub-session";
import { ApiError, apiJson } from "@/lib/api/client";
import { activeAppWorkCount } from "@/lib/app-activity";
import { updateReleaseUrl } from "@/lib/update-format";
import type { UpdateState } from "../../../preload/index.d";

export type UpdateAction = "download" | "install" | "retry" | "later" | "manual";

export interface ModelInstallJobs {
  jobs: { state: string }[];
}

export const MODEL_INSTALL_STATUS_PATH = "/models/install/status";
export const ACTIVE_BATCH_JOBS_PATH = "/batch/jobs?status=active&limit=1";

const ACTIVE_INSTALL_STATES = ["queued", "downloading", "running", "installing"];

/** Model installs and batch jobs the managed backend is still running. */
export const serverWorkCount = (models?: ModelInstallJobs, batches?: unknown[]) =>
  // Older backends answer with other shapes; anything unrecognised is idle.
  (Array.isArray(models?.jobs)
    ? models.jobs.filter((job) => ACTIVE_INSTALL_STATES.includes(job?.state)).length
    : 0) + (Array.isArray(batches) ? batches.length : 0);

export const serverWorkActive = (models?: ModelInstallJobs, batches?: unknown[]) =>
  serverWorkCount(models, batches) > 0;

/** A dub that is still processing or recovering; editing and done are safe to restart. */
export const dubBusy = (value: typeof dubSession.state) =>
  Boolean(value.recovery || value.batchProgress) ||
  !["idle", "editing", "done"].includes(value.phase);

const WORK_STATUS_TIMEOUT_MS = 5_000;
const UNKNOWN = Symbol("unknown work status");

/**
 * Reads one backend work-status endpoint. A stopped backend (unreachable) and
 * an older backend without the route (404/405) have no work to lose; any other
 * failure means the work status is unknown. Our own timeout also surfaces as
 * an unreachable error from the API client, so it is checked first: a backend
 * too busy to answer is the case most likely to be running a job.
 */
async function workStatus<T>(
  path: string,
  timeoutMs: number,
): Promise<T | undefined | typeof UNKNOWN> {
  const signal = AbortSignal.timeout(timeoutMs);
  try {
    return await apiJson<T>(path, { signal });
  } catch (error) {
    if (signal.aborted) return UNKNOWN;
    if (error instanceof ApiError && [0, 404, 405].includes(error.status))
      return undefined;
    return UNKNOWN;
  }
}

/** Running tasks a restart would stop; `unknown` when the backend could not say. */
export interface RestartWork {
  count: number;
  unknown: boolean;
}

/**
 * The work restarting now would interrupt: renderer activity, a dub, and
 * server-side model installs and batch jobs (which outlive their uploads).
 */
export async function updateRestartWork(
  timeoutMs = WORK_STATUS_TIMEOUT_MS,
): Promise<RestartWork> {
  const [models, batches] = await Promise.all([
    workStatus<ModelInstallJobs>(MODEL_INSTALL_STATUS_PATH, timeoutMs),
    workStatus<unknown[]>(ACTIVE_BATCH_JOBS_PATH, timeoutMs),
  ]);
  const unknown = models === UNKNOWN || batches === UNKNOWN;
  return {
    count:
      activeAppWorkCount() +
      (dubBusy(dubSession.state) ? 1 : 0) +
      serverWorkCount(
        models === UNKNOWN ? undefined : models,
        batches === UNKNOWN ? undefined : batches,
      ),
    unknown,
  };
}

/** True when restarting now would interrupt work, or the backend could not say. */
export async function updateRestartBlocked(
  timeoutMs = WORK_STATUS_TIMEOUT_MS,
): Promise<boolean> {
  const work = await updateRestartWork(timeoutMs);
  return work.unknown || work.count > 0;
}

/** A restart waiting for the user to confirm; shown by UpdateRestartConfirm. */
export const restartConfirmation = new Store<RestartWork | null>(null);

/** Restarts into the downloaded update now, saving the dub draft first. */
export async function restartIntoUpdate(): Promise<void> {
  flushDubDraft();
  await getBridge()?.updates.install();
}

/**
 * Every restart-to-update entry point goes through here: it restarts at once
 * when nothing is running, and otherwise asks first.
 */
export async function requestUpdateRestart(): Promise<void> {
  const work = await updateRestartWork();
  if (work.unknown || work.count > 0) {
    restartConfirmation.setState(() => work);
    return;
  }
  await restartIntoUpdate();
}

/** Opens the version's GitHub release page in the user's browser. */
export async function openUpdateRelease(version?: string): Promise<void> {
  await getBridge()?.files.openExternal(updateReleaseUrl(version));
}

/** The one action a state offers in place, or null when none applies. */
export function primaryUpdateAction(
  state: UpdateState | null,
): UpdateAction | null {
  if (!state) return null;
  if (state.status === "available") return "download";
  if (state.status === "downloaded") return "install";
  if (state.status === "error") return "retry";
  return null;
}

/** Runs an update action through the preload bridge; install asks first while work is running. */
export async function runUpdateAction(
  action: UpdateAction,
  state: UpdateState,
  t: TFunction,
): Promise<void> {
  const updates = getBridge()?.updates;
  if (!updates) return;
  try {
    if (action === "download") await updates.download();
    else if (action === "later") {
      if (state.status === "error") await updates.dismiss();
      else await updates.later();
    } else if (action === "manual") await openUpdateRelease(state.availableVersion);
    else if (action === "retry") {
      // The updater only downloads from `available`, so a failed download
      // re-checks first and downloads only when the update is offered again.
      const next = await updates.check();
      if (state.availableVersion && next?.status === "available")
        await updates.download();
    } else await requestUpdateRestart();
  } catch {
    toast.error(t("update.failed"));
  }
}
