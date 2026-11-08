import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { getBridge } from "@/components/bridge";
import { runRendererTask } from "@/lib/global-error-recovery";
import { formatBytes, updateErrorKey } from "@/lib/update-format";
import { primaryUpdateAction, runUpdateAction } from "@/lib/update-actions";
import { router } from "@/router";
import type { UpdateState } from "../../../preload/index.d";

// Versions the user chose "Later" for stay hidden until the next launch.
const laterVersions = new Set<string>();

export const updateToastId = (state: UpdateState) =>
  `desktop-update-${state.availableVersion ?? "check"}`;

function UpdateToast({ state, id }: { state: UpdateState; id: string }) {
  const { t } = useTranslation();
  const version = state.availableVersion ?? "";
  const action = primaryUpdateAction(state);
  const hide = () => toast.dismiss(id);
  const later = () => {
    if (state.availableVersion) laterVersions.add(state.availableVersion);
    void getBridge()
      ?.updates.dismiss()
      .catch(() => {});
    hide();
  };
  const more = () =>
    runRendererTask("Navigate to updates", () =>
      router.navigate({ to: "/settings/updates" }),
    );
  const pct = Math.round(state.progress);
  const title =
    state.status === "error"
      ? t(updateErrorKey(state.error))
      : state.status === "downloading"
        ? t("update.downloading", { pct })
        : state.status === "downloaded"
          ? t("update.prompt_ready")
          : t("update.prompt_available", { version });
  return (
    <div
      role="status"
      className="flex w-[356px] max-w-full flex-col gap-2 rounded-lg border bg-popover p-3 text-sm text-popover-foreground shadow-lg"
    >
      <p className="font-medium">{title}</p>
      {state.status === "downloading" && (
        <>
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
          >
            <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
          </div>
          {state.totalBytes ? (
            <p className="text-xs text-muted-foreground tabular-nums">
              {formatBytes(state.transferredBytes || 0)} /{" "}
              {formatBytes(state.totalBytes)}
            </p>
          ) : null}
        </>
      )}
      {state.status === "error" && state.error && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">
            {t("update.prompt_details")}
          </summary>
          <p className="mt-1 break-words">{state.error}</p>
        </details>
      )}
      <div className="flex items-center gap-2">
        {action === "download" && (
          <Button
            size="sm"
            onClick={() => void runUpdateAction("download", state, t)}
          >
            {t("update.download")}
          </Button>
        )}
        {action === "install" && (
          <Button
            size="sm"
            onClick={() => void runUpdateAction("install", state, t)}
          >
            {t("update.restart")}
          </Button>
        )}
        {action === "retry" && (
          <Button
            size="sm"
            onClick={() => void runUpdateAction("retry", state, t)}
          >
            {t("update.retry")}
          </Button>
        )}
        {state.status === "downloading" ? (
          <Button size="sm" variant="ghost" onClick={hide}>
            {t("update.prompt_hide")}
          </Button>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            onClick={state.status === "error" ? hide : later}
          >
            {t("update.toast_later")}
          </Button>
        )}
        <Button size="sm" variant="link" className="ml-auto" onClick={more}>
          {t("update.prompt_more")}
        </Button>
      </div>
    </div>
  );
}

/** Persistent, in-place update toast for every screen; mounted at the App root. */
export function UpdatePrompt() {
  useEffect(() => {
    const updates = getBridge()?.updates;
    if (!updates) return;
    let shown: string | null = null;
    const apply = (state: UpdateState) => {
      const visible =
        (state.status === "available" ||
          state.status === "downloading" ||
          state.status === "downloaded" ||
          state.status === "error") &&
        !(state.availableVersion && laterVersions.has(state.availableVersion));
      if (!visible) {
        if (shown) toast.dismiss(shown);
        shown = null;
        return;
      }
      const id = updateToastId(state);
      if (shown && shown !== id) toast.dismiss(shown);
      shown = id;
      toast.custom(() => <UpdateToast state={state} id={id} />, {
        id,
        duration: Infinity,
      });
    };
    let active = true;
    void updates
      .getState()
      .then((state) => active && apply(state))
      .catch(() => {});
    const unsubscribe = updates.onState(apply);
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);
  return null;
}
