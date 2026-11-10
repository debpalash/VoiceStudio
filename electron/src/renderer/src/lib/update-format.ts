import type { UpdateState } from "../../../preload/index.d";

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${Math.max(0, Math.round(bytes))} B`;
}

const NETWORK_ERROR =
  /\b5\d\d\b|<\s*(?:!doctype|html|head|body)|ETIMEDOUT|ENOTFOUND|ECONNRESET|ECONNREFUSED|EAI_AGAIN|ESOCKETTIMEDOUT|ENETUNREACH|net::ERR_|timed? ?out|bad gateway|service unavailable|gateway time-?out/i;

/** i18n key for a user-facing updater error; the raw text belongs behind "Details". */
export function updateErrorKey(
  raw?: string,
): "update.error_network" | "update.error_generic" {
  return raw && NETWORK_ERROR.test(raw)
    ? "update.error_network"
    : "update.error_generic";
}

export type UpdateErrorTitleKey =
  | "update.error_network"
  | "update.error_generic"
  | "update.error_check"
  | "update.error_download"
  | "update.error_verify"
  | "update.error_install"
  | "update.error_not_applied";

/** The headline for a failed update, by the step that failed. */
export function updateErrorTitleKey(
  state: Pick<UpdateState, "error" | "errorKind">,
): UpdateErrorTitleKey {
  switch (state.errorKind) {
    case "check":
      return updateErrorKey(state.error) === "update.error_network"
        ? "update.error_network"
        : "update.error_check";
    case "download":
      return "update.error_download";
    case "verify":
      return "update.error_verify";
    case "install":
      return "update.error_install";
    case "not-applied":
      return "update.error_not_applied";
    default:
      return updateErrorKey(state.error);
  }
}

const RELEASES_PAGE = "https://github.com/debpalash/VoiceStudio/releases";

/** The GitHub release page for a version, or the releases list when unknown. */
export function updateReleaseUrl(version?: string): string {
  const clean = version?.trim().replace(/^v/, "");
  return clean && /^\d+\.\d+\.\d+(?:-beta\.\d+)?$/.test(clean)
    ? `${RELEASES_PAGE}/tag/v${clean}`
    : RELEASES_PAGE;
}

export interface ShownProgress {
  status: UpdateState["status"];
  pct: number;
  at: number;
}

/**
 * Progress text refreshes about once a second, or on each whole percent,
 * so speed and time left stay readable instead of flickering.
 */
export function progressDue(
  shown: ShownProgress | null,
  next: Pick<ShownProgress, "status" | "pct">,
  now: number,
): boolean {
  return (
    !shown ||
    shown.status !== next.status ||
    Math.abs(next.pct - shown.pct) >= 1 ||
    now - shown.at >= 1_000
  );
}
