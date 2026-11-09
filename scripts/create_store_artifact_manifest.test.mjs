import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import {
  createArtifactManifest,
  resolveSourceCommit,
} from "./create_store_artifact_manifest.mjs";

test("records only Mac App Store artifacts and their signing evidence", () => {
  const directory = mkdtempSync(join(tmpdir(), "voicestudio-mas-manifest-"));
  try {
    const pkg = "VoiceStudio-MacAppStore-0.5.6-mac-arm64.pkg";
    writeFileSync(join(directory, pkg), "signed package");
    writeFileSync(join(directory, "VoiceStudio-0.5.6-mac-arm64.dmg"), "direct package");
    writeFileSync(join(directory, "apple-signing-arm64.json"), "{}");
    const output = join(directory, "manifest.json");
    const manifest = createArtifactManifest({
      releaseDir: directory,
      output,
      distribution: "mac-app-store",
      platform: "darwin",
      arch: "arm64",
      version: "0.5.6",
      commit: "0123456789abcdef",
      generatedAt: "2026-10-14T00:00:00.000Z",
      signingVerification: { status: "valid", evidence: "apple-signing-arm64.json" },
    });
    assert.deepEqual(
      manifest.artifacts.map((artifact) => artifact.file),
      [pkg],
    );
    assert.deepEqual(manifest.signingVerification, {
      status: "valid",
      evidence: "apple-signing-arm64.json",
    });
    assert.equal(JSON.parse(readFileSync(output, "utf8")).distribution.target, "mac-app-store");
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("records the checked-out commit unless an explicit commit is provided", () => {
  const head = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
  assert.equal(resolveSourceCommit(""), head);
  assert.equal(resolveSourceCommit("f".repeat(40)), "f".repeat(40));
});
