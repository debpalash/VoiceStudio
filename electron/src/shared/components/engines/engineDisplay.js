import { cn } from '@/lib/utils';
import SupertonicLicenseDialog from '../SupertonicLicenseDialog';
import PocketTTSLicenseDialog from '../PocketTTSLicenseDialog';

/**
 * Presentation helpers shared by the engine list and its detail panel: the
 * status phrase / runs-on device for a row, GPU chip styling, license-dialog
 * registry and size formatting. No React.
 */
/** Engines that gate first use behind an in-app license acceptance dialog. */
export const LICENSE_DIALOGS = {
  supertonic3: SupertonicLicenseDialog,
  pockettts: PocketTTSLicenseDialog,
};

/** Heuristic detector for the "license not accepted" backend reason. */
export function reasonMentionsLicense(reason) {
  if (!reason || typeof reason !== 'string') return false;
  return /license not accepted/i.test(reason);
}

export function fmtDiskBytes(value, unknownLabel, locale) {
  if (value == null) return unknownLabel;
  const [divisor, unit, digits] =
    value >= 1024 ** 3
      ? [1024 ** 3, 'gigabyte', 2]
      : value >= 1024 ** 2
        ? [1024 ** 2, 'megabyte', 1]
        : [1024, 'kilobyte', 0];
  return new Intl.NumberFormat(locale, {
    style: 'unit',
    unit,
    unitDisplay: 'short',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value / divisor);
}

export const GPU_LABEL = {
  cuda: 'CUDA',
  mps: 'MPS',
  rocm: 'ROCm',
  xpu: 'XPU',
  cpu: 'CPU',
  directml: 'DirectML',
};

// GPU compat chip — base + per-device tint (detail panel only now).
const CHIP_BASE =
  'inline-block px-[6px] py-px text-[10px] font-mono font-semibold tracking-[0.04em] uppercase rounded border select-none';
const CHIP_DEVICE = {
  cuda: 'text-[#76b900] border-[color:color-mix(in_srgb,#76b900_45%,transparent)] bg-[color:color-mix(in_srgb,#76b900_10%,transparent)]',
  mps: 'text-[#b8b8b8] border-[color:color-mix(in_srgb,#b8b8b8_45%,transparent)] bg-[color:color-mix(in_srgb,#b8b8b8_10%,transparent)]',
  rocm: 'text-[#ed1c24] border-[color:color-mix(in_srgb,#ed1c24_45%,transparent)] bg-[color:color-mix(in_srgb,#ed1c24_10%,transparent)]',
  vulkan:
    'text-[#b62e3b] border-[color:color-mix(in_srgb,#b62e3b_45%,transparent)] bg-[color:color-mix(in_srgb,#b62e3b_10%,transparent)]',
  xpu: 'text-[#0071c5] border-[color:color-mix(in_srgb,#0071c5_45%,transparent)] bg-[color:color-mix(in_srgb,#0071c5_10%,transparent)]',
  cpu: 'text-muted-foreground border-border bg-transparent',
};
// The "device this host actually uses" highlight. `is-effective` is kept as a
// literal marker class — the suite asserts the chip carries it.
const CHIP_EFFECTIVE =
  'is-effective shadow-[0_0_0_1px_var(--chrome-accent,#fe8019)] border-[var(--chrome-accent,#fe8019)] text-foreground font-bold';
export const chipCls = (device, effective) =>
  cn(CHIP_BASE, CHIP_DEVICE[device] || CHIP_DEVICE.cpu, effective && CHIP_EFFECTIVE);

// routing_status → status phrase + tone. `unavailable` is handled separately
// (it reads "Needs setup"); anything else unknown falls back to "Unknown".
const ROUTING_STATUS = {
  accelerated: { tone: 'text-success', k: 'engines.routingAccelerated' },
  cpu_fallback: { tone: 'text-warn', k: 'engines.routingCpuFallback' },
  cpu_only: { tone: 'text-muted-foreground', k: 'engines.routingCpuOnly' },
  'n/a': { tone: 'text-success', k: 'engines.available' },
};

/** Human duration: "0.4s" for ≥1 s, "820 ms" below. */
export function fmtDuration(ms) {
  const n = Number(ms) || 0;
  return n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${Math.round(n)} ms`;
}

export const deviceLabel = (t, g) =>
  g === 'vulkan' ? t('engines.gpuVulkan') : GPU_LABEL[g] || g.toUpperCase();

/** The one device this engine would run on here, for the Runs-on column. */
export function runsOn(t, b) {
  if (b.routing_status === 'n/a') return { text: t('engines.routingRemote'), muted: false };
  if (b.available && b.routing_status && b.routing_status !== 'unavailable' && b.effective_device) {
    return { text: deviceLabel(t, b.effective_device), muted: false };
  }
  // Not runnable right now: name what it would need (its best device).
  return { text: deviceLabel(t, b.gpu_compat[0]), muted: true };
}

/** The Status column phrase + colour for a row. */
export function statusOf(t, b, install) {
  const job = install?.job;
  if (!b.available) {
    if (job?.state === 'running') return { text: t('engines.installing'), cls: 'text-accent' };
    if (job?.state === 'failed') return { text: t('engines.failed'), cls: 'text-destructive' };
    return { text: t('catalogue.status_setup'), cls: 'text-muted-foreground' };
  }
  if (b.routing_status === 'unavailable') {
    return { text: t('catalogue.status_setup'), cls: 'text-muted-foreground' };
  }
  if (!b.routing_status) return { text: t('engines.available'), cls: 'text-success' };
  const known = ROUTING_STATUS[b.routing_status];
  return known
    ? { text: t(known.k), cls: known.tone }
    : { text: t('engines.routingUnknown'), cls: 'text-muted-foreground' };
}

export const LABEL =
  'font-mono text-[11px] font-medium uppercase tracking-[var(--chrome-label-track)] text-muted-foreground';
