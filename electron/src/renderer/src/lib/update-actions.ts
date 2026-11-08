import { toast } from "sonner";
import type { TFunction } from "i18next";
import { getBridge } from "@/components/bridge";
import { flushDubDraft } from "@/features/dub/dub-session";
import { hasActiveAppWork } from "@/lib/app-activity";
import type { UpdateState } from "../../../preload/index.d";

export type UpdateAction = "download" | "install" | "retry";

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
    else if (action === "retry")
      await (state.availableVersion ? updates.download() : updates.check());
    else {
      if (hasActiveAppWork()) {
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
