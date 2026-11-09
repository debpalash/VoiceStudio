export type DistributionTarget = "direct" | "microsoft-store" | "snap-store" | "mac-app-store";

export type ApplicationUpdateOwner = "application" | "store";
export type RuntimeCodePolicy = "user-initiated-download" | "bundled-only";
export type FileAccessPolicy = "user-selected" | "confined" | "sandbox-scoped";
export type SigningPolicy = "platform" | "authenticode" | "store-assertion" | "apple-distribution";

export interface DistributionProfile {
  readonly target: DistributionTarget;
  readonly applicationUpdates: ApplicationUpdateOwner;
  readonly runtimeCode: RuntimeCodePolicy;
  readonly fileAccess: FileAccessPolicy;
  readonly signing: SigningPolicy;
}

export const DISTRIBUTION_TARGETS: readonly DistributionTarget[];
export const DISTRIBUTION_PROFILES: Readonly<Record<DistributionTarget, DistributionProfile>>;
export const DISTRIBUTION_TARGET: DistributionTarget;
export const DISTRIBUTION_PROFILE: DistributionProfile;
export function resolveDistributionTarget(value?: unknown): DistributionTarget;
export function distributionProfile(target?: unknown): DistributionProfile;
