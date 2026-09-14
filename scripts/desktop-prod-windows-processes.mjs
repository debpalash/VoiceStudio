#!/usr/bin/env bun
// Safely stop Windows processes owned by this checkout's debug build.
//
// The Windows counterpart of scripts/desktop_prod_processes.py. `kill_running_instances`
// in desktop-prod.sh reaches for `pgrep`, `lsof` and POSIX `kill`; Git Bash ships
// none of them for native Win32 processes, and every lookup is `|| true`-guarded,
// so the whole step degrades to a silent no-op on Windows. Two consequences, both
// the exact failures the kill exists to prevent:
//
//   1. The stale omnivoice-studio.exe keeps a lock on its own image, so the next
//      `cargo build` dies with "failed to remove file ... Access is denied.
//      (os error 5)" after a full compile.
//   2. On a wipe run the app survives the data deletion, leaving the zombie
//      backend the script's own header warns about.
//
// Ownership is proved by executable path only — strictly under this checkout's
// target/debug — never by bundle id, so an installed VoiceStudio is left alone
// (the rule tests/test_desktop_prod_scripts.py::test_kill_is_scoped_to_this_
// checkouts_build fixes for the Unix path).
//
// Termination is identity-bound: `taskkill`/Terminate address a REUSABLE pid, so
// a pid recycled between listing and kill would take an unrelated process down.
// stopWindowsProcess() re-checks the creation timestamp inside the same
// PowerShell pass that terminates, which is why it is reused here rather than
// reimplemented.

import { spawnSync } from "node:child_process";
import { stopWindowsProcess } from "./clear-dev-ports.mjs";

/** Process image name of the Tauri debug build (both launch shapes use it). */
export const APP_EXE = "omnivoice-studio.exe";

/**
 * Convert an MSYS/Git Bash path to its Win32 form.
 *
 * desktop-prod.sh runs under Git Bash, so $TAURI_BUILD_ROOT arrives as
 * `/c/checkout/...` while Win32_Process reports `C:\checkout\...`. Comparing the
 * two without this returns no owners and reinstates the silent no-op. Already
 * Win32-shaped input passes through unchanged, so the helper stays callable from
 * a native shell too.
 */
export function toWindowsPath(value) {
  const raw = String(value || "");
  if (!raw) return "";
  const drive = raw.match(/^\/([A-Za-z])(\/.*)?$/);
  const win = (drive ? `${drive[1].toUpperCase()}:${drive[2] || "\\"}` : raw).replaceAll("/", "\\");
  // Trailing separators are stripped so prefix comparison is stable, except on
  // a bare drive root, where the separator is part of the path (`C:` is the
  // drive's *current directory*, which is not `C:\`).
  return /^[A-Za-z]:\\$/.test(win) ? win : win.replace(/\\+$/, "");
}

/**
 * True when `executable` sits strictly inside `buildRoot`.
 *
 * Prefix matching alone would accept a sibling directory whose name merely
 * starts with the root (`target\debug-old\`), so the separator is required.
 * Case-insensitive because Windows paths are.
 */
export function ownedByBuild(executable, buildRoot) {
  const exe = toWindowsPath(executable).toLowerCase();
  const root = toWindowsPath(buildRoot).toLowerCase();
  if (!exe || !root) return false;
  return exe.startsWith(`${root}\\`);
}

/** Parse the PowerShell payload, which is an object for one match, an array for many. */
export function parseProcessList(stdout) {
  const text = String(stdout || "").trim();
  if (!text) return [];
  const parsed = JSON.parse(text);
  return (Array.isArray(parsed) ? parsed : [parsed]).filter(Boolean);
}

/**
 * List this checkout's running debug processes as {pid, executable, identity}.
 *
 * `Started` is formatted round-trip ('o') so the identity string is byte-stable
 * and re-comparable by stopWindowsProcess().
 */
export function listOwnedProcesses(buildRoot, run = spawnSync) {
  const script = [
    `$procs = Get-CimInstance Win32_Process -Filter "Name='${APP_EXE}'"`,
    "if ($null -ne $procs) {",
    "  $procs | Select-Object ProcessId,ExecutablePath,@{n='Started';e={$_.CreationDate.ToUniversalTime().ToString('o')}} | ConvertTo-Json -Compress",
    "}",
  ].join("; ");
  const result = run(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-Command", script],
    { encoding: "utf8" },
  );
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error("Could not enumerate VoiceStudio processes");
  return parseProcessList(result.stdout)
    .filter((p) => ownedByBuild(p.ExecutablePath, buildRoot))
    .map((p) => ({
      pid: Number(p.ProcessId),
      executable: p.ExecutablePath,
      identity: `windows:${p.Started}`,
    }));
}

/** True once no owned process remains — i.e. every pid really exited. */
function stillRunning(buildRoot, run) {
  return listOwnedProcesses(buildRoot, run).map((p) => p.pid);
}

/**
 * Terminate every owned process and wait for it to actually exit.
 *
 * Returns the pids that were stopped. Throws when any survives: the caller is
 * about to delete the app data directory, and a survivor becomes the zombie
 * backend that answers /health from memory while every route 500s off deleted
 * files. Refusing is what desktop_prod_processes.py does on Linux for the same
 * reason, so the two platforms fail the same way.
 */
export function stopOwnedProcesses(
  buildRoot,
  { run = spawnSync, stop = stopWindowsProcess, waitMs = 5000, sleep = defaultSleep } = {},
) {
  const owned = listOwnedProcesses(buildRoot, run);
  if (owned.length === 0) return [];

  for (const { pid, identity } of owned) stop(pid, false, identity, run);

  const deadline = Date.now() + waitMs;
  let remaining = stillRunning(buildRoot, run);
  while (remaining.length > 0 && Date.now() < deadline) {
    sleep(100);
    remaining = stillRunning(buildRoot, run);
  }
  if (remaining.length > 0) {
    throw new Error(
      `VoiceStudio processes did not exit (${remaining.join(", ")}); refusing to reset app data`,
    );
  }
  return owned.map((p) => p.pid);
}

/** Block without pulling in a timer: the caller is a short-lived CLI. */
function defaultSleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function main(argv) {
  const buildRoot = argv[2];
  if (!buildRoot) {
    console.error("usage: desktop-prod-windows-processes.mjs <build_root>");
    return 2;
  }
  for (const pid of stopOwnedProcesses(buildRoot)) console.log(pid);
  return 0;
}

if (import.meta.main) process.exit(main(process.argv));
