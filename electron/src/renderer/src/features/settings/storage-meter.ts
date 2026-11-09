import type { StorageWarning } from '@shared/utils/storageWarnings';

/** Disk bar fill: amber above 85% used, red above 95%. */
export function diskMeterClass(usedPercent: number): string {
  if (usedPercent > 95) return 'bg-destructive';
  if (usedPercent > 85) return 'bg-warning';
  return 'bg-primary';
}

const FULL_DISK_KINDS = new Set(['low_disk', 'volume_pressure']);

/**
 * One "disk is full" warning per page: the most severe low-space warning
 * wins (the report sorts critical first). Scan warnings stay as they are.
 */
export function visibleStorageWarnings(warnings: StorageWarning[] = []): StorageWarning[] {
  const full = warnings.filter((warning) => FULL_DISK_KINDS.has(warning.kind));
  const top =
    full.find((warning) => warning.severity === 'critical') ??
    full.find((warning) => warning.kind === 'low_disk') ??
    full[0];
  return [
    ...(top ? [top] : []),
    ...warnings.filter((warning) => !FULL_DISK_KINDS.has(warning.kind)),
  ];
}
