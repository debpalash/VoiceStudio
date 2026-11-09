import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import {
  closeSync,
  openSync,
  readFileSync,
  readSync,
  readdirSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { distributionProfile, resolveDistributionTarget } from "../config/distributions.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function sha256File(path) {
  const hash = createHash("sha256");
  const descriptor = openSync(path, "r");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    for (;;) {
      const length = readSync(descriptor, buffer, 0, buffer.length, null);
      if (!length) break;
      hash.update(buffer.subarray(0, length));
    }
  } finally {
    closeSync(descriptor);
  }
  return hash.digest("hex");
}

function releaseArtifact(name, distribution) {
  // Direct and Snap packages are `VoiceStudio-<version>-…`; store builds carry a label.
  const installer =
    {
      "microsoft-store": /^VoiceStudio-MicrosoftStore-/,
      "mac-app-store": /^VoiceStudio-MacAppStore-/,
    }[distribution] ?? /^VoiceStudio-\d/;
  const updatePrefix =
    distribution === "microsoft-store"
      ? "electron-microsoft-store-stable-"
      : distribution === "direct"
        ? "electron-stable-"
        : null;
  return (
    installer.test(name) ||
    (updatePrefix !== null && name.startsWith(updatePrefix) && name.endsWith(".yml"))
  );
}

export function createArtifactManifest({
  releaseDir,
  output,
  distribution,
  platform,
  arch,
  version,
  commit,
  generatedAt = new Date().toISOString(),
  signingVerification = { status: "not-run" },
}) {
  const target = resolveDistributionTarget(distribution);
  const profile = distributionProfile(target);
  const artifacts = readdirSync(releaseDir, { withFileTypes: true })
    .filter((entry) => entry.isFile() && releaseArtifact(entry.name, target))
    .map((entry) => {
      const path = resolve(releaseDir, entry.name);
      return {
        file: entry.name,
        bytes: statSync(path).size,
        sha256: sha256File(path),
      };
    })
    .sort((left, right) => (left.file < right.file ? -1 : left.file > right.file ? 1 : 0));
  if (!artifacts.length) throw new Error(`No Electron release artifacts found in ${releaseDir}`);

  const compliancePath = resolve(root, "store/compliance.json");
  const manifest = {
    schemaVersion: 1,
    generatedAt,
    source: { version, commit },
    target: { platform, arch },
    distribution: {
      target,
      applicationUpdates: profile.applicationUpdates,
      runtimeCode: profile.runtimeCode,
      fileAccess: profile.fileAccess,
      signingPolicy: profile.signing,
    },
    complianceManifestSha256: sha256File(compliancePath),
    signingVerification,
    artifacts,
  };
  writeFileSync(output, `${JSON.stringify(manifest, null, 2)}\n`);
  return manifest;
}

export function resolveSourceCommit(explicitCommit, cwd = root) {
  return (
    explicitCommit || execFileSync("git", ["rev-parse", "HEAD"], { cwd, encoding: "utf8" }).trim()
  );
}

function option(name, fallback) {
  const prefix = `--${name}=`;
  return process.argv.find((value) => value.startsWith(prefix))?.slice(prefix.length) ?? fallback;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const packageJson = JSON.parse(readFileSync(resolve(root, "package.json"), "utf8"));
  const releaseDir = resolve(root, option("release-dir", "electron/release"));
  const platform = option("platform", process.platform);
  const arch = option("arch", process.arch);
  const output = resolve(
    root,
    option("output", `electron/release/store-artifact-manifest-${platform}-${arch}.json`),
  );
  const commit = resolveSourceCommit(option("commit", ""));
  createArtifactManifest({
    releaseDir,
    output,
    distribution: option("distribution", process.env.VOICESTUDIO_DISTRIBUTION),
    platform,
    arch,
    version: packageJson.version,
    commit,
    signingVerification: option("signing-evidence", "")
      ? { status: "valid", evidence: option("signing-evidence", "") }
      : { status: "not-run" },
  });
  console.log(`Wrote ${basename(output)}`);
}
