import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { assertSupportedNode } from "../electron/scripts/node-version.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export function needsLocalBackendSetup({
  env = process.env,
  platform = process.platform,
  arch = process.arch,
} = {}) {
  if (String(env.OMNIVOICE_BACKEND_CMD ?? "").trim()) return false;
  if (env.VOICESTUDIO_SKIP_BACKEND === "1") return false;
  return platform !== "darwin" || arch !== "x64";
}

/** Validate Node before installing dependencies and preparing a local backend. */
export function prepareDev({
  nodeVersion = process.versions.node,
  env = process.env,
  platform = process.platform,
  arch = process.arch,
  run = spawnSync,
  log = console.log,
} = {}) {
  assertSupportedNode(nodeVersion);
  const commands = [["install"]];
  if (needsLocalBackendSetup({ env, platform, arch })) {
    commands.push(["run", "setup:api"]);
  } else {
    log("[dev] Skipping local API setup; Electron will use the configured external backend.");
  }

  for (const args of commands) {
    const result = run("bun", args, { cwd: root, env, stdio: "inherit" });
    if (result.error) throw result.error;
    if (result.status !== 0) return result.status ?? 1;
  }
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    process.exitCode = prepareDev();
  } catch (error) {
    console.error(`[dev] ${error.message}`);
    process.exitCode = 1;
  }
}
