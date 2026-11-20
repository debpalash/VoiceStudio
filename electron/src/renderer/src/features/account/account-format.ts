import { CrownIcon, SproutIcon } from "lucide-react";
import type { AccountPublicState } from "../../../../preload/index.d";

type Plan = NonNullable<AccountPublicState["plan"]>;

/** Up to two initials for an account without a photo; an email stays one initial. */
export function initials(name: string): string {
  const words = name.includes("@") ? [name] : name.trim().split(/\s+/);
  return words
    .slice(0, 2)
    .map((w) => w[0] ?? "")
    .join("")
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

/**
 * Whether the signed-in account has Pro. The one rule every Pro surface uses.
 * `pro.desktop` is authoritative when the entitlements were read (true or false);
 * when they could not be (null), fall back to the plan itself so a lifetime or
 * purchased plan is never shown as Free: any non-free plan code, or a plan that
 * came from a purchase or grant rather than the default.
 */
export function isProPlan(plan: Plan | null | undefined): boolean {
  if (!plan) return false;
  if (typeof plan.proDesktop === "boolean") return plan.proDesktop;
  const code = plan.planCode.trim().toLowerCase();
  if (/pro|lifetime/.test(code)) return true;
  return code !== "" && code !== "free" && plan.planOrigin !== "default";
}

/** Whether the plan is Pro, and the icon its badge shows. */
export function planLook(plan: Plan | null) {
  const pro = isProPlan(plan);
  return { pro, PlanIcon: pro ? CrownIcon : SproutIcon };
}
