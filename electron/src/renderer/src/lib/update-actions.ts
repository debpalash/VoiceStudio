import { toast } from "sonner";
import type { TFunction } from "i18next";
import { getBridge } from "@/components/bridge";
import { dubSession, flushDubDraft } from "@/features/dub/dub-session";
import { apiJson } from "@/lib/api/client";
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

const optional = <T>(path: string) =>
  apiJson<T>(path).catch(() => undefined);

/**
 * True when restarting now would interrupt work: renderer activity, a dub, or
 * server-side model installs and batch jobs (which outlive their uploads).
 * Every restart-to-update entry point must use this one check.
 */
export async function updateRestartBlocked(): Promise<boolean> {
  if (hasActiveAppWork() || dubBusy(dubSession.state)) return true;
  const [models, batches] = await Promise.all([
    optional<ModelInstallJobs>(MODEL_INSTALL_STATUS_PATH),
    optional<unknown[]>(ACTIVE_BATCH_JOBS_PATH),
  ]);
  return hasActiveAppWork() || dubBusy(dubSession.state) || serverWorkActive(models, batches);
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
