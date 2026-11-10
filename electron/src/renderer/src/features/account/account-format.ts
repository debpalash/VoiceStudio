import { CrownIcon, SproutIcon } from 'lucide-react';
import type { AccountPublicState } from '../../../../preload/index.d';

type Plan = NonNullable<AccountPublicState['plan']>;

/** Up to two initials for an account without a photo; an email stays one initial. */
export function initials(name: string): string {
  const words = name.includes('@') ? [name] : name.trim().split(/\s+/);
  return words
    .slice(0, 2)
    .map((w) => w[0] ?? '')
    .join('')
    .toUpperCase();
}

/**
 * A storage amount as KB, MB or GB. The unit is picked from the rounded value,
 * so just under 1 MB reads "1.0 MB" rather than "1024 KB".
 */
export function storageSize(bytes: number): string {
  const kb = Math.max(0, Math.round(bytes / 1024));
  if (kb < 1024) return `${kb} KB`;
  const mb = (bytes / 1024 ** 2).toFixed(1);
  if (Number(mb) < 1024) return `${mb} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

/** The share of cloud storage used, 0–100; 0 when the plan has no storage. */
export function storageShare(plan: Plan): number {
  return plan.storageLimitBytes > 0
    ? Math.min(100, (plan.storageUsedBytes / plan.storageLimitBytes) * 100)
    : 0;
}

/** Whether the plan is Pro, and the icon its badge shows. */
export function planLook(plan: Plan | null) {
  const pro = plan?.proDesktop === true;
  return { pro, PlanIcon: pro ? CrownIcon : SproutIcon };
}
