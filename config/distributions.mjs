const profiles = {
  direct: {
    applicationUpdates: "application",
    runtimeCode: "user-initiated-download",
    fileAccess: "user-selected",
    signing: "platform",
  },
  "microsoft-store": {
    // The first Microsoft Store package is the existing unpackaged NSIS EXE.
    // Microsoft explicitly leaves updates for existing EXE/MSI users to the
    // application; change this only if the package format changes to MSIX.
    applicationUpdates: "application",
    runtimeCode: "user-initiated-download",
    fileAccess: "user-selected",
    signing: "authenticode",
  },
  "snap-store": {
    applicationUpdates: "store",
    runtimeCode: "user-initiated-download",
    fileAccess: "confined",
    signing: "store-assertion",
  },
  "mac-app-store": {
    applicationUpdates: "store",
    runtimeCode: "bundled-only",
    fileAccess: "sandbox-scoped",
    signing: "apple-distribution",
  },
};

export const DISTRIBUTION_TARGETS = Object.freeze(Object.keys(profiles));
export const DISTRIBUTION_PROFILES = Object.freeze(
  Object.fromEntries(
    Object.entries(profiles).map(([target, profile]) => [
      target,
      Object.freeze({ target, ...profile }),
    ]),
  ),
);

export function resolveDistributionTarget(value = process.env.VOICESTUDIO_DISTRIBUTION) {
  const target = typeof value === "string" && value.trim() ? value.trim() : "direct";
  if (!Object.hasOwn(DISTRIBUTION_PROFILES, target)) {
    throw new TypeError(
      `Unsupported VoiceStudio distribution: ${target}. Expected one of: ${DISTRIBUTION_TARGETS.join(", ")}`,
    );
  }
  return target;
}

export function distributionProfile(target = resolveDistributionTarget()) {
  return DISTRIBUTION_PROFILES[resolveDistributionTarget(target)];
}

export const DISTRIBUTION_TARGET = resolveDistributionTarget();
export const DISTRIBUTION_PROFILE = distributionProfile(DISTRIBUTION_TARGET);
