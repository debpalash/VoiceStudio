// ──────────────────────────────────────────────────────────────────────────
// dev-backend.mjs — `bun run dev:api` wrapper that makes a backend death
// LOUD instead of silent (#1164).
//
// In dev, concurrently's --kill-others-on-fail used to tear the whole stack
// down the moment uvicorn exited, and the only trace of WHY was whatever
// scrolled past in the terminal — the browser tab just showed "Can't reach
// the local VoiceStudio backend". This wrapper launches uvicorn directly,
// owns Python source reloads, restarts isolated crashes with a bounded backoff,
// and prints a boxed banner with:
//   - the exit code / signal,
//   - the last 20 lines of omnivoice.log (resolved like
//     backend/core/config.py::get_app_data_dir),
//   - an OOM-check hint on Linux (journalctl -k), and
//   - a pointer to the crash notice the run sentinel will raise on the next
//     backend start.
// Repeated crashes still exit non-zero so --kill-others-on-fail can tear down
// the broken stack instead of hiding an infinite crash loop.
//
// Runs under bun and node alike; cross-platform (uv resolves to uv.exe via
// the Windows CreateProcess PATH search — no shell needed).
// ──────────────────────────────────────────────────────────────────────────

import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync, watch } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

export const DEFAULT_BACKEND_PORT = 3900;

/** The port Vite proxies to (electron/vite.web.config.ts: OMNIVOICE_PORT || 3900). */
export function backendPort(env = process.env) {
  const port = Number.parseInt(String(env.OMNIVOICE_PORT ?? "").trim(), 10);
  return Number.isInteger(port) && port > 0 && port < 65536 ? port : DEFAULT_BACKEND_PORT;
}

/**
 * Loopback by default, like backend/main.py: the API ships unauthenticated on
 * loopback, so `dev:api` must not publish it to the LAN. OMNIVOICE_BIND_HOST
 * opts in to another interface, the same knob the backend honours.
 */
export function bindHost(env = process.env) {
  return String(env.OMNIVOICE_BIND_HOST ?? "").trim() || "127.0.0.1";
}

// The wrapper owns Python source reloads so uvicorn's reload parent cannot stay
// alive after its server worker dies (#1690).
export function uvicornArgs(env = process.env) {
  return [
    "run",
    "uvicorn",
    "main:app",
    "--app-dir",
    "backend",
    "--host",
    bindHost(env),
    "--port",
    String(backendPort(env)),
  ];
}

export const CRASH_RESTART_DELAY_MS = 1_000;
export const CRASH_RESTART_LIMIT = 3;
export const CRASH_RESTART_WINDOW_MS = 60_000;
export const SOURCE_RELOAD_DEBOUNCE_MS = 250;

/**
 * Stop a spawned backend and everything it started.
 *
 * We spawn `uv`, which spawns uvicorn as its own child. Windows has no signals:
 * `child.kill()` maps to TerminateProcess on the DIRECT child only, so killing
 * `uv` orphaned the uvicorn grandchild — which kept holding port 3900. The next
 * spawn then failed to bind ([Errno 10048]), which the supervisor counted as a
 * crash, and three of those tore the whole dev stack down. `taskkill /T` walks
 * the tree; POSIX keeps plain signal delivery.
 */
export function killProcessTree(child, sig, { platform = process.platform, run = spawnSync } = {}) {
  if (platform !== "win32") {
    child.kill(sig);
    return "signal";
  }
  const result = run("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  if (!result || result.error || result.status !== 0) {
    child.kill(sig); // taskkill unavailable or the process already exited
    return "fallback";
  }
  return "taskkill";
}

export function isBackendSourceChange(filename) {
  return typeof filename === "string" && filename.toLowerCase().endsWith(".py");
}

/** Mirror backend/core/config.py::get_app_data_dir() so the banner reads the
 *  same omnivoice.log the backend writes. Pure — testable with fake inputs. */
export function resolveDataDir(env = process.env, platform = process.platform, home = homedir()) {
  if (env.OMNIVOICE_DATA_DIR) return env.OMNIVOICE_DATA_DIR;
  if (platform === "darwin") return path.join(home, "Library/Application Support/OmniVoice");
  if (platform === "win32") return path.join(env.APPDATA || "", "OmniVoice");
  return path.join(home, ".omnivoice");
}

/** Last `n` lines of a file, or null when unreadable. Pure-ish (fs read). */
export function tailFile(filePath, n = 20) {
  try {
    if (!existsSync(filePath)) return null;
    const lines = readFileSync(filePath, "utf-8").split(/\r?\n/);
    while (lines.length && lines[lines.length - 1] === "") lines.pop();
    return lines.slice(-n).join("\n");
  } catch {
    return null;
  }
}

/** The banner text (pure, testable). `code`/`signal` from the child's exit. */
export function buildExitBanner({ code, signal, logTail, logPath, platform = process.platform }) {
  const bar = "═".repeat(74);
  const how = signal ? `killed by signal ${signal}` : `exit code ${code}`;
  const lines = [
    "",
    `╔${bar}╗`,
    "║  OMNIVOICE BACKEND DIED — this is why the UI says it can't reach it.",
    `║  uvicorn ended with ${how}.`,
    "╚" + bar + "╝",
    "",
  ];
  if (logTail) {
    lines.push(`Last 20 lines of ${logPath}:`, "─".repeat(76), logTail, "─".repeat(76), "");
  } else {
    lines.push(
      `(no omnivoice.log found at ${logPath} — the backend may have died before logging)`,
      "",
    );
  }
  if (signal === "SIGKILL" || code === 137) {
    lines.push("SIGKILL usually means the operating system's out-of-memory killer stopped it.");
  }
  if (platform === "linux") {
    lines.push("If you suspect an OOM kill, check:  journalctl -k | grep -i oom", "");
  }
  lines.push(
    "This death will also be reported as a crash notice in the UI the next time",
    "the backend starts (run sentinel, see docs/install/troubleshooting.md).",
    "",
  );
  return lines.join("\n");
}

// Setup is explicit and chooses the CPU/CUDA wheel graph. Never let launch or
// restart silently restore the default CUDA group (or undo a ROCm swap).
export function uvRunArgs(env = process.env) {
  const args = uvicornArgs(env);
  return [args[0], "--no-sync", ...args.slice(1)];
}

/**
 * Supervise the dev backend without hiding a persistent crash loop. Dependencies
 * are injectable so crash/restart/signal behavior is deterministic in tests.
 */
export function createBackendSupervisor({
  spawnBackend = () => spawn("uv", uvRunArgs(), { stdio: "inherit" }),
  watchBackend = (onChange) =>
    watch("backend", { recursive: true }, (_event, filename) => onChange(filename?.toString())),
  killBackend = (proc, sig) => killProcessTree(proc, sig),
  schedule = setTimeout,
  cancelSchedule = clearTimeout,
  now = Date.now,
  exit = (code) => process.exit(code),
  report = (message) => console.error(message),
  dataDir = () => resolveDataDir(),
} = {}) {
  let interrupted = false;
  let child = null;
  let restartTimer = null;
  let reloadTimer = null;
  let reloadRequested = false;
  // True when the reload we asked for was served by a forced tree-kill, which
  // reports a non-zero exit and no signal. Only then may such an exit be read
  // as "the reload we asked for" instead of "it crashed mid-reload".
  let reloadKillForced = false;
  let watcher = null;
  let crashTimes = [];
  let requestedExitCode = null;

  function closeWatcher() {
    if (!watcher) return;
    watcher.close();
    watcher = null;
  }

  function stop(sig, exitCode = null) {
    if (interrupted) return;
    interrupted = true;
    requestedExitCode = exitCode;
    closeWatcher();
    if (reloadTimer !== null) {
      cancelSchedule(reloadTimer);
      reloadTimer = null;
    }
    if (restartTimer !== null) {
      cancelSchedule(restartTimer);
      restartTimer = null;
      exit(requestedExitCode ?? 0);
      return;
    }
    if (!child) {
      exit(requestedExitCode ?? 0);
      return;
    }
    try {
      killBackend(child, sig);
    } catch {
      exit(requestedExitCode ?? 0);
    }
  }

  function requestReload(filename) {
    if (interrupted || reloadRequested || !isBackendSourceChange(filename)) return;
    if (reloadTimer !== null) cancelSchedule(reloadTimer);
    reloadTimer = schedule(() => {
      reloadTimer = null;
      if (!child || restartTimer !== null) return;
      reloadRequested = true;
      report(`[dev-backend] ${filename} changed; reloading backend…`);
      try {
        reloadKillForced = killBackend(child, "SIGTERM") === "taskkill";
      } catch {
        reloadRequested = false;
      }
    }, SOURCE_RELOAD_DEBOUNCE_MS);
  }

  function start() {
    if (interrupted) return;

    if (!watcher) {
      try {
        watcher = watchBackend(requestReload);
      } catch (err) {
        report(`[dev-backend] could not watch backend sources: ${err.message}`);
        exit(1);
        return;
      }
      watcher.on?.("error", (err) => {
        if (interrupted) return;
        report(`[dev-backend] backend source watcher failed: ${err.message}`);
        stop("SIGTERM", 1);
      });
    }

    let current;
    try {
      current = spawnBackend();
    } catch (err) {
      report(`[dev-backend] could not start uv: ${err.message}`);
      exit(1);
      return;
    }
    child = current;
    let settled = false;

    current.once("error", (err) => {
      if (settled) return;
      settled = true;
      if (child === current) child = null;
      report(`[dev-backend] could not start uv: ${err.message}`);
      exit(1);
    });

    current.once("exit", (code, signal) => {
      if (settled) return;
      settled = true;
      if (child === current) child = null;

      if (interrupted) {
        exit(requestedExitCode ?? (signal ? 1 : (code ?? 0)));
        return;
      }

      if (reloadRequested) {
        reloadRequested = false;
        const expectedReloadExit =
          code === 0 || signal === "SIGTERM" || reloadKillForced;
        reloadKillForced = false;
        if (expectedReloadExit) {
          start();
          return;
        }
      }

      const crashed = Boolean(signal) || (code !== 0 && code != null);
      if (!crashed) {
        exit(code ?? 0);
        return;
      }

      const dir = dataDir();
      const logPath = path.join(dir, "omnivoice.log");
      report(buildExitBanner({ code, signal, logTail: tailFile(logPath, 20), logPath }));

      const timestamp = now();
      crashTimes = crashTimes.filter(
        (crashTime) => timestamp - crashTime < CRASH_RESTART_WINDOW_MS,
      );
      if (crashTimes.length >= CRASH_RESTART_LIMIT) {
        report(
          `[dev-backend] stopped after ${CRASH_RESTART_LIMIT} restarts in ` +
            `${CRASH_RESTART_WINDOW_MS / 1_000}s; fix the crash above, then run bun run dev again.`,
        );
        exit(signal ? 1 : (code ?? 1));
        return;
      }

      crashTimes.push(timestamp);
      report(
        `[dev-backend] restarting in ${CRASH_RESTART_DELAY_MS / 1_000}s ` +
          `(${crashTimes.length}/${CRASH_RESTART_LIMIT})…`,
      );
      restartTimer = schedule(() => {
        restartTimer = null;
        start();
      }, CRASH_RESTART_DELAY_MS);
    });
  }

  return { start, stop };
}

function main() {
  const supervisor = createBackendSupervisor();

  // A Ctrl+C / concurrently teardown is a DELIBERATE stop — no scary banner
  // and no restart.
  for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
    try {
      process.on(sig, () => supervisor.stop(sig));
    } catch {
      /* signal unsupported on this platform (e.g. SIGHUP on Windows) */
    }
  }

  supervisor.start();
}

// Import-safe: tests import the pure helpers without spawning anything.
// fileURLToPath (not URL.pathname) so the comparison also holds on Windows,
// where pathname yields "/C:/…" but argv[1] is "C:\…".
const isMain =
  process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));
if (isMain) main();
