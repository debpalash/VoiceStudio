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

import { PRODUCT_NAME } from "../config/app-config.mjs";
import { verifySnapRuntimePayload } from "../electron/snap-template-payload.mjs";
import { verifySnapLauncher } from "../electron/snap-launcher.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REQUIRED_PLUGS = [
  "browser-support",
  "desktop",
  "desktop-legacy",
  "home",
  "x11",
  "wayland",
  "unity7",
  "network",
  "network-bind",
  "gsettings",
  "audio-playback",
  "audio-record",
  "pulseaudio",
  "opengl",
];

function field(source, name) {
  return source.match(new RegExp(`^${name}: (.+)$`, "m"))?.[1];
}

export function verifySnapMetadata(source, metadata) {
  const name = field(source, "name");
  const version = field(source, "version");
  const confinement = field(source, "confinement");
  if (name !== "voicestudio") throw new Error(`Unexpected Snap name: ${name}`);
  if (version !== metadata.version) throw new Error(`Unexpected Snap version: ${version}`);
  if (field(source, "title") !== PRODUCT_NAME) throw new Error("Unexpected Snap title");
  if (field(source, "summary") !== metadata.listing.shortDescription) {
    throw new Error("Snap summary drifted from store/metadata.json");
  }
  if (field(source, "description") !== metadata.listing.description) {
    throw new Error("Snap description drifted from store/metadata.json");
  }
  if (field(source, "base") !== "core22") throw new Error("Unexpected Snap base");
  if (confinement !== "strict") throw new Error(`Unexpected Snap confinement: ${confinement}`);
  const browserSupport = source.match(/^  browser-support:\n((?:    .*(?:\n|$))+)/m)?.[1] ?? "";
  if (!/^    interface: browser-support$/m.test(browserSupport) ||
      !/^    allow-sandbox: false$/m.test(browserSupport)) {
    throw new Error("Snap browser-support must explicitly disable the privileged Chromium sandbox");
  }
  for (const plug of REQUIRED_PLUGS) {
    if (!source.includes(`      - ${plug}\n`)) throw new Error(`Missing Snap interface: ${plug}`);
  }
  if (source.includes("      - removable-media\n")) {
    throw new Error("Snap must use the user-selected file portal, not removable-media");
  }
  return { name, version, confinement, base: "core22", plugs: REQUIRED_PLUGS };
}

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

function option(name, fallback) {
  const prefix = `--${name}=`;
  return process.argv.find((value) => value.startsWith(prefix))?.slice(prefix.length) ?? fallback;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const releaseDir = resolve(root, option("release-dir", "electron/release"));
  const candidates = readdirSync(releaseDir)
    .filter((name) => /^VoiceStudio-\d.*-linux-x64\.snap$/.test(name))
    .sort();
  if (candidates.length !== 1) {
    throw new Error(`Expected one x64 Snap artifact, found: ${candidates.join(", ")}`);
  }
  const artifact = resolve(releaseDir, candidates[0]);
  const snapYaml = execFileSync("unsquashfs", ["-cat", artifact, "meta/snap.yaml"], {
    encoding: "utf8",
  });
  const listing = execFileSync("unsquashfs", ["-ll", artifact], { encoding: "utf8" });
  verifySnapRuntimePayload(listing);
  const launcher = execFileSync("unsquashfs", ["-cat", artifact, "command.sh"], { encoding: "utf8" });
  verifySnapLauncher(launcher);
  for (const path of ["resources/native/voicestudio-desktop-bridge", "resources/tools/uv"]) {
    if (!listing.includes(path)) throw new Error(`Snap payload is missing ${path}`);
  }
  const packageJson = JSON.parse(readFileSync(resolve(root, "package.json"), "utf8"));
  const metadata = JSON.parse(readFileSync(resolve(root, "store/metadata.json"), "utf8"));
  metadata.version = packageJson.version;
  const verified = verifySnapMetadata(snapYaml, metadata);
  const evidence = {
    schemaVersion: 1,
    artifact: basename(artifact),
    bytes: statSync(artifact).size,
    sha256: sha256(artifact),
    ...verified,
    assertion: "not-submitted",
  };
  const output = resolve(
    root,
    option(
      "output",
      `electron/release/VoiceStudio-${packageJson.version}-snap-verification.json`,
    ),
  );
  writeFileSync(output, `${JSON.stringify(evidence, null, 2)}\n`);
  console.log(`Verified ${basename(artifact)} and wrote ${basename(output)}`);
}
