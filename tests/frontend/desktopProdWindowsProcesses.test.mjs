// `desktop-prod` must actually stop a running app on Windows.
//
// kill_running_instances reaches for pgrep/lsof/POSIX kill, none of which see a
// native Win32 process from Git Bash, and each lookup is `|| true`-guarded — so
// the step silently did nothing on Windows. The stale omnivoice-studio.exe then
// held a lock on its own image and the next `cargo build` failed with
// "failed to remove file ... Access is denied. (os error 5)" after a full compile.
//
// These run on every platform: the PowerShell boundary is injected, so the
// ownership, pid-identity and survivor rules are checked on Linux CI too.
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  listOwnedProcesses,
  ownedByBuild,
  parseProcessList,
  stopOwnedProcesses,
  toWindowsPath,
} from "../../scripts/desktop-prod-windows-processes.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const BUILD_ROOT = "/c/checkout/frontend/src-tauri/target/debug";
const WIN_BUILD_ROOT = "C:\\checkout\\frontend\\src-tauri\\target\\debug";

/** A PowerShell stub returning `procs` for the list pass. */
function listStub(batches) {
  const queue = [...batches];
  return () => ({
    status: 0,
    stdout: (() => {
      const next = queue.length > 1 ? queue.shift() : queue[0];
      return next.length === 0 ? "" : JSON.stringify(next.length === 1 ? next[0] : next);
    })(),
  });
}

const proc = (ProcessId, ExecutablePath, Started = "2026-01-01T00:00:00.0000000Z") => ({
  ProcessId,
  ExecutablePath,
  Started,
});

test("MSYS build roots are converted to their Win32 form", () => {
  // Git Bash hands the script /c/... while Win32_Process reports C:\... —
  // comparing the two unconverted matches nothing and restores the no-op.
  assert.equal(toWindowsPath(BUILD_ROOT), WIN_BUILD_ROOT);
  assert.equal(toWindowsPath("/d/x"), "D:\\x");
  assert.equal(toWindowsPath(WIN_BUILD_ROOT), WIN_BUILD_ROOT);
  assert.equal(toWindowsPath("/c/"), "C:\\");
  assert.equal(toWindowsPath(""), "");
});

test("ownership is decided by build path, so an installed copy is spared", () => {
  assert.equal(ownedByBuild(`${WIN_BUILD_ROOT}\\omnivoice-studio.exe`, BUILD_ROOT), true);
  // Case-insensitive: Windows paths are.
  assert.equal(ownedByBuild(`${WIN_BUILD_ROOT.toLowerCase()}\\OMNIVOICE-STUDIO.EXE`, BUILD_ROOT), true);
  // The installed app is somebody's live session — never ours to kill.
  assert.equal(
    ownedByBuild("C:\\Program Files\\VoiceStudio\\omnivoice-studio.exe", BUILD_ROOT),
    false,
  );
  // A sibling whose name merely starts with the root is not inside it.
  assert.equal(ownedByBuild(`${WIN_BUILD_ROOT}-old\\omnivoice-studio.exe`, BUILD_ROOT), false);
  // The root itself is a directory, not a process image.
  assert.equal(ownedByBuild(WIN_BUILD_ROOT, BUILD_ROOT), false);
  assert.equal(ownedByBuild("", BUILD_ROOT), false);
});

test("ConvertTo-Json emits an object for one match and an array for many", () => {
  assert.equal(parseProcessList("").length, 0);
  assert.equal(parseProcessList(JSON.stringify(proc(1, "x"))).length, 1);
  assert.equal(parseProcessList(JSON.stringify([proc(1, "x"), proc(2, "y")])).length, 2);
});

test("only this checkout's processes are listed, with a round-trip identity", () => {
  const run = listStub([
    [
      proc(100, `${WIN_BUILD_ROOT}\\omnivoice-studio.exe`, "2026-01-01T00:00:00.0000000Z"),
      proc(200, "C:\\Program Files\\VoiceStudio\\omnivoice-studio.exe"),
    ],
  ]);
  const owned = listOwnedProcesses(BUILD_ROOT, run);
  assert.deepEqual(
    owned.map((p) => p.pid),
    [100],
  );
  assert.equal(owned[0].identity, "windows:2026-01-01T00:00:00.0000000Z");
});

test("each kill is bound to the process instance, not the reusable pid", () => {
  const stopped = [];
  const run = listStub([[proc(100, `${WIN_BUILD_ROOT}\\omnivoice-studio.exe`)], []]);
  const pids = stopOwnedProcesses(BUILD_ROOT, {
    run,
    stop: (pid, _force, identity) => stopped.push([pid, identity]),
    sleep: () => {},
  });
  assert.deepEqual(pids, [100]);
  assert.deepEqual(stopped, [[100, "windows:2026-01-01T00:00:00.0000000Z"]]);
});

test("a survivor fails loudly instead of letting the wipe proceed", () => {
  // The caller deletes the app data dir next. A survivor becomes the zombie
  // backend that answers /health from memory while every route 500s off
  // deleted files — which is why Linux refuses here too.
  const alive = [proc(100, `${WIN_BUILD_ROOT}\\omnivoice-studio.exe`)];
  assert.throws(
    () =>
      stopOwnedProcesses(BUILD_ROOT, {
        run: listStub([alive]),
        stop: () => {},
        waitMs: 10,
        sleep: () => {},
      }),
    /did not exit.*refusing to reset app data/s,
  );
});

test("nothing running is not an error", () => {
  assert.deepEqual(stopOwnedProcesses(BUILD_ROOT, { run: listStub([[]]), sleep: () => {} }), []);
});

test("desktop-prod.sh routes Windows to the helper before the POSIX lookups", () => {
  // Mechanical on purpose: the reason pgrep cannot be used here is not visible
  // from the pgrep line itself, so the wiring belongs in a test.
  const sh = readFileSync(resolve(ROOT, "scripts", "desktop-prod.sh"), "utf8");
  const start = sh.indexOf("kill_running_instances() {");
  assert.ok(start !== -1, "kill_running_instances is gone");
  const body = sh.slice(start, sh.indexOf("\n}", start));
  assert.match(body, /desktop-prod-windows-processes\.mjs/);
  const branch = body.indexOf('if [ "$PLATFORM" = "windows" ]');
  const pgrep = body.indexOf("pgrep -f");
  assert.ok(branch !== -1, "no Windows branch in kill_running_instances");
  assert.ok(
    branch < pgrep,
    "the Windows branch must precede the pgrep path it cannot use",
  );
});
