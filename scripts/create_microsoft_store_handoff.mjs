import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  closeSync,
  existsSync,
  openSync,
  readFileSync,
  readSync,
  readdirSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function sha256(path) {
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

export function createMicrosoftStoreHandoff({
  releaseDir,
  output,
  version,
  tag,
  repository = "debpalash/VoiceStudio",
  generatedAt = new Date().toISOString(),
}) {
  assert.match(version, /^\d+\.\d+\.\d+$/, "Version must be a release version");
  assert.equal(tag, `v${version}`, "Microsoft Store packages require the exact version tag");
  assert.match(repository, /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/, "Invalid GitHub repository");

  const packages = readdirSync(releaseDir)
    .filter((name) =>
      new RegExp(
        `^VoiceStudio-MicrosoftStore-${version.replaceAll(".", "\\.")}-win-(x64|arm64)\\.exe$`,
      ).test(name),
    )
    .sort()
    .map((file) => {
      const arch = file.match(/-win-(x64|arm64)\.exe$/)?.[1];
      assert(arch, `Cannot determine architecture for ${file}`);
      const manifestFile = `store-artifact-manifest-win32-${arch}.json`;
      const evidenceFile = `authenticode-win32-${arch}.json`;
      const updateManifestFile = `electron-microsoft-store-stable-win32-${arch}.yml`;
      const manifestPath = resolve(releaseDir, manifestFile);
      const evidencePath = resolve(releaseDir, evidenceFile);
      assert(existsSync(manifestPath), `Missing provenance manifest: ${manifestFile}`);
      assert(existsSync(evidencePath), `Missing Authenticode evidence: ${evidenceFile}`);
      const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
      assert.equal(manifest.distribution?.target, "microsoft-store");
      assert.equal(manifest.distribution?.applicationUpdates, "application");
      assert.deepEqual(manifest.signingVerification, {
        status: "valid",
        evidence: evidenceFile,
      });
      const path = resolve(releaseDir, file);
      const digest = sha256(path);
      const artifact = manifest.artifacts?.find((item) => item.file === file);
      assert(artifact, `${manifestFile} does not describe ${file}`);
      assert.equal(artifact.bytes, statSync(path).size, `${file} size differs from provenance`);
      assert.equal(artifact.sha256, digest, `${file} digest differs from provenance`);
      const updateArtifact = manifest.artifacts?.find((item) => item.file === updateManifestFile);
      assert(updateArtifact, `${manifestFile} does not describe ${updateManifestFile}`);
      const updateManifestPath = resolve(releaseDir, updateManifestFile);
      assert(existsSync(updateManifestPath), `Missing update manifest: ${updateManifestFile}`);
      assert.equal(
        updateArtifact.sha256,
        sha256(updateManifestPath),
        `${updateManifestFile} digest differs from provenance`,
      );
      return {
        architecture: arch,
        type: "exe",
        file,
        url: `https://github.com/${repository}/releases/download/${tag}/${file}`,
        sha256: digest,
        bytes: statSync(path).size,
        installerParameters: "/S",
        provenance: manifestFile,
        authenticodeEvidence: evidenceFile,
        updateManifest: updateManifestFile,
      };
    });

  assert(
    packages.some((item) => item.architecture === "x64"),
    "An x64 Store package is required",
  );
  const metadata = JSON.parse(readFileSync(resolve(root, "store/metadata.json"), "utf8"));
  const assets = JSON.parse(readFileSync(resolve(root, "store/assets.json"), "utf8"));
  const handoff = {
    schemaVersion: 1,
    generatedAt,
    lifecycle: "validated",
    ownerApprovalRequiredForSubmission: true,
    identity: metadata.identity,
    listing: metadata.listing,
    category: metadata.categories["microsoft-store"],
    privacyPolicyUrl: metadata.urls.privacyPolicy,
    supportUrl: metadata.urls.support,
    applicableLicenseTermsUrl: `${metadata.urls.source}/blob/${tag}/LICENSE`,
    supportedLanguages: [metadata.identity.defaultLocale.toLowerCase()],
    assets: {
      boxArt: assets.iconSource,
      screenshots: assets.screenshots,
    },
    packages,
  };
  writeFileSync(output, `${JSON.stringify(handoff, null, 2)}\n`);
  return handoff;
}

function option(name, fallback) {
  const prefix = `--${name}=`;
  return process.argv.find((value) => value.startsWith(prefix))?.slice(prefix.length) ?? fallback;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { version } = JSON.parse(readFileSync(resolve(root, "package.json"), "utf8"));
  const releaseDir = resolve(root, option("release-dir", "electron/release"));
  const output = resolve(
    root,
    option("output", "electron/release/microsoft-store-submission.json"),
  );
  createMicrosoftStoreHandoff({
    releaseDir,
    output,
    version,
    tag: option("tag", `v${version}`),
    repository: option("repository", process.env.GITHUB_REPOSITORY || "debpalash/VoiceStudio"),
  });
  console.log(`Wrote ${basename(output)}`);
}
