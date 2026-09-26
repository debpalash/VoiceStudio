export interface StorageWarning {
  kind: string;
  severity: string;
  path: string;
  free_gb?: number;
  min_free_gb?: number;
  used_percent?: number;
}
export function warningText(
  t: (key: string, options: Record<string, unknown>) => string,
  warning: StorageWarning,
): string;
