// ──────────────────────────────────────────────────────────────────────────
// desktop-toolchain-path.mjs — heal a stale PATH for the desktop launchers.
//
// Why this exists: every desktop launcher (`bun desktop`, `bun desktop-prod`,
// `bun desktop-fresh`) shells out to `cargo` (via the Tauri CLI) and to `uv`.
// A terminal opened *before* rustup / the uv installer ran keeps a PATH
// snapshot that lacks `~/.cargo/bin` / `~/.local/bin`, so the launcher dies with
//     failed to run 'cargo metadata' command … program not found
// even though the tool IS installed and IS on the persisted user PATH — a
// brand-new terminal would find it. `desktop-dev.mjs` healed this for `bun
// desktop` alone; `bun desktop-prod` then failed the exact same way on the
// next command the user typed. Every launcher now shares this one helper.
//
// Pure w.r.t. process state: it never mutates the env it is given (mutating
// process.env doesn't reliably propagate to children under Bun) and every
// probe is injectable so the behavior is unit-tested without a toolchain.
// ──────────────────────────────────────────────────────────────────────────
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { homedir } from "node:os";
import process from "node:process";

/** The env's PATH key — Windows uses "Path", others "PATH"; match case-insensitively. */
export function pathKeyOf(env) {
  return Object.keys(env).find((k) => k.toLowerCase() === "path") ?? "PATH";
}

/** Tools the launchers need, with the per-user bin dir their installers use
 *  on every platform (rustup → ~/.cargo/bin; astral's uv → ~/.local/bin). */
export const TOOLCHAIN_TOOLS = [
  { tool: "cargo", dirParts: [".cargo", "bin"], installer: "rustup" },
  { tool: "uv", dirParts: [".local", "bin"], installer: "the uv installer" },
];

/** Is `tool` resolvable via the given env's PATH? Uses a child that searches
 *  its own PATH (`cmd`/`sh`), which mirrors how the Tauri CLI's Rust resolves
 *  `cargo` downstream — unlike Bun's own launcher resolution, which snapshots
 *  PATH and would give a false negative after we heal it. */
export function toolResolvable(tool, env, platform = process.platform) {
  const probe =
    platform === "win32"
      ? spawnSync("cmd", ["/c", `${tool} --version`], { env, stdio: "ignore" })
      : spawnSync("sh", ["-c", `command -v ${tool}`], { env, stdio: "ignore" });
  return probe.status === 0;
}

/**
 * Return a *copy* of `env` whose PATH also reaches every installed-but-invisible
 * toolchain binary, plus what was added and what is missing outright.
 *
 * @returns {{ env: Record<string,string|undefined>, added: {tool:string, dir:string}[], missing: string[] }}
 */
export function healToolchainPath(
  env,
  {
    platform = process.platform,
    home = homedir(),
    exists = existsSync,
    resolvable = (tool, e) => toolResolvable(tool, e, platform),
    tools = TOOLCHAIN_TOOLS,
  } = {},
) {
  // Path rules follow the *emulated* platform, not the host's, so the unit
  // tests are deterministic on every CI runner.
  const P = platform === "win32" ? path.win32 : path.posix;
  const healed = { ...env };
  const key = pathKeyOf(healed);
  const added = [];
  const missing = [];
  for (const { tool, dirParts } of tools) {
    if (resolvable(tool, healed)) continue;
    const dir = P.join(home, ...dirParts);
    const exe = P.join(dir, platform === "win32" ? `${tool}.exe` : tool);
    if (exists(exe)) {
      healed[key] = dir + P.delimiter + (healed[key] ?? "");
      added.push({ tool, dir });
    } else {
      missing.push(tool);
    }
  }
  return { env: healed, added, missing };
}

/** One line per healed tool, for the launcher to print so the fix is visible
 *  and the permanent remedy (a new terminal) is named. */
export function healedPathNotes(added, tag) {
  return added.map(
    ({ tool, dir }) =>
      `[${tag}] added '${dir}' to PATH for this run - ${tool} is installed but wasn't visible to ` +
      `this terminal (a stale PATH from before it was installed). Open a new terminal to make it permanent.`,
  );
}

/** The actionable failure text when Rust is genuinely absent. `command` is
 *  what needed it (e.g. "`tauri dev`", "`bun desktop-prod`"). */
export function cargoMissingMessage(command) {
  return [
    "",
    `❌ ${command} needs Rust/cargo, and none was found.`,
    "",
    "   Install the Rust toolchain, then reopen your terminal:",
    "     Windows:      winget install Rustlang.Rustup",
    "     macOS/Linux:  https://rustup.rs",
    "",
    "   Or download a prebuilt installer from the Releases page (no toolchain needed).",
    "",
  ].join("\n");
}
