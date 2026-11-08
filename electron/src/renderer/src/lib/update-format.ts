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
