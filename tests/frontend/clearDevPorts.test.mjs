import assert from "node:assert/strict";
import test from "node:test";

import {
  APP_BUNDLE_ID,
  belongsToCheckout,
  clearDevPortsWith,
  isUninspectableProcessError,
  parseSsListeners,
  parseWindowsListeners,
  stopUnixProcess,
  stopWindowsProcess,
} from "../../scripts/clear-dev-ports.mjs";

test("parses only requested Windows TCP listeners", () => {
  const output = [
    "  TCP    0.0.0.0:3900    0.0.0.0:0    LISTENING    1234",
    "  TCP    [::]:3901       [::]:0       LISTENING    5678",
    "  TCP    0.0.0.0:5173    0.0.0.0:0    LISTENING    9999",
  ].join("\r\n");
  assert.deepEqual(parseWindowsListeners(output, [3900, 3901]), [1234, 5678]);
});

test("parses only requested Linux listeners and deduplicates pids", () => {
  const output = [
    'LISTEN 0 512 *:3900 *:* users:(("bun",pid=1234,fd=11))',
    'LISTEN 0 512 127.0.0.1:3901 0.0.0.0:* users:(("bun",pid=1234,fd=12))',
    'LISTEN 0 512 *:5173 *:* users:(("bun",pid=9999,fd=8))',
  ].join("\n");
  assert.deepEqual(parseSsListeners(output, [3900, 3901]), [1234]);
});

test("command ownership requires a checkout path boundary", () => {
  const root = "/work/VoiceStudio";
  assert.equal(belongsToCheckout("", `bun ${root}/scripts/dev.mjs`, "", false, root), true);
  assert.equal(belongsToCheckout("", `bun '${root}'`, "", false, root), true);
  assert.equal(belongsToCheckout("", `bun --cwd=${root}/frontend`, "", false, root), true);
  assert.equal(belongsToCheckout("", `bun ${root}-old/scripts/dev.mjs`, "", false, root), false);
  assert.equal(belongsToCheckout("", `bun ${root}2/scripts/dev.mjs`, "", false, root), false);
  assert.equal(belongsToCheckout("", `bun /tmp${root}/scripts/dev.mjs`, "", false, root), false);
  assert.equal(belongsToCheckout("", "bun C:/repo/scripts/dev.mjs", "", true, "C:\\repo"), true);
});

test("permission and exit races make a process uninspectable", () => {
  for (const code of ["ENOENT", "ESRCH", "EACCES", "EPERM"]) {
    assert.equal(isUninspectableProcessError({ code }), true);
  }
  assert.equal(isUninspectableProcessError({ code: "EIO" }), false);
});

test("an already-exited Unix process counts as stopped", () => {
  const missing = Object.assign(new Error("gone"), { code: "ESRCH" });
  assert.doesNotThrow(() =>
    stopUnixProcess(1234, false, () => {
      throw missing;
    }),
  );
  assert.throws(
    () =>
      stopUnixProcess(1234, true, () => {
        throw Object.assign(new Error("denied"), { code: "EPERM" });
      }),
    /denied/,
  );
});

test("refuses an unrelated listener without signalling it", async () => {
  const signals = [];
  const ops = {
    listeners: async () => [1234],
    inspect: async () => ({ identity: "start-a", owned: false }),
    stop: async (...args) => signals.push(args),
    sleep: async () => {},
  };
  await assert.rejects(clearDevPortsWith([3900], ops), /Refusing to stop unrelated process 1234/);
  assert.deepEqual(signals, []);
});

test("refuses Windows auto-stop when termination cannot bind to the inspected process", async () => {
  const signals = [];
  const ops = {
    canStop: false,
    listeners: async () => [1234],
    inspect: async () => ({ identity: "windows:start-a", owned: true }),
    stop: async (...args) => signals.push(args),
    sleep: async () => {},
  };
  await assert.rejects(clearDevPortsWith([3900], ops), /stop it in Task Manager and retry/);
  assert.deepEqual(signals, []);
});

test("never force-kills a recycled pid", async () => {
  const signals = [];
  let inspections = 0;
  let discovery = 0;
  const ops = {
    listeners: async () => (discovery++ === 0 ? [1234] : []),
    inspect: async () => {
      inspections += 1;
      if (inspections <= 3) return { identity: "start-a", owned: true };
      return { identity: "start-b", owned: false };
    },
    stop: async (pid, force) => signals.push([pid, force]),
    sleep: async () => {},
  };
  await clearDevPortsWith([3900], ops);
  assert.deepEqual(signals, [[1234, false]]);
});

test("escalates only while ownership and identity remain stable", async () => {
  const signals = [];
  let discovery = 0;
  const ops = {
    listeners: async () => (discovery++ === 0 ? [1234] : []),
    inspect: async () => ({ identity: "start-a", owned: true }),
    stop: async (pid, force) => signals.push([pid, force]),
    sleep: async () => {},
  };
  await clearDevPortsWith([3900], ops);
  assert.deepEqual(signals, [
    [1234, false],
    [1234, true],
  ]);
});

test("does not force-kill when the platform cannot prove process identity precisely", async () => {
  const signals = [];
  let discovery = 0;
  const ops = {
    canForce: false,
    listeners: async () => (discovery++ === 0 ? [1234] : []),
    inspect: async () => ({ identity: "mac:start-second", owned: true }),
    stop: async (pid, force) => signals.push([pid, force]),
    sleep: async () => {},
  };
  await clearDevPortsWith([3900], ops);
  assert.deepEqual(signals, [[1234, false]]);
});

test("waits for the listener to disappear after force stop", async () => {
  const signals = [];
  let discovery = 0;
  let sleeps = 0;
  const ops = {
    listeners: async () => (++discovery < 4 ? [1234] : []),
    inspect: async () => ({ identity: "start-a", owned: true }),
    stop: async (pid, force) => signals.push([pid, force]),
    sleep: async () => {
      sleeps += 1;
    },
  };
  await clearDevPortsWith([3900], ops);
  assert.deepEqual(signals, [
    [1234, false],
    [1234, true],
  ]);
  assert.equal(sleeps, 12);
});

// Windows auto-stop is allowed again, but only bound to the process INSTANCE:
// a pid recycled between inspect and kill must never be terminated.
test("windows stop re-checks the process identity before terminating", () => {
  const calls = [];
  const run = (_exe, args) => {
    calls.push(args.at(-1));
    return { status: 0 };
  };
  stopWindowsProcess(4242, false, "windows:2026-09-08T21:25:58.5000000Z", run);
  assert.equal(calls.length, 1);
  assert.match(calls[0], /ProcessId = 4242/);
  assert.match(calls[0], /2026-09-08T21:25:58\.5000000Z/);
  assert.match(calls[0], /Invoke-CimMethod -InputObject \$p -MethodName Terminate/);
  // The identity must be compared before the terminate, never after.
  assert.ok(calls[0].indexOf("-ne '2026") < calls[0].indexOf("Invoke-CimMethod"));
});

test("windows stop leaves a recycled pid alone instead of failing the run", () => {
  assert.doesNotThrow(() =>
    stopWindowsProcess(4242, false, "windows:whatever", () => ({ status: 3 })),
  );
});

test("windows stop surfaces a real termination failure", () => {
  assert.throws(
    () => stopWindowsProcess(4242, false, "windows:whatever", () => ({ status: 1 })),
    /Could not stop process 4242/,
  );
});

// Win32_Process.Terminate reports failure through ReturnValue, not by throwing:
// discarding it would report success on an access-denied kill, and the port
// would still be held.
test("windows stop fails when Terminate reports a non-zero ReturnValue", () => {
  const run = (_exe, args) => {
    assert.match(args.at(-1), /ReturnValue -ne 0/);
    return { status: 4, stdout: "2" };
  };
  assert.throws(
    () => stopWindowsProcess(4242, false, "windows:whatever", run),
    /Could not stop process 4242: Terminate returned 2/,
  );
});

// #1974: a backend the Tauri shell spawned lives under a per-app directory
// named after the bundle id, not under the git checkout. The ownership test
// only knew about the checkout, so the launcher treated its own orphaned
// backend as a stranger, refused to reclaim port 3900, and aborted the run
// with no way forward but Task Manager.
test("an app-managed backend is recognised as ours", () => {
  const root = "/work/VoiceStudio";
  const macApp = `/Users/x/Library/Application Support/${APP_BUNDLE_ID}/project/.venv/bin/python`;
  assert.equal(belongsToCheckout("", "", macApp, false, root), true);
  assert.equal(belongsToCheckout("", `${macApp} -m uvicorn main:app`, "", false, root), true);

  // Built by join so the Windows separators need no escaping in source.
  const sep = String.fromCharCode(92);
  const winApp = ["C:", "Users", "x", "AppData", "Roaming", APP_BUNDLE_ID, "project", ".venv", "Scripts", "python.exe"].join(sep);
  assert.equal(belongsToCheckout("", "", winApp, true, ["C:", "repo"].join(sep)), true);
});

test("an Electron-managed runtime backend is recognised as ours", () => {
  const root = "/work/VoiceStudio";
  const linux = "/home/x/.config/VoiceStudio/runtime/project/.venv/bin/python";
  assert.equal(belongsToCheckout("", `${linux} -m uvicorn main:app`, "", false, root), true);
  const custom = "/mnt/fast/VoiceStudio/project/.venv/bin/python";
  assert.equal(belongsToCheckout("", "", custom, false, root), true);
  const sep = String.fromCharCode(92);
  const win = ["C:", "Users", "x", "AppData", "Roaming", "VoiceStudio", "runtime", "project", ".venv", "Scripts", "python.exe"].join(sep);
  assert.equal(belongsToCheckout("", "", win, true, ["C:", "repo"].join(sep)), true);
  // Another project's venv in a folder that merely mentions the name is not ours.
  assert.equal(belongsToCheckout("", "", "/srv/voicestudio-clone/.venv/bin/python", false, root), false);
});

test("the bundle id match is case-insensitive", () => {
  // Windows paths come back with inconsistent casing depending on the API.
  const root = "/work/VoiceStudio";
  const shouty = `/Users/x/Library/Application Support/${APP_BUNDLE_ID.toUpperCase()}/project/.venv/bin/python`;
  assert.equal(belongsToCheckout("", "", shouty, false, root), true);
});

test("a foreign process is still refused", () => {
  // The guard exists to avoid killing someone else's service on the port;
  // widening ownership must not widen it to everything.
  const root = "/work/VoiceStudio";
  assert.equal(belongsToCheckout("", "python -m http.server 3900", "", false, root), false);
  assert.equal(belongsToCheckout("", "", "/usr/bin/python3", false, root), false);
  assert.equal(
    belongsToCheckout("", "node /opt/com.someoneelse.app/server.js", "", false, root),
    false,
  );
});
