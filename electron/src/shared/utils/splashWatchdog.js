/**
 * IPC-independent splash watchdog (issue #879).
 *
 * After an unclean shutdown (Windows BSOD), the WebView2 profile cache
 * (%LOCALAPPDATA%\<identifier>\EBWebView) can corrupt. Tauri's IPC custom
 * protocol then fails ("IPC custom protocol failed, Tauri will now use the
 * postMessage interface instead — TypeError: Failed to fetch") and the
 * postMessage fallback can be broken too: `invoke()` hangs forever and no
 * bootstrap events are ever delivered. The splash used to gate the
 * splash → app transition solely on that IPC, so it sat at "preparing"
 * forever even with a fully healthy backend answering over plain HTTP.
 *
 * This watchdog is the escape hatch — it only trusts plain HTTP:
 *
 *   t + ipcSilenceMs    no IPC signal yet → start polling GET /health
 *   backend healthy     proceed to the app as if 'ready' was received
 *   t + recoveryAfterMs neither IPC nor healthy HTTP → onStuck() renders the
 *                       recovery panel. Health polling keeps running after
 *                       that, so a slow first-run install with broken IPC
 *                       still reaches the app once the backend comes up.
 *
 * Any successful IPC signal (a `bootstrap_status` response) at any point
 * disarms the watchdog permanently via `markIpcAlive()` — the normal
 * IPC-driven path owns the transition from then on.
 *
 * Detection note (part 3 of the #879 fix): Tauri 2 does NOT expose the
 * "custom protocol failed → postMessage fallback" state to page JS (it's a
 * closure-local in its injected ipc.js and only surfaces as a console.warn
 * from the init script). The reliable detector for a broken IPC layer is
 * therefore exactly the combination this watchdog observes — zero IPC
 * signals plus a working plain-HTTP backend — and we console.warn that
 * combination so diagnostic bundles / auto bug reports carry the breadcrumb.
 */

/** How long to wait for the first IPC signal before falling back to HTTP. */
export const IPC_SILENCE_MS = 10_000;
/** Total window before declaring the startup stuck (recovery panel). */
export const RECOVERY_AFTER_MS = 45_000;
/** Interval between /health polls once the HTTP fallback is active. */
export const HEALTH_POLL_MS = 2_000;

/**
 * Start the watchdog. Returns { markIpcAlive, cancel }.
 *
 * @param {object} opts
 * @param {string} opts.healthUrl - absolute URL of the backend /health endpoint
 * @param {() => void} opts.onReadyViaHttp - backend healthy but IPC silent → proceed
 * @param {() => void} opts.onStuck - neither IPC nor HTTP after recoveryAfterMs
 * @param {typeof fetch} [opts.fetchFn] - injectable for tests
 */
export function startSplashWatchdog({
  healthUrl,
  onReadyViaHttp,
  onStuck,
  fetchFn,
  ipcSilenceMs = IPC_SILENCE_MS,
  recoveryAfterMs = RECOVERY_AFTER_MS,
  healthPollMs = HEALTH_POLL_MS,
}) {
  const doFetch = fetchFn || ((...args) => fetch(...args));
  let done = false; // IPC alive, cancelled, or already proceeded via HTTP
  let stuckFired = false;
  let silenceTimer = null;
  let stuckTimer = null;
  let pollTimer = null;

  const clearTimers = () => {
    if (silenceTimer) clearTimeout(silenceTimer);
    if (stuckTimer) clearTimeout(stuckTimer);
    if (pollTimer) clearTimeout(pollTimer);
    silenceTimer = stuckTimer = pollTimer = null;
  };

  const finish = () => {
    done = true;
    clearTimers();
  };

  const poll = async () => {
    if (done) return;
    let healthy = false;
    try {
      // AbortSignal.timeout keeps a hung request from stalling the chain;
      // the next poll is only scheduled after this one settles.
      const res = await doFetch(healthUrl, {
        cache: 'no-store',
        signal:
          typeof AbortSignal !== 'undefined' && AbortSignal.timeout
            ? AbortSignal.timeout(healthPollMs)
            : undefined,
      });
      healthy = !!res && res.ok;
    } catch {
      healthy = false;
    }
    if (done) return;
    if (healthy) {
      // The #879 breadcrumb: backend reachable over HTTP, IPC totally silent.
      console.warn(
        '[splash-watchdog] Backend is healthy over plain HTTP but no Tauri IPC signal ever ' +
          'arrived — the WebView IPC layer (custom protocol AND its postMessage fallback) ' +
          'appears broken. Proceeding to the app via HTTP health. A corrupted WebView cache ' +
          'after an unclean shutdown is the usual cause (issue #879).',
      );
      finish();
      onReadyViaHttp();
      return;
    }
    pollTimer = setTimeout(poll, healthPollMs);
  };

  silenceTimer = setTimeout(() => {
    if (done) return;
    console.warn(
      `[splash-watchdog] No Tauri IPC signal within ${Math.round(ipcSilenceMs / 1000)}s — ` +
        `WebView IPC may be broken (issue #879). Falling back to plain-HTTP health polling ` +
        `at ${healthUrl}.`,
    );
    poll();
  }, ipcSilenceMs);

  stuckTimer = setTimeout(() => {
    if (done || stuckFired) return;
    stuckFired = true;
    console.warn(
      '[splash-watchdog] Startup stuck: no Tauri IPC signal AND the backend never answered ' +
        '/health within the recovery window — showing the recovery panel (issue #879).',
    );
    onStuck();
    // Deliberately NOT finish(): keep polling /health so a slow first-run
    // install with broken IPC still transitions to the app when it comes up.
  }, recoveryAfterMs);

  return {
    /** Call on any successful IPC response — disarms the watchdog for good. */
    markIpcAlive() {
      finish();
    },
    cancel() {
      finish();
    },
  };
}

/**
 * Poll GET /health until it answers OK, then fire onHealthy exactly once.
 *
 * Used by the splash's `failed` stage (#1156): `failed` used to be fully
 * terminal — the IPC poll loop stopped and the successful IPC reply had
 * already disarmed the #879 watchdog — so a backend that came back on its
 * own (supervisor restart, completed dependency repair) could never dismiss
 * the "Setup failed" card. This poller is that missing exit.
 */
export function startHealthRecoveryPoll({
  healthUrl,
  onHealthy,
  fetchFn,
  healthPollMs = HEALTH_POLL_MS,
}) {
  const doFetch = fetchFn || ((...args) => fetch(...args));
  let done = false;
  let pollTimer = null;

  const poll = async () => {
    if (done) return;
    let healthy = false;
    try {
      const res = await doFetch(healthUrl, {
        cache: 'no-store',
        signal:
          typeof AbortSignal !== 'undefined' && AbortSignal.timeout
            ? AbortSignal.timeout(healthPollMs)
            : undefined,
      });
      healthy = !!res && res.ok;
    } catch {
      healthy = false;
    }
    if (done) return;
    if (healthy) {
      done = true;
      onHealthy();
      return;
    }
    pollTimer = setTimeout(poll, healthPollMs);
  };
  pollTimer = setTimeout(poll, healthPollMs);

  return {
    cancel() {
      done = true;
      if (pollTimer) clearTimeout(pollTimer);
    },
  };
}
