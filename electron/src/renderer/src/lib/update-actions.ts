import { toast } from "sonner";
import type { TFunction } from "i18next";
import { getBridge } from "@/components/bridge";
import { dubSession, flushDubDraft } from "@/features/dub/dub-session";
import { ApiError, apiJson } from "@/lib/api/client";
import { hasActiveAppWork } from "@/lib/app-activity";
import type { UpdateState } from "../../../preload/index.d";

export type UpdateAction = "download" | "install" | "retry";

export interface ModelInstallJobs {
  jobs: { state: string }[];
}

export const MODEL_INSTALL_STATUS_PATH = "/models/install/status";
export const ACTIVE_BATCH_JOBS_PATH = "/batch/jobs?status=active&limit=1";

/** Model installs or batch jobs the managed backend is still running. */
export const serverWorkActive = (
  models?: ModelInstallJobs,
  batches?: unknown[],
) =>
  // Older backends answer with other shapes; anything unrecognised is idle.
  Boolean(
    (Array.isArray(models?.jobs) &&
      models.jobs.some((job) =>
        ["queued", "downloading", "running", "installing"].includes(job?.state),
      )) ||
      (Array.isArray(batches) && batches.length > 0),
  );

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

/**
 * True when restarting now would interrupt work: renderer activity, a dub, or
 * server-side model installs and batch jobs (which outlive their uploads). An
 * unknown server status defers the restart rather than risk a running job.
 * Every restart-to-update entry point must use this one check.
 */
export async function updateRestartBlocked(
  timeoutMs = WORK_STATUS_TIMEOUT_MS,
): Promise<boolean> {
  if (hasActiveAppWork() || dubBusy(dubSession.state)) return true;
  const [models, batches] = await Promise.all([
    workStatus<ModelInstallJobs>(MODEL_INSTALL_STATUS_PATH, timeoutMs),
    workStatus<unknown[]>(ACTIVE_BATCH_JOBS_PATH, timeoutMs),
  ]);
  if (models === UNKNOWN || batches === UNKNOWN) return true;
  return (
    hasActiveAppWork() ||
    dubBusy(dubSession.state) ||
    serverWorkActive(models, batches)
  );
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

/** Runs a primary update action through the preload bridge; install defers while work is running. */
export async function runUpdateAction(
  action: UpdateAction,
  state: UpdateState,
  t: TFunction,
): Promise<void> {
  const updates = getBridge()?.updates;
  if (!updates) return;
  try {
    if (action === "download") await updates.download();
    else if (action === "retry") {
      // The updater only downloads from `available`, so a failed download
      // re-checks first and downloads only when the update is offered again.
      const next = await updates.check();
      if (state.availableVersion && next?.status === "available")
        await updates.download();
    } else {
      if (await updateRestartBlocked()) {
        toast.info(t("update.busy"));
        return;
      }
      flushDubDraft();
      await updates.install();
    }
  } catch {
    toast.error(t("update.failed"));
  }
}
