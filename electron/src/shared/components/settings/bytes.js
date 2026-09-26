/**
 * Byte formatting for the Storage panels.
 *
 * Deliberately not `models/format.fmtBytes`: that one floors at kilobytes
 * (`Math.round(n / 1024)` + " KB"), so a 391-byte config file renders as
 * "0 KB" — which reads as "nothing here" for a folder that very much exists.
 * These panels list real folders and must be able to say "391 B".
 */

/** "1.4 GB" / "820 MB" / "12 KB" / "391 B". Pure. */
export function fmtBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}
