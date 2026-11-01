import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { APP_ID, PRODUCT_NAME } from "../config/app-config.mjs";
import {
  DISTRIBUTION_TARGETS,
  distributionProfile,
  resolveDistributionTarget,
} from "../config/distributions.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const load = (path) => JSON.parse(readFileSync(resolve(root, path), "utf8"));
const metadata = load("store/metadata.json");
const privacy = load("store/privacy.json");
const compliance = load("store/compliance.json");
const assets = load("store/assets.json");
const distribution = resolveDistributionTarget(
  process.argv.find((value) => value.startsWith("--distribution="))?.split("=", 2)[1],
);
const profile = distributionProfile(distribution);

export function bundledOnlyRuntimeBlockers(components) {
  return components.filter(
    (component) => component.delivery === "runtime-download" && component.status !== "not-bundled",
  );
}

assert.equal(metadata.schemaVersion, 1);
assert.equal(metadata.identity.productName, PRODUCT_NAME);
assert.equal(metadata.identity.applicationId, APP_ID);
assert.match(metadata.identity.macAppStore.applicationId, /^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/);
assert.match(metadata.identity.macAppStore.appStoreConnectId, /^\d+$/);
assert.equal(metadata.identity.license, "AGPL-3.0-only");
assert(metadata.identity.publisherLegalName);
assert(metadata.listing.shortDescription.length <= 100);
assert(metadata.listing.description.length >= 100);
for (const [name, value] of Object.entries(metadata.urls)) {
  const url = new URL(value);
  assert.equal(url.protocol, "https:", `${name} must use HTTPS`);
}
for (const target of DISTRIBUTION_TARGETS.filter((value) => value !== "direct")) {
  assert(metadata.categories[target], `Missing category for ${target}`);
}

assert.equal(privacy.schemaVersion, 1);
assert.equal(privacy.policyUrl, metadata.urls.privacyPolicy);
assert.equal(privacy.defaultBehavior.analyticsEnabled, false);
assert.equal(privacy.defaultBehavior.audioUploaded, false);
assert(privacy.transmittedData.some((entry) => entry.destination === "PostHog US"));

assert.equal(compliance.schemaVersion, 1);
const allowedStatuses = new Set(compliance.statuses);
for (const component of compliance.components) {
  assert(component.id && component.delivery && component.license && component.evidence);
  assert(allowedStatuses.has(component.status), `Invalid status for ${component.id}`);
}
if (profile.runtimeCode === "bundled-only") {
  const unresolvedRuntime = bundledOnlyRuntimeBlockers(compliance.components);
  assert.equal(
    unresolvedRuntime.length,
    0,
    `Bundled-only ${distribution} has unresolved runtime components: ${unresolvedRuntime.map((item) => item.id).join(", ")}`,
  );
}

assert.equal(assets.schemaVersion, 1);
for (const path of [assets.iconSource, ...assets.screenshots.map((item) => item.source)]) {
  assert(existsSync(resolve(root, path)), `Missing store asset source: ${path}`);
}
assert(assets.screenshots.length >= 6, "At least six canonical screenshots are required");
assert(
  assets.screenshots.every((item) => item.caption?.trim()),
  "Every screenshot needs a caption",
);

console.log(`PASS: store foundation (${distribution}, updates=${profile.applicationUpdates})`);
