//! Bootstrap progress tracking, venv creation, and retry commands.

use std::fs;
use std::io::{self, BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{Emitter, Manager};

use crate::config::get_effective_region;
use crate::crash::BackendExit;
use crate::tools::resolve_uv;
use crate::{AppFlags, BackendState, backend_port};

// ── Bootstrap stages ──────────────────────────────────────────────────────

#[derive(Clone, Serialize, Debug)]
#[serde(tag = "stage", rename_all = "snake_case")]
pub enum BootstrapStage {
    /// First run with nothing installed: parked on the setup screen waiting
    /// for the user to confirm an install plan (mode, storage, mirrors).
    /// Nothing downloads or installs in this stage — `complete_setup` is the
    /// only way out of it.
    AwaitingSetup,
    /// Working out whether we need to bootstrap at all.
    Checking,
    /// Fetching the standalone `uv` binary from astral-sh/uv releases.
    DownloadingUv { percent: Option<u8> },
    /// Creating the Python 3.11 venv.
    CreatingVenv,
    /// Running `uv sync --frozen --no-dev`. Biggest time sink on first run
    /// (~5-10 min to pull torch + whisperx + faster-whisper + demucs).
    InstallingDeps,
    /// Venv ready, spawning uvicorn. Should be <5 s.
    StartingBackend,
    /// Backend is listening and healthy. Frontend can leave the splash.
    Ready,
    /// Something blew up; message carries the reason.
    Failed { message: String },
}

pub struct BootstrapState {
    pub stage: Arc<Mutex<BootstrapStage>>,
    pub logs: Arc<Mutex<Vec<LogPayload>>>,
}

/// The last `Failed { message }` diagnosis this session, retained after the
/// stage itself has moved on (#1177).
///
/// A `Failed` stage is not durable: a Retry sets `Checking`, the supervisor
/// sets `StartingBackend` before a respawn, and either overwrite the only copy
/// of the reason the previous start failed. When a later attempt then fails
/// with a vaguer message — or the frontend asks after the transition — that
/// diagnosis is simply gone, and the user is back to an evidence-free "can't
/// reach the backend". Keeping the last one costs a string and is the
/// difference between a diagnosable report and an unactionable one.
static LAST_FAILURE: Mutex<Option<String>> = Mutex::new(None);

pub fn set_stage(state: &Arc<Mutex<BootstrapStage>>, stage: BootstrapStage) {
    set_stage_into(state, &LAST_FAILURE, stage)
}

/// The retention logic itself, with the storage slot as a parameter.
///
/// `set_stage` is a one-line delegate that passes the process-global slot.
/// Splitting it this way keeps the behaviour testable against a caller-owned
/// slot: a test that wrote through the global would mutate shared state with no
/// teardown, and `cargo test` runs the tests in a binary in PARALLEL, so it
/// would race any future test asserting on `last_failure_message()`.
fn set_stage_into(
    state: &Arc<Mutex<BootstrapStage>>,
    last_failure: &Mutex<Option<String>>,
    stage: BootstrapStage,
) {
    if let BootstrapStage::Failed { message } = &stage {
        if let Ok(mut last) = last_failure.lock() {
            *last = Some(message.clone());
        }
    }
    if let Ok(mut guard) = state.lock() {
        *guard = stage;
    }
}

/// The retained diagnosis, for a frontend that reached a `failed` stage whose
/// own message is already gone. `None` when nothing has failed this session.
pub fn last_failure_message() -> Option<String> {
    LAST_FAILURE.lock().ok().and_then(|g| g.clone())
}

#[tauri::command]
pub fn last_bootstrap_failure() -> Option<String> {
    last_failure_message()
}

/// True when the stage already carries a `Failed` diagnosis.
///
/// The venv bootstrap (`ensure_venv_ready`) records the REAL reason a start
/// failed — "Intel Macs can't run the local AI backend", a `uv sync` error, a
/// blocked GitHub — through `fail()`, which sets exactly this. The spawn watcher
/// must not then bulldoze it with the generic "never started" (#1112): a caller
/// that already knows the cause outranks one that only knows the symptom.
pub fn already_diagnosed(state: &Arc<Mutex<BootstrapStage>>) -> bool {
    state
        .lock()
        .map(|g| matches!(*g, BootstrapStage::Failed { .. }))
        .unwrap_or(false)
}

// ── Splash log + byte-progress event channel ─────────────────────────────

#[derive(Clone, Serialize)]
pub struct LogPayload {
    pub stage: String,
    pub line: String,
}

pub fn emit_log<R: tauri::Runtime>(app: &tauri::AppHandle<R>, stage: &str, line: &str) {
    let payload = LogPayload { stage: stage.to_string(), line: line.to_string() };
    // Buffer the log so the frontend can backfill on mount.
    if let Some(state) = app.try_state::<BootstrapState>() {
        if let Ok(mut logs) = state.logs.lock() {
            logs.push(payload.clone());
        }
    }
    let _ = app.emit("bootstrap-log", payload);
}

/// Stream stdout+stderr of a long-running subprocess line-by-line into the
/// splash log panel.
pub fn run_streaming<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    stage: &str,
    cmd: &mut Command,
) -> io::Result<std::process::ExitStatus> {
    if backend_stop_requested(app) {
        return Err(io::Error::new(
            io::ErrorKind::Interrupted,
            "app is quitting",
        ));
    }
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    // Long installs use the same stable containment as the backend. A command
    // which exits while one of its descendants is still alive is drained
    // before its root handle is reaped.
    let crate::tools::ContainedChild {
        mut child,
        mut tree,
    } = crate::tools::spawn_process_tree(cmd)?;
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let app_out = app.clone();
    let app_err = app.clone();
    let stage_out = stage.to_string();
    let stage_err = stage.to_string();
    let h_out = std::thread::spawn(move || {
        if let Some(s) = stdout {
            for line in BufReader::new(s).lines().flatten() {
                log::info!("[{}] {}", stage_out, line);
                emit_log(&app_out, &stage_out, &line);
            }
        }
    });
    let h_err = std::thread::spawn(move || {
        if let Some(s) = stderr {
            for line in BufReader::new(s).lines().flatten() {
                log::info!("[{}] {}", stage_err, line);
                emit_log(&app_err, &stage_err, &line);
            }
        }
    });
    let status = loop {
        if backend_stop_requested(app) {
            log::info!("App is quitting — stopping bootstrap subprocess tree (pid {})", child.id());
            break match crate::tools::terminate_process_tree(
                &mut child,
                &mut tree,
                Duration::from_millis(750),
            ) {
                Ok(_) => Err(io::Error::new(
                    io::ErrorKind::Interrupted,
                    "app quit during bootstrap subprocess",
                )),
                Err(error) => Err(error),
            };
        }
        match crate::tools::contained_child_exit(&mut child, &mut tree) {
            Ok(Some(status)) => break Ok(status),
            Ok(None) => std::thread::sleep(Duration::from_millis(100)),
            Err(error) => break Err(error),
        }
    };
    let _ = h_out.join();
    let _ = h_err.join();
    status
}

// ── Tauri commands ────────────────────────────────────────────────────────

#[tauri::command]
pub fn bootstrap_status(state: tauri::State<'_, BootstrapState>) -> BootstrapStage {
    state
        .stage
        .lock()
        .map(|g| g.clone())
        .unwrap_or(BootstrapStage::Checking)
}

#[tauri::command]
pub fn get_bootstrap_logs(state: tauri::State<'_, BootstrapState>) -> Vec<LogPayload> {
    state
        .logs
        .lock()
        .map(|g| g.clone())
        .unwrap_or_default()
}

fn buffered_log_tail(logs: &[LogPayload], stage: &str, max_lines: usize) -> String {
    let mut lines = logs
        .iter()
        .rev()
        .filter(|entry| entry.stage == stage && !entry.line.trim().is_empty())
        .take(max_lines)
        .map(|entry| entry.line.trim().to_string())
        .collect::<Vec<_>>();
    lines.reverse();
    lines.join("\n")
}

fn command_failure_message(
    prefix: &str,
    result: &io::Result<std::process::ExitStatus>,
    output_tail: &str,
) -> String {
    let outcome = match result {
        Ok(status) => status
            .code()
            .map(|code| format!("exit code {code}"))
            .unwrap_or_else(|| status.to_string()),
        Err(error) => format!("command error: {error}"),
    };
    if output_tail.is_empty() {
        format!("{prefix}: {outcome} — no command output was captured")
    } else {
        format!("{prefix}: {outcome}\n\nLast output:\n{output_tail}")
    }
}

#[tauri::command]
pub fn retry_bootstrap(app: tauri::AppHandle, state: tauri::State<'_, BootstrapState>) {
    respawn_backend(app, state.stage.clone(), state.logs.clone());
}

/// Take the port back and bring a healthy backend up on it, from scratch if
/// need be. Shared by the Retry button and by a scoped reset (`reset.rs`), which
/// deletes data out from under a stopped backend and needs the *same* recovery
/// afterwards — a fresh process that re-runs `ensure_dirs()` and alembic, so a
/// wiped database comes back empty rather than missing.
pub fn respawn_backend<R: tauri::Runtime>(
    app: tauri::AppHandle<R>,
    stage: Arc<Mutex<BootstrapStage>>,
    logs: Arc<Mutex<Vec<LogPayload>>>,
) {
    if let Ok(mut guard) = stage.lock() {
        *guard = BootstrapStage::Checking;
    }
    if let Ok(mut logs) = logs.lock() {
        logs.clear();
    }
    let stage_handle = stage;
    std::thread::spawn(move || {
        if backend_stop_requested(&app) {
            return;
        }
        let skip_spawn = std::env::var("TAURI_SKIP_BACKEND").is_ok();
        if skip_spawn {
            log::info!("TAURI_SKIP_BACKEND set — not spawning");
            set_backend_kill_intended(false);
            set_stage(&stage_handle, BootstrapStage::Ready);
            return;
        }
        spawn_backend_and_wait(&app, &stage_handle);
    });
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LaunchPreparation {
    SuperviseAttached { owner: u64 },
    Spawn,
    Failed,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LaunchOutcome {
    SupervisedReady { owner: u64 },
    Done,
}

/// Run a teardown while holding the same lifecycle ownership every spawn path
/// uses. Reset, Clean & Retry, setup re-entry, uninstall, and app exit join
/// launch through this guard; disk mutations keep it until they are complete,
/// so neither bootstrap nor the supervisor can resurrect the backend.
pub fn with_backend_stopped<R: tauri::Runtime, T>(
    app: &tauri::AppHandle<R>,
    action: impl FnOnce() -> T,
) -> Result<T, String> {
    let state = app.state::<BackendState>();
    let _lifecycle = state.lifecycle.lock().unwrap_or_else(|e| e.into_inner());
    if let Err(error) = stop_backend_locked(app) {
        // stop_backend_locked removes the tracked child before terminating
        // it. A partial teardown error therefore cannot rely on the previous
        // supervisor still existing; re-enter the serialized launch path for
        // a recoverable, non-terminal caller. An incomplete tree deliberately
        // retains the kill-intended fence: spawning alongside survivors could
        // duplicate engines or race files still held open.
        if error.restart_safe {
            set_backend_kill_intended(false);
        }
        if error.restart_safe && !backend_stop_requested(app) {
            if let Some(bootstrap) = app.try_state::<BootstrapState>() {
                respawn_backend(app.clone(), bootstrap.stage.clone(), bootstrap.logs.clone());
            }
        }
        return Err(error.message);
    }
    Ok(action())
}

/// Stop both the tracked child (including one which has not bound yet) and any
/// untracked listener. The caller must own `BackendState::lifecycle`.
struct BackendStopError {
    message: String,
    restart_safe: bool,
}

fn stop_backend_locked<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
) -> Result<(), BackendStopError> {
    set_backend_kill_intended(true);
    let state = app.state::<BackendState>();
    let mut child = state
        .process
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .take();
    let mut owned_tree = state
        .owned_tree
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .take();
    let attached = state.attached.swap(false, Ordering::SeqCst);
    reset_attached_health(&state);
    *state
        .spawned_at
        .lock()
        .unwrap_or_else(|e| e.into_inner()) = None;

    if attached {
        if app
            .try_state::<AppFlags>()
            .is_some_and(|flags| flags.quitting.load(Ordering::SeqCst))
        {
            // A terminal desktop exit releases an attachment; it does not own
            // the external backend and therefore must not signal it.
            return Ok(());
        }
        state.attached.store(true, Ordering::SeqCst);
        set_backend_kill_intended(false);
        return Err(BackendStopError {
            message: "Another VoiceStudio instance owns the running backend. Quit that instance before changing or uninstalling its environment.".to_string(),
            restart_safe: true,
        });
    }

    let tree_error = match (child.as_mut(), owned_tree.as_mut()) {
        (Some(child), Some(tree)) => {
            // The unreaped root/process handle and containment handle move
            // together, closing PID-reuse and post-crash descendant races.
            log::info!("Stopping tracked backend tree (root pid {})", child.id());
            crate::tools::terminate_process_tree(child, tree, Duration::from_secs(2)).err()
        }
        (None, None) => None,
        _ => Some(io::Error::new(
            io::ErrorKind::Other,
            "backend process and containment handles became inconsistent",
        )),
    };
    if let Some(error) = tree_error {
        log::warn!("Could not fully stop tracked backend tree: {error}");
        // Preserve both stable handles for a later teardown attempt. In
        // particular, never fall back to signalling the numeric root PID.
        if let Some(child) = child {
            *state.process.lock().unwrap_or_else(|e| e.into_inner()) = Some(child);
        }
        if let Some(tree) = owned_tree {
            *state.owned_tree.lock().unwrap_or_else(|e| e.into_inner()) = Some(tree);
        }
        return Err(BackendStopError {
            message: format!(
                "VoiceStudio could not fully stop the backend process tree: {error}"
            ),
            restart_safe: false,
        });
    }

    if crate::backend::port_in_use(backend_port())
        && !crate::backend::free_port_or_report(backend_port())
    {
        return Err(BackendStopError {
            message: format!(
                "Port {} is already in use by another application, and VoiceStudio \
                 could not free it. Quit whatever is using that port (another copy \
                 of VoiceStudio, or an app that claimed it) and try again.",
                backend_port()
            ),
            restart_safe: false,
        });
    }
    #[cfg(debug_assertions)]
    if std::env::var_os("OMNIVOICE_TEST_FORCE_STOP_ERROR").is_some() {
        return Err(BackendStopError {
            message: "injected backend stop failure".to_string(),
            restart_safe: true,
        });
    }
    #[cfg(debug_assertions)]
    if std::env::var_os("OMNIVOICE_TEST_FORCE_INCOMPLETE_STOP_ERROR").is_some() {
        return Err(BackendStopError {
            message: "injected incomplete backend tree".to_string(),
            restart_safe: false,
        });
    }
    Ok(())
}

fn tracked_backend_exists<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> bool {
    let state = app.state::<BackendState>();
    state
        .process
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .is_some()
        || state.attached.load(Ordering::SeqCst)
}

/// Probe, attach, or reclaim the backend while lifecycle ownership is held.
/// Keeping the initial probe and any kill in the same critical section as
/// spawn+track closes the empty-port race from #1635.
fn prepare_backend_launch<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    stage_handle: &Arc<Mutex<BootstrapStage>>,
) -> LaunchPreparation {
    let mut replace = tracked_backend_exists(app);
    // #1770: version and code fingerprint are read from ONE `/system/info`
    // fetch (`running_backend_identity`) — never two independent probes for
    // THOSE two fields. Splitting them would let a transport hiccup on the
    // second one masquerade as "responded, but the field was absent", which
    // `code_fingerprint_is_current` treats as stale — silently killing a
    // perfectly healthy backend on a single flaky probe.
    //
    // The health probe (`/profiles`) and the identity probe (`/system/info`)
    // are still two separate requests — they hit different routes, so they
    // can't be folded into one fetch — which leaves a structurally similar
    // window open: two unsynchronized requests can never be atomic, so
    // EITHER ordering can pair the response of one process on the port with
    // a DIFFERENT process's response to the other probe if the port changes
    // hands between them. (This window predates #1770: the old code split
    // `running_backend_version` and `backend_deep_healthy` the same way, but
    // this PR makes the identity check load-bearing, so it's the right
    // place to reason about it explicitly rather than leave it implicit.)
    //
    // Reordering cannot remove that window — it only decides which side of
    // the pair can be stale, and the two failure modes are not equally bad:
    //   - identity-then-health (the naive order): a swap after the identity
    //     probe means we could attach on a STALE IDENTITY paired with a
    //     fresh health check — silently adopting code we never actually
    //     validated. That's exactly the #1770 bug, and nothing downstream
    //     ever re-checks identity on an attached backend, so it would be
    //     permanent for the life of the session.
    //   - health-then-identity (this order): a swap after the health probe
    //     means we could attach on a STALE HEALTH CHECK paired with a fresh
    //     identity — the code is correctly validated, only the "it was
    //     alive a moment ago" claim might be outdated. That failure is
    //     SELF-CORRECTING: an attached backend is polled continuously by
    //     `attached_backend_healthy()` in the supervisor loop (see its grace
    //     window — `attached_backend_health_grace_recovers_without_false_crash_or_port_failure`
    //     covers exactly this), so a backend that was actually unhealthy at
    //     attach time is caught and replaced within one poll interval.
    //     Identity has no equivalent recheck — that absence is the whole
    //     reason this PR exists — so its correctness must be established at
    //     attach time, as late as possible.
    //
    // Health is therefore probed FIRST and identity LAST, immediately
    // before the attach decision, deliberately choosing "stale health" over
    // "stale identity" as the side left exposed. A process swap that
    // happened before the identity probe is caught by that fresh read (the
    // version-mismatch and fingerprint-mismatch arms below fire exactly as
    // if the swap had always been there). The remaining exposure —
    // attaching to a backend whose health was confirmed a moment earlier
    // rather than at this exact instant — is irreducible without one atomic
    // endpoint returning identity + readiness together, and is accepted
    // because the supervisor's continuous health polling already covers it.
    let deep_healthy = crate::backend::backend_deep_healthy(backend_port());
    match crate::backend::running_backend_identity(backend_port()) {
        Some(identity) if crate::backend::same_app_version(&identity.version) => {
            let v = identity.version;
            // Version match alone doesn't prove code match — `main` holds
            // one version string for an entire release cycle, so a
            // same-version backend can still be running weeks-old code.
            let our_fp = own_backend_code_fingerprint(app);
            let code_current =
                crate::backend::code_fingerprint_is_current(identity.code_fingerprint.as_deref(), our_fp.as_deref());
            if !deep_healthy {
                log::warn!(
                    "Port {} serves VoiceStudio v{} but failed the deep health probe — replacing it",
                    backend_port(),
                    v
                );
                replace = true;
            } else if !code_current {
                // Maintainer-recognizable marker: grep logs for
                // "STALE-CODE-ATTACH" or "#1770" to spot this class
                // directly, rather than re-deriving it from an export 422,
                // two reports, and a code audit.
                log::warn!(
                    "STALE-CODE-ATTACH (#1770): port {} serves VoiceStudio v{} — version matches \
this build, but its code fingerprint does not (running={:?}, ours={:?}). A same-version backend \
can still run stale code within one release cycle; replacing it instead of attaching.",
                    backend_port(),
                    v,
                    identity.code_fingerprint,
                    our_fp,
                );
                replace = true;
            } else {
                if replace {
                    let owner = SUPERVISOR_OWNER.fetch_add(1, Ordering::SeqCst) + 1;
                    log::info!(
                        "Port {} already serving tracked VoiceStudio backend v{} — renewing supervision",
                        backend_port(),
                        v
                    );
                    set_backend_kill_intended(false);
                    set_stage(stage_handle, BootstrapStage::Ready);
                    return LaunchPreparation::SuperviseAttached { owner };
                }
                track_attached_backend(app);
                let owner = SUPERVISOR_OWNER.fetch_add(1, Ordering::SeqCst) + 1;
                log::info!(
                    "Port {} already serving VoiceStudio backend v{} — attaching with health supervision (external process remains unowned)",
                    backend_port(),
                    v
                );
                set_stage(stage_handle, BootstrapStage::Ready);
                return LaunchPreparation::SuperviseAttached { owner };
            }
        }
        Some(identity) => {
            log::warn!(
                "Port {} serves a stale VoiceStudio backend (v{} != app v{}) — replacing it",
                backend_port(),
                if identity.version.is_empty() { "<unknown>" } else { identity.version.as_str() },
                env!("CARGO_PKG_VERSION"),
            );
            replace = true;
        }
        None => {
            replace |= crate::backend::port_in_use(backend_port());
        }
    }

    if replace {
        log::warn!("Taking lifecycle ownership of backend port {}", backend_port());
        if let Err(message) = stop_backend_locked(app) {
            if message.restart_safe {
                set_backend_kill_intended(false);
            }
            set_stage(
                stage_handle,
                BootstrapStage::Failed {
                    message: message.message,
                },
            );
            return LaunchPreparation::Failed;
        }
    }
    LaunchPreparation::Spawn
}

/// Initial launch preserves the setup-screen gate, but performs it only after
/// the serialized attach probe. An already-running current backend therefore
/// still wins over a missing first-run marker.
pub fn spawn_initial_backend_and_wait<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    stage_handle: &Arc<Mutex<BootstrapStage>>,
) {
    launch_backend_and_wait(app, stage_handle, true);
}

/// Spawn the backend and poll until it is healthy (→ `Ready`) or dead /
/// timed out (→ `Failed`). Shared by launch, Retry, reset, and setup re-entry.
pub fn spawn_backend_and_wait<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    stage_handle: &Arc<Mutex<BootstrapStage>>,
) {
    launch_backend_and_wait(app, stage_handle, false);
}

fn launch_backend_and_wait<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    stage_handle: &Arc<Mutex<BootstrapStage>>,
    first_run_gate: bool,
) {
    let outcome = {
        let state = app.state::<BackendState>();
        let _lifecycle = state.lifecycle.lock().unwrap_or_else(|e| e.into_inner());
        if backend_stop_requested(app) {
            log::info!("App is quitting — backend launch cancelled");
            LaunchOutcome::Done
        } else {
            match prepare_backend_launch(app, stage_handle) {
                LaunchPreparation::Failed => LaunchOutcome::Done,
                LaunchPreparation::SuperviseAttached { owner } => {
                    LaunchOutcome::SupervisedReady { owner }
                }
                LaunchPreparation::Spawn => {
                    if first_run_gate {
                        crate::setup::migrate_existing_install_if_needed(app);
                        if crate::setup::is_first_run(app) {
                            log::info!(
                                "First run — awaiting setup screen confirmation before installing"
                            );
                            set_backend_kill_intended(false);
                            set_stage(stage_handle, BootstrapStage::AwaitingSetup);
                            LaunchOutcome::Done
                        } else {
                            spawn_with_supervisor_owner(app, stage_handle)
                        }
                    } else {
                        spawn_with_supervisor_owner(app, stage_handle)
                    }
                }
            }
        }
    };

    if let LaunchOutcome::SupervisedReady { owner } = outcome {
        supervise_backend(app, stage_handle, owner);
    }
}

/// Invalidate any previous supervisor before creating a replacement child, so
/// it cannot mistake this child's pre-Ready exit for a post-Ready crash. The
/// reserved owner starts monitoring only after this launch reaches Ready.
fn spawn_with_supervisor_owner<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    stage_handle: &Arc<Mutex<BootstrapStage>>,
) -> LaunchOutcome {
    let supervisor_owner = SUPERVISOR_OWNER.fetch_add(1, Ordering::SeqCst) + 1;
    if spawn_backend_until_ready(app, stage_handle) {
        LaunchOutcome::SupervisedReady {
            owner: supervisor_owner,
        }
    } else {
        LaunchOutcome::Done
    }
}

///
/// #314: when the backend dies with a broken-venv signature ("No pyvenv.cfg
/// file" / exit code 106 from the CPython venv launcher), the venv — and only
/// the venv — is removed and the bootstrap re-runs once, recreating it through
/// the normal `CreatingVenv` / `InstallingDeps` setup path instead of
/// surfacing the same dead-end failure on every retry. Lifecycle ownership is
/// already held by the caller for this entire function.
fn spawn_backend_until_ready<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    stage_handle: &Arc<Mutex<BootstrapStage>>,
) -> bool {
    let mut venv_heal_attempted = false;
    'bootstrap: loop {
        if backend_stop_requested(app) {
            return false;
        }
        spawn_and_track_backend(app, stage_handle);
        let start = std::time::Instant::now();
        // Early-bind narration: the backend answers /startup/progress within
        // ~1s of spawn, long before it is Ready — surface each step change
        // as a log line so the splash shows "Loading ML runtime (PyTorch)…"
        // instead of a silent 300s wait. An old backend (no endpoint) yields
        // None and the wait looks exactly as it did before.
        let mut last_step = String::new();
        while start.elapsed() < startup_budget() {
            if backend_stop_requested(app) {
                log::info!("App is quitting — backend startup poll cancelled");
                return false;
            }
            if crate::backend::backend_ready(backend_port()) {
                set_stage(stage_handle, BootstrapStage::Ready);
                return true;
            }
            let process_dead: Option<(String, Option<BackendExit>)> =
                match backend_child_exit(app) {
                    Ok(Some(exit)) => Some((exit.description.clone(), Some(exit))),
                    Ok(None) if !tracked_backend_exists(app) => {
                        // Spawn itself failed — no process ever ran, so this
                        // is a spawn failure (spawn_failure_diagnostic owns
                        // it), NOT a crash: no marker.
                        Some(("never started".to_string(), None))
                    }
                    Ok(None) => None,
                    Err(error) => {
                        // The root was observed but its stable containment
                        // could not be drained. Never start a second backend
                        // alongside descendants whose teardown is uncertain.
                        set_backend_kill_intended(true);
                        let message = format!(
                            "VoiceStudio could not safely clean up the failed backend: {error}"
                        );
                        set_stage(stage_handle, BootstrapStage::Failed { message });
                        return false;
                    }
                };
            if let Some((exit_info, real_exit)) = process_dead {
                let err_tail = crate::backend::read_error_log_tail_for_run(30);
                // #941: persist the forensics for every true process death —
                // startup crashes included — unless the app is shutting down
                // or a retry flow deliberately killed the child.
                if let Some(ref exit) = real_exit {
                    if !backend_stop_requested(app)
                        && !backend_kill_intended()
                        && crate::crash::should_record_backend_crash(exit)
                    {
                        crate::crash::record_crash(crate::crash::marker_now(
                            exit,
                            backend_uptime_s(app),
                            crate::backend::read_error_log_tail_for_run(CRASH_STDERR_TAIL_LINES),
                        ));
                    }
                }
                // #314: a backend that dies because the venv itself is broken
                // can only be healed by rebuilding the venv — do that once
                // instead of failing into an unwinnable retry loop.
                if !venv_heal_attempted
                    && backend_exit_indicates_broken_venv(&exit_info, &err_tail)
                {
                    venv_heal_attempted = true;
                    let venv_dir = crate::setup::env_root(app).join("project").join(".venv");
                    // Data-safe guard (feat/safe-updates): the signature above
                    // is text matching — confirm the venv is actually broken
                    // (structural check + direct interpreter probe) before
                    // destroying it. A healthy venv is never deleted.
                    let structural = venv_structural_problem(&venv_dir);
                    let probe = venv_interpreter_probe(&venv_python_path(&venv_dir));
                    if venv_rebuild_justified(structural.as_deref(), probe) {
                        log::warn!(
                            "Backend exited with a broken-venv signature ({}; structural={:?}, probe={:?}) — removing {} and rebuilding (#314)",
                            exit_info,
                            structural,
                            probe,
                            venv_dir.display()
                        );
                        emit_log(
                            app,
                            "checking",
                            "Backend failed because the Python environment is broken — rebuilding it automatically",
                        );
                        if quarantine_broken_venv(&venv_dir) {
                            set_stage(stage_handle, BootstrapStage::Checking);
                            continue 'bootstrap;
                        }
                        log::error!(
                            "Could not remove broken venv at {} — surfacing the failure",
                            venv_dir.display()
                        );
                    } else {
                        log::warn!(
                            "Backend exit matched a broken-venv signature ({}) but the venv at {} probes healthy — keeping it (data-safe guard) and surfacing the real error",
                            exit_info,
                            venv_dir.display()
                        );
                    }
                }
                // #1112: when the backend NEVER started, `ensure_venv_ready` has
                // usually already diagnosed exactly why — Intel Mac unsupported,
                // a failed `uv sync`, a blocked GitHub — and recorded it via
                // `fail()` as a Failed stage carrying that reason. Overwriting it
                // here with the generic "never started — no error output captured"
                // destroyed every precise diagnosis: the user saw a message with
                // no cause, and the UI's hint matcher (which keys off the specific
                // text — e.g. the Intel-Mac hint) could never fire, so they were
                // offered a Retry that can never work. Keep the specific reason.
                //
                // A REAL spawn failure (exec error) is unaffected: it writes its
                // diagnostic to backend_err.log and leaves the stage un-Failed, so
                // the message below still forms with that tail. Likewise a genuine
                // crash after a successful start (stage is Ready/StartingBackend).
                if already_diagnosed(stage_handle) {
                    log::error!(
                        "Backend never started ({}) — keeping the specific failure already diagnosed",
                        exit_info
                    );
                    return false;
                }
                // #1783: the interpreter dies inside `site` before main.py runs
                // when the venv path has a byte that isn't valid in the active
                // Windows ANSI code page (`.pth` files decode with
                // `encoding="locale"` on Python 3.11 — PYTHONUTF8=1 doesn't
                // help). `setup::ascii_safe_dir` prevents this for every new
                // venv, but name the cause specifically if it still happens
                // (8.3 short names disabled, or an already-broken pre-fix
                // install) instead of dumping the raw traceback.
                let msg = if backend_exit_indicates_nonascii_pth_crash(&err_tail) {
                    nonascii_pth_crash_msg(crate::config::load_config(app).install_mode == "portable")
                } else if real_exit
                    .as_ref()
                    .and_then(|e| e.code)
                    .is_some_and(|c| c == crate::backend::EXIT_PORT_IN_USE)
                {
                    format!(
                        "Port {} is already in use, so the backend could not \
                         start. Another copy of VoiceStudio — or an app that \
                         claimed that port — is holding it. Quit it and try \
                         again; if nothing is visibly running, an orphaned \
                         backend from a previous session still has the port.",
                        backend_port()
                    )
                } else if err_tail.is_empty() {
                    format!("Backend process exited ({}) — no error output captured", exit_info)
                } else {
                    format!("Backend process exited ({}):\n{}", exit_info, err_tail)
                };
                log::error!("Backend died early: {}", msg);
                set_stage(stage_handle, BootstrapStage::Failed { message: msg });
                return false;
            }
            if let Some((status, step, label)) =
                crate::backend::startup_progress(backend_port())
            {
                if status == "starting" && !step.is_empty() && step != last_step {
                    last_step = step;
                    emit_log(app, "starting_backend", &format!("Startup: {label}"));
                }
            }
            std::thread::sleep(Duration::from_millis(500));
        }
        if backend_stop_requested(app) {
            return false;
        }
        let err_tail = crate::backend::read_error_log_tail_for_run(20);
        let msg = if err_tail.is_empty() {
            format!("Backend did not respond within {} s", startup_budget().as_secs())
        } else {
            format!(
                "Backend did not respond within {} s. Last stderr output:\n{}",
                startup_budget().as_secs(),
                err_tail
            )
        };
        set_stage(stage_handle, BootstrapStage::Failed { message: msg });
        return false;
    }
}

// ── Backend supervisor (auto-restart) ─────────────────────────────────────
//
// #567/#570/#571: the backend used to be spawned once and never watched again
// (`spawn_backend_and_wait` returned the instant it was healthy). When the
// uvicorn process then died mid-session — a CUDA OOM/context fault under a
// burst of generations, an antivirus kill, any crash — nothing restarted it,
// so every later request threw connection-refused and the user was stuck on
// the "Can't reach the local backend" toast until they restarted the whole
// app. The supervisor closes that gap: after Ready, it watches the child and
// respawns it (bounded) so a crash self-heals.

/// Ownership token for the supervisor loop. Each lifecycle-owned spawn
/// reserves a new token before creating its child, then starts monitoring only
/// if that child reaches Ready. An older loop observes the mismatch before any
/// later lifecycle mutation and exits, without a timing-based handoff.
static SUPERVISOR_OWNER: AtomicU64 = AtomicU64::new(0);

/// #941: set while a retry/clean-retry flow deliberately kills the backend to
/// replace it, so the death watchers (startup poll + supervisor) never write a
/// crash marker for — or respawn against — an *intentional* kill. Cleared the
/// moment a fresh child is spawned and tracked (`track_backend_child`).
static BACKEND_KILL_INTENDED: AtomicBool = AtomicBool::new(false);

/// Bumped every time `track_backend_child` installs a new child. The
/// supervisor snapshots it when it observes a death; a change during its
/// backoff pause means ANOTHER flow (Retry / Clean & Retry) spawned and
/// tracked a replacement — ownership has transferred, whether or not that
/// replacement is still alive when sampled (the flag and a liveness check
/// can both be missed inside one 500ms window; the generation cannot).
static BACKEND_SPAWN_GENERATION: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);

pub fn set_backend_kill_intended(value: bool) {
    BACKEND_KILL_INTENDED.store(value, Ordering::SeqCst);
}

fn backend_kill_intended() -> bool {
    BACKEND_KILL_INTENDED.load(Ordering::SeqCst)
}

/// Whether a failed stop completed enough teardown for an explicit recovery
/// launch. Incomplete process trees keep the deliberate-kill fence raised so
/// uninstall rollback cannot spawn alongside surviving engines/installers.
pub fn backend_stop_recovery_safe() -> bool {
    !backend_kill_intended()
}

/// How much of backend_err.log rides inside a crash marker (#941). ~40 lines
/// is enough for a Python traceback or a native abort banner without bloating
/// the marker file or the bug-report URL (the frontend truncates further).
const CRASH_STDERR_TAIL_LINES: usize = 40;

/// Crash-loop escalation guard (#941, supersedes the #567 5-in-60s budget):
/// give up (surface Failed with the crash details) once the backend has died
/// `MAX_RESTARTS` times inside `RESTART_WINDOW`. The longer 10-minute window
/// catches *slow* crash loops (e.g. an engine that OOMs a couple of minutes
/// into every generation) that the old 60-second window let spin silently
/// forever. The #314 broken-venv self-heal stays the venv-failure path; the
/// supervisor only handles post-Ready deaths.
const MAX_RESTARTS: usize = 3;
const RESTART_WINDOW: Duration = Duration::from_secs(600);

fn backend_stop_requested<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> bool {
    app.try_state::<AppFlags>()
        .map(|f| f.quitting.load(Ordering::SeqCst) || f.uninstalling.load(Ordering::SeqCst))
        .unwrap_or(false)
}

/// Store the freshly spawned backend child (and its spawn time, for the crash
/// marker's `uptime_s`), and re-arm the death watchers: any deliberate-kill
/// window ends the moment a new child is tracked.
fn track_backend_child<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    contained: Option<crate::tools::ContainedChild>,
) {
    let state = app.state::<BackendState>();
    let (child, owned_tree, spawned_at) = match contained {
        Some(crate::tools::ContainedChild { child, tree }) => {
            (Some(child), Some(tree), Some(Instant::now()))
        }
        None => (None, None, None),
    };
    *state.process.lock().unwrap_or_else(|e| e.into_inner()) = child;
    *state.owned_tree.lock().unwrap_or_else(|e| e.into_inner()) = owned_tree;
    *state.spawned_at.lock().unwrap_or_else(|e| e.into_inner()) = spawned_at;
    state.attached.store(false, Ordering::SeqCst);
    reset_attached_health(&state);
    BACKEND_SPAWN_GENERATION.fetch_add(1, Ordering::SeqCst);
    set_backend_kill_intended(false);
}

/// Health-supervise a same-version listener which predates this launch. It is
/// intentionally not adopted by PID: only a process spawned into our stable
/// containment primitive is safe for this desktop instance to terminate.
fn track_attached_backend<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    let state = app.state::<BackendState>();
    *state.process.lock().unwrap_or_else(|e| e.into_inner()) = None;
    *state.owned_tree.lock().unwrap_or_else(|e| e.into_inner()) = None;
    *state
        .spawned_at
        .lock()
        .unwrap_or_else(|e| e.into_inner()) = None;
    state.attached.store(true, Ordering::SeqCst);
    reset_attached_health(&state);
    BACKEND_SPAWN_GENERATION.fetch_add(1, Ordering::SeqCst);
    set_backend_kill_intended(false);
}

/// The one spawn→track chokepoint. Callers already hold lifecycle ownership,
/// so no child can be created without becoming the uniquely tracked child.
fn spawn_and_track_backend<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    stage_handle: &Arc<Mutex<BootstrapStage>>,
) {
    let child = crate::backend::spawn_backend(app, Some(stage_handle));
    #[cfg(debug_assertions)]
    wait_for_tracking_test_gate(
        "OMNIVOICE_TEST_BEFORE_TRACK_ENTERED",
        "OMNIVOICE_TEST_BEFORE_TRACK_RELEASE",
    );
    track_backend_child(app, child);
    #[cfg(debug_assertions)]
    wait_for_tracking_test_gate(
        "OMNIVOICE_TEST_AFTER_TRACK_ENTERED",
        "OMNIVOICE_TEST_AFTER_TRACK_RELEASE",
    );
}

/// Deterministic fault-injection seam around child tracking. These are the
/// precise pre-bind shutdown/uninstall races that process-only or port-only
/// teardown used to lose. Compiled out of release builds.
#[cfg(debug_assertions)]
fn wait_for_tracking_test_gate(entered_var: &str, release_var: &str) {
    let Some(entered) = std::env::var_os(entered_var) else {
        return;
    };
    let Some(release) = std::env::var_os(release_var) else {
        return;
    };
    let entered = std::path::PathBuf::from(entered);
    let release = std::path::PathBuf::from(release);
    if let Err(error) = std::fs::write(&entered, b"spawned") {
        log::warn!("Could not arm lifecycle test seam {entered_var}: {error}");
        return;
    }
    let deadline = Instant::now() + Duration::from_secs(10);
    while !release.exists() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
}

/// Seconds since the tracked backend child was spawned (0 when unknown).
fn backend_uptime_s<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> u64 {
    app.try_state::<BackendState>()
        .and_then(|s| s.spawned_at.lock().ok().and_then(|g| *g))
        .map(|t| t.elapsed().as_secs())
        .unwrap_or(0)
}

/// Observe a contained child through its stable root/containment handles, or
/// health-monitor an external attachment without ever signalling it.
fn backend_child_exit<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
) -> Result<Option<BackendExit>, String> {
    let Some(state) = app.try_state::<BackendState>() else {
        return Ok(None);
    };
    let mut process = state.process.lock().map_err(|e| e.to_string())?;
    if let Some(child) = process.as_mut() {
        let mut tree = state.owned_tree.lock().map_err(|e| e.to_string())?;
        let owned = tree
            .as_mut()
            .ok_or_else(|| "tracked backend is missing its containment handle".to_string())?;
        return match crate::tools::contained_child_exit(child, owned) {
            Ok(Some(status)) => {
                *process = None;
                *tree = None;
                Ok(Some(BackendExit::from_status(status)))
            }
            Ok(None) => Ok(None),
            Err(error) => Err(format!(
                "could not clean the contained backend after observing its root: {error}"
            )),
        };
    }
    if state.attached.load(Ordering::SeqCst) {
        if attached_backend_healthy() {
            reset_attached_health(&state);
            return Ok(None);
        }
        if !attached_outage_confirmed(&state) {
            return Ok(None);
        }
        // The grace window elapsed; take one final independent sample before
        // changing ownership state. A recovered external backend is still
        // unowned and must remain attached, never signalled by PID.
        if attached_backend_healthy() {
            reset_attached_health(&state);
            return Ok(None);
        }
        return Ok(Some(BackendExit::unknown(
            "attached backend stopped responding after the health grace period",
        )));
    }
    Ok(None)
}

const ATTACHED_FAILURE_THRESHOLD: u32 = 3;

fn attached_backend_healthy() -> bool {
    crate::backend::running_backend_version(backend_port())
        .is_some_and(|version| crate::backend::same_app_version(&version))
        && crate::backend::backend_deep_healthy(backend_port())
}

fn attached_health_grace() -> Duration {
    std::env::var("OMNIVOICE_ATTACHED_FAILURE_GRACE_MS")
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok())
        .filter(|&millis| millis > 0)
        .map(Duration::from_millis)
        .unwrap_or(Duration::from_secs(6))
}

fn reset_attached_health(state: &BackendState) {
    let mut health = state
        .attached_health
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    health.failures = 0;
    health.unhealthy_since = None;
}

fn attached_outage_confirmed(state: &BackendState) -> bool {
    let now = Instant::now();
    let mut health = state
        .attached_health
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    health.failures = health.failures.saturating_add(1);
    let since = *health.unhealthy_since.get_or_insert(now);
    health.failures >= ATTACHED_FAILURE_THRESHOLD
        && now.duration_since(since) >= attached_health_grace()
}

/// How long the launch poll waits for the backend to become Ready before
/// declaring Failed. 300s in production; `OMNIVOICE_STARTUP_BUDGET_S`
/// exists for the fault-injection harness (a slow-start scenario must not
/// sleep five minutes in CI) and for support triage on pathological disks.
fn startup_budget() -> Duration {
    std::env::var("OMNIVOICE_STARTUP_BUDGET_S")
        .ok()
        .and_then(|v| v.trim().parse::<u64>().ok())
        .filter(|&s| s > 0)
        .map(Duration::from_secs)
        .unwrap_or(Duration::from_secs(300))
}

/// The supervisor's death-detection poll interval. 2s in production;
/// `OMNIVOICE_SUPERVISOR_POLL_MS` shrinks it for the harness only.
fn supervisor_poll() -> Duration {
    std::env::var("OMNIVOICE_SUPERVISOR_POLL_MS")
        .ok()
        .and_then(|v| v.trim().parse::<u64>().ok())
        .filter(|&ms| ms > 0)
        .map(Duration::from_millis)
        .unwrap_or(Duration::from_secs(2))
}

/// Drop restart timestamps older than `RESTART_WINDOW` and report whether the
/// remaining count has hit the cap. Pure so the backoff policy is unit-tested
/// without spawning real processes.
fn restart_budget_exhausted(times: &mut Vec<Instant>, now: Instant) -> bool {
    times.retain(|t| now.duration_since(*t) < RESTART_WINDOW);
    times.len() >= MAX_RESTARTS
}

/// Escalating pause before a respawn, keyed on how many restarts already
/// happened inside `RESTART_WINDOW`. The FIRST respawn stays immediate (a
/// one-off crash should self-heal fast); repeat deaths get breathing room so
/// a tight crash loop doesn't burn the whole 3-in-600s budget in seconds —
/// back-to-back torch-import storms are exactly what pushes a
/// memory-pressured machine over the edge again. Pure for unit testing.
fn restart_backoff_delay(recent_restarts: usize) -> Duration {
    match recent_restarts {
        0 => Duration::ZERO,
        1 => Duration::from_secs(5),
        _ => Duration::from_secs(15),
    }
}

/// After the backend is Ready, watch its process and respawn it on an
/// unexpected exit. Runs on the (otherwise-returning) bootstrap thread and
/// stops the instant the app is quitting so it never resurrects the backend
/// during shutdown. A desktop-spawned backend is observed through its stable
/// child handle; a pre-existing attachment is health-probed but never killed.
fn supervise_backend<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    stage_handle: &Arc<Mutex<BootstrapStage>>,
    owner: u64,
) {
    let mut restart_times: Vec<Instant> = Vec::new();
    loop {
        std::thread::sleep(supervisor_poll());
        if backend_stop_requested(app) || SUPERVISOR_OWNER.load(Ordering::SeqCst) != owner {
            return;
        }
        // Snapshot the spawn generation BEFORE observing the exit: sampled
        // after, a replacement tracked in the gap between `try_wait` and the
        // load would be baked into the snapshot and the transfer missed
        // (third-pass review find). Sampled before, any tracking that
        // happens from here on — even one whose child we are about to see
        // exit — reads as a generation change and yields.
        let observed_generation = BACKEND_SPAWN_GENERATION.load(Ordering::SeqCst);
        let backend_state = app.state::<BackendState>();
        let was_attached = backend_state.attached.load(Ordering::SeqCst);
        let mut attachment_lifecycle = None;
        let exit = match backend_child_exit(app) {
            Ok(Some(exit)) => exit,
            Ok(None) => continue, // still running
            Err(error) => {
                set_backend_kill_intended(true);
                let message = format!(
                    "VoiceStudio could not safely clean up the crashed backend: {error}"
                );
                log::error!("{message}");
                set_stage(stage_handle, BootstrapStage::Failed { message });
                return;
            }
        };
        // The exit may have raced with a shutdown that killed the child.
        if backend_stop_requested(app) {
            return;
        }
        if SUPERVISOR_OWNER.load(Ordering::SeqCst) != owner {
            return;
        }
        // A retry/clean-retry flow killed the child on purpose and owns the
        // respawn — no crash marker, and step aside so the retry's own
        // spawn_backend_and_wait claims the supervisor slot at Ready (#941).
        if backend_kill_intended() {
            log::info!("Backend exit was a deliberate replace — supervisor yielding to the retry flow");
            return;
        }
        if was_attached {
            // An unhealthy response is not proof that this unowned process
            // died. Serialize a final recovery/port sample with lifecycle
            // owners. If it recovered, keep supervising it; if it still owns
            // the port, re-arm observation and wait without signalling it or
            // landing permanently in Failed. Only a released port permits a
            // desktop-owned replacement.
            let lifecycle = backend_state
                .lifecycle
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            if backend_stop_requested(app)
                || SUPERVISOR_OWNER.load(Ordering::SeqCst) != owner
                || BACKEND_SPAWN_GENERATION.load(Ordering::SeqCst) != observed_generation
            {
                return;
            }
            if attached_backend_healthy() {
                track_attached_backend(app);
                set_stage(stage_handle, BootstrapStage::Ready);
                continue;
            }
            if crate::backend::port_in_use(backend_port()) {
                backend_state.attached.store(true, Ordering::SeqCst);
                continue;
            }
            backend_state.attached.store(false, Ordering::SeqCst);
            // Keep ownership from the final free-port observation through the
            // spawn-and-track handoff below; no internal lifecycle flow can
            // interleave and no external listener is ever signalled.
            attachment_lifecycle = Some(lifecycle);
        }
        let exit_info = exit.description.clone();
        // #941: make the death self-documenting BEFORE any restart attempt —
        // the marker (exit code/signal + stderr tail + uptime) is what turns
        // the next "Can't reach the backend" report into a diagnosable one.
        let backoff = if was_attached {
            // A health-confirmed disconnect from an unowned listener is not a
            // process crash: no fabricated crash marker/budget entry.
            Duration::ZERO
        } else {
            let is_crash = crate::crash::should_record_backend_crash(&exit);
            if is_crash {
                let uptime_s = backend_uptime_s(app);
                crate::crash::record_crash(crate::crash::marker_now(
                    &exit,
                    uptime_s,
                    crate::backend::read_error_log_tail_for_run(CRASH_STDERR_TAIL_LINES),
                ));
            } else {
                log::info!(
                    "Backend received an external Windows debugger termination ({exit_info}); restarting without a crash marker"
                );
            }
            if restart_budget_exhausted(&mut restart_times, Instant::now()) {
                let tail = crate::backend::read_error_log_tail_for_run(30);
                let msg = format!(
                    "The backend kept {} ({} times in {} min; last stop: {}) and couldn't \
                     be kept running. Use Clean & Retry, or check Settings → Logs → Backend.{}",
                    if is_crash { "crashing" } else { "being stopped externally" },
                    MAX_RESTARTS,
                    RESTART_WINDOW.as_secs() / 60,
                    exit.label(),
                    if tail.is_empty() { String::new() } else { format!("\n\nLast output:\n{tail}") },
                );
                log::error!("Backend supervisor giving up: {msg}");
                let _ = app.emit("backend-restart-failed", msg.clone());
                set_stage(stage_handle, BootstrapStage::Failed { message: msg });
                return;
            }
            // Backoff BEFORE this restart is recorded: `restart_times` was
            // just pruned, so its length is recent respawns already attempted.
            let delay = restart_backoff_delay(restart_times.len());
            restart_times.push(Instant::now());
            delay
        };
        log::warn!("Backend process exited unexpectedly ({exit_info}) — restarting it (#567)");
        emit_log(app, "starting_backend", "Backend stopped unexpectedly — restarting it automatically");
        // Frontend listens for this to show a "reconnecting" banner (the splash
        // poll has already stopped post-Ready, so the stage alone won't show).
        let _ = app.emit("backend-restarting", exit_info.clone());
        set_stage(stage_handle, BootstrapStage::StartingBackend);
        // The banner is already up, so the pause reads as "reconnecting", not
        // as a hang. Chunked so quitting (or a deliberate retry-flow kill,
        // which owns the respawn) is honored within 500 ms.
        if !backoff.is_zero() {
            log::info!(
                "Backend died {} time(s) in the last {} min — waiting {}s before respawning",
                restart_times.len(),
                RESTART_WINDOW.as_secs() / 60,
                backoff.as_secs()
            );
            let waited = Instant::now();
            while waited.elapsed() < backoff {
                if backend_stop_requested(app) {
                    return;
                }
                if SUPERVISOR_OWNER.load(Ordering::SeqCst) != owner {
                    return;
                }
                if backend_kill_intended() {
                    log::info!("Deliberate replace during restart backoff — supervisor yielding");
                    return;
                }
                // A completed Retry/Clean&Retry sets the deliberate-kill flag
                // and then `track_backend_child` CLEARS it — possibly both
                // between two of these samples, so the flag alone can be
                // missed. The durable tell is the spawn GENERATION: it bumps
                // when a replacement is tracked and never un-bumps, so it is
                // observed even if the replacement has itself already exited
                // by the time we sample. Yield promptly (not at backoff end)
                // so the replacement flow can claim the supervisor slot, and
                // never free_port() its child out from under it.
                if BACKEND_SPAWN_GENERATION.load(Ordering::SeqCst) != observed_generation {
                    log::info!(
                        "A replacement backend was tracked during restart backoff — supervisor yielding"
                    );
                    return;
                }
                std::thread::sleep(Duration::from_millis(500));
            }
        }
        // Claim the same lifecycle ownership used by bootstrap/Retry before
        // the final probe. Another flow may have completed a whole replacement
        // between our backoff samples; after this lock, probe/kill/spawn/track
        // stay atomic with respect to every other owner (#1635).
        let _lifecycle = match attachment_lifecycle {
            Some(lifecycle) => lifecycle,
            None => backend_state
                .lifecycle
                .lock()
                .unwrap_or_else(|e| e.into_inner()),
        };
        if backend_stop_requested(app) {
            return;
        }
        if SUPERVISOR_OWNER.load(Ordering::SeqCst) != owner {
            return;
        }
        if backend_kill_intended() {
            log::info!("Deliberate replace owns the backend lifecycle — supervisor yielding");
            return;
        }
        // Last look before touching the port — covers the zero-backoff first
        // respawn and a replacement completed while this supervisor waited for
        // lifecycle ownership.
        if BACKEND_SPAWN_GENERATION.load(Ordering::SeqCst) != observed_generation {
            log::info!("A replacement backend was tracked — supervisor yielding to its flow");
            return;
        }
        // Clear any orphan still holding the port before the respawn. #1223:
        // if it can't be cleared, respawning just reproduces the bind failure
        // — stop and say so rather than burning a restart attempt.
        if crate::backend::port_in_use(backend_port())
            && !crate::backend::free_port_or_report(backend_port())
        {
            set_stage(
                stage_handle,
                BootstrapStage::Failed {
                    // Wording note: every one of these must contain a phrase
                    // `BootstrapSplash.detectHints` matches ("port … in use"),
                    // because that is what turns an English Rust message into
                    // the LOCALISED `bootstrap.hint_port` the user actually
                    // reads. Pinned in frontend/src/test/portInUseHint.test.js
                    // — an earlier draft of this one said "is held by" and
                    // silently lost the translated guidance.
                    message: format!(
                        "Port {} is still in use by another application and \
                         VoiceStudio could not free it, so the backend can't \
                         restart. Quit whatever is using that port and relaunch.",
                        backend_port()
                    ),
                },
            );
            return;
        }
        spawn_and_track_backend(app, stage_handle);
        // Wait (bounded) for the respawn to become healthy. If it dies again
        // immediately, bail early so the next loop counts it toward the cap.
        let start = Instant::now();
        let mut last_step = String::new();
        while start.elapsed() < Duration::from_secs(120) {
            if backend_stop_requested(app) {
                return;
            }
            if crate::backend::backend_ready(backend_port()) {
                set_stage(stage_handle, BootstrapStage::Ready);
                let _ = app.emit("backend-restored", ());
                log::info!("Backend restarted and healthy again");
                break;
            }
            match backend_child_exit(app) {
                Ok(Some(_)) => break,
                Ok(None) => {}
                Err(error) => {
                    set_backend_kill_intended(true);
                    let message = format!(
                        "VoiceStudio could not safely clean up the restarted backend: {error}"
                    );
                    set_stage(stage_handle, BootstrapStage::Failed { message });
                    return;
                }
            }
            // Same early-bind narration as the launch poll: name the startup
            // step in the reconnecting window instead of a silent wait.
            if let Some((status, step, label)) =
                crate::backend::startup_progress(backend_port())
            {
                if status == "starting" && !step.is_empty() && step != last_step {
                    last_step = step;
                    emit_log(app, "starting_backend", &format!("Startup: {label}"));
                }
            }
            std::thread::sleep(Duration::from_millis(500));
        }
    }
}

#[tauri::command]
pub async fn clean_and_retry_bootstrap(app: tauri::AppHandle) {
    let state = app.state::<BootstrapState>();
    let failure_stage = state.stage.clone();
    let worker_app = app.clone();
    let worker_stage = state.stage.clone();
    let worker_logs = state.logs.clone();
    let joined = tauri::async_runtime::spawn_blocking(move || {
        // env_root honors the setup-screen choice (portable / custom env dir),
        // so clean-retry removes the venv the bootstrap actually uses. Stop,
        // recursive deletion, and recovery all stay in this retained blocking
        // task: the UI thread never waits on process teardown or filesystem I/O,
        // and navigation cannot drop the respawn handoff.
        let project_dir = crate::setup::env_root(&worker_app).join("project");
        match with_backend_stopped(&worker_app, || {
            if project_dir.is_dir() {
                log::info!("Clean retry: removing {}", project_dir.display());
                let _ = fs::remove_dir_all(&project_dir);
            }
        }) {
            Ok(()) => respawn_backend(worker_app, worker_stage, worker_logs),
            Err(message) => set_stage(&worker_stage, BootstrapStage::Failed { message }),
        }
    })
    .await;
    if let Err(error) = joined {
        log::error!("Clean & Retry task failed to join: {error}");
        set_stage(
            &failure_stage,
            BootstrapStage::Failed {
                message: "Clean & Retry task failed unexpectedly".to_string(),
            },
        );
    }
}

// ── Venv bootstrap ────────────────────────────────────────────────────────

pub fn venv_python_path(venv: &Path) -> PathBuf {
    if cfg!(windows) {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python")
    }
}

/// Recursive directory copy that skips `__pycache__` and any dotfile dirs.
pub fn copy_dir_recursive(src: &Path, dst: &Path) -> io::Result<()> {
    fs::create_dir_all(dst)?;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let src_path = entry.path();
        let file_name = entry.file_name();
        let name_str = file_name.to_string_lossy();
        if src_path.is_dir() {
            if name_str == "__pycache__" || name_str.starts_with('.') {
                continue;
            }
            copy_dir_recursive(&src_path, &dst.join(&file_name))?;
        } else if name_str.ends_with(".pyc") {
            continue;
        } else {
            fs::copy(&src_path, &dst.join(&file_name))?;
        }
    }
    Ok(())
}

/// Install the production SPA beside `backend/`, where the Python server's
/// static-file mount resolves it for Network Sharing clients.
fn sync_packaged_frontend(resource_root: &Path, project_dir: &Path) -> io::Result<()> {
    let source = resource_root.join("frontend").join("dist");
    if !source.join("index.html").is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "bundled frontend is missing index.html",
        ));
    }

    let destination = project_dir.join("frontend").join("dist");
    let frontend_dir = destination.parent().expect("frontend dist has a parent");
    let staging = frontend_dir.join(".dist-staging");
    let backup = frontend_dir.join(".dist-backup");
    fs::create_dir_all(frontend_dir)?;
    if staging.exists() {
        fs::remove_dir_all(&staging)?;
    }
    // A previous process may have died after moving the live shell aside but
    // before installing staging. Restore the only known-good SPA before doing
    // any new work; never discard that recovery copy merely because startup
    // retried.
    if !destination.exists() && backup.exists() {
        fs::rename(&backup, &destination)?;
    }
    if let Err(error) = copy_dir_recursive(&source, &staging) {
        let _ = fs::remove_dir_all(&staging);
        return Err(error);
    }

    if destination.exists() {
        if backup.exists() {
            // An interrupted cleanup can leave an incomplete backup. Remove
            // it before touching the known-working destination; if cleanup
            // fails, abort with the live shell still intact.
            fs::remove_dir_all(&backup)?;
        }
        fs::rename(&destination, &backup)?;
    }
    if let Err(error) = fs::rename(&staging, &destination) {
        if backup.exists() {
            let _ = fs::rename(&backup, &destination);
        }
        let _ = fs::remove_dir_all(&staging);
        return Err(error);
    }
    if backup.exists() {
        fs::remove_dir_all(backup)?;
    }
    Ok(())
}

/// Refresh `pyproject.toml` + `uv.lock` in the project dir from the bundled
/// resources, so an upgraded app never runs freshly-synced backend code against
/// the stale dependency manifests from when the venv was first created (#307 —
/// a venv predating scalar-fastapi's addition crashed main.py on import).
/// Returns true when the lockfile content changed (or the project had none):
/// the signal that the venv may be missing newly added dependencies and needs
/// a `uv sync`.
fn refresh_project_manifests(resource_dir: &Path, project_dir: &Path) -> bool {
    let flat = resource_dir.to_path_buf();
    let up2 = resource_dir.join("_up_").join("_up_");
    let res_root = if flat.join("pyproject.toml").is_file() { flat } else { up2 };
    let res_pyproject = res_root.join("pyproject.toml");
    let res_uvlock = res_root.join("uv.lock");
    if res_pyproject.is_file() {
        if let Err(e) = fs::copy(&res_pyproject, project_dir.join("pyproject.toml")) {
            log::warn!("Could not refresh pyproject.toml from bundle: {}", e);
        }
    }
    // Keep the shipped CHANGELOG.md current too — the backend's
    // GET /api/settings/changelog (Settings → Updates "What's new" viewer)
    // reads it from the project root, so an upgraded app must not show the
    // notes from whenever the install was first created. Best-effort.
    let res_changelog = res_root.join("CHANGELOG.md");
    if res_changelog.is_file() {
        if let Err(e) = fs::copy(&res_changelog, project_dir.join("CHANGELOG.md")) {
            log::warn!("Could not refresh CHANGELOG.md from bundle: {}", e);
        }
    }
    if !res_uvlock.is_file() {
        return false;
    }
    let project_lock = project_dir.join("uv.lock");
    let lock_changed = match (fs::read(&res_uvlock), fs::read(&project_lock)) {
        (Ok(bundled), Ok(existing)) => bundled != existing,
        (Ok(_), Err(_)) => true, // project has no lock yet — treat as drift
        (Err(e), _) => {
            log::warn!("Could not read bundled uv.lock: {}", e);
            return false;
        }
    };
    if lock_changed {
        if let Err(e) = fs::copy(&res_uvlock, &project_lock) {
            log::warn!("Could not refresh uv.lock from bundle: {}", e);
            return false; // don't sync against a lock we failed to refresh
        }
    }
    lock_changed
}

/// Dev-mode fallback: running from the source tree (`bun run dev`).
pub fn find_dev_project_root() -> Option<PathBuf> {
    let candidates = [
        PathBuf::from("../../"),       // from frontend/src-tauri
        PathBuf::from("."),            // from project root
        PathBuf::from(".."),           // from frontend/
    ];
    for c in &candidates {
        if c.join("backend/main.py").is_file() {
            return Some(c.clone());
        }
    }
    None
}

// ── #1770: attach-handshake code fingerprint ────────────────────────────────
//
// `same_app_version` (backend.rs) only proves the running backend's *version
// string* matches this build's. That's not the same as its *code* matching:
// per the Versioning convention, `main` holds ONE version string for an
// entire release cycle, so every commit merged during that cycle — including
// the one that fixed #1770 itself — reports the same `app_version`. A
// backend that answers with today's version string can still be running
// weeks-old code. The fingerprint below closes that gap: it's a content
// digest of the backend's actual `.py` sources, passed to a spawned child as
// `OMNIVOICE_BUILD_FINGERPRINT` and echoed back via `GET /system/info`
// (`code_fingerprint`), so the attach handshake can compare "the code this
// build ships" against "the code the already-running process loaded" instead
// of trusting a coarse version string alone.

/// Recursively collect every `.py` file under `dir`, as (label, path) pairs
/// where `label` is `"<root_label>/<path relative to dir>"` with `/`
/// separators (stable across platforms). Skips `__pycache__` and dotdirs
/// (`.venv`, `.pytest_cache`, …) so dev mode — which executes `backend/`
/// directly out of the live source tree and litters it with bytecode caches
/// and test artifacts — doesn't shift the fingerprint without any actual
/// code change.
fn collect_py_files(dir: &Path, base: &Path, root_label: &str, out: &mut Vec<(String, PathBuf)>) -> io::Result<()> {
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        let file_type = entry.file_type()?;
        if file_type.is_dir() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if name == "__pycache__" || name.starts_with('.') {
                continue;
            }
            collect_py_files(&path, base, root_label, out)?;
        } else if file_type.is_file() && path.extension().and_then(|e| e.to_str()) == Some("py") {
            let rel = path.strip_prefix(base).unwrap_or(&path);
            out.push((format!("{}/{}", root_label, rel.to_string_lossy().replace('\\', "/")), path));
        }
    }
    Ok(())
}

/// Content fingerprint of one or more Python source directories (each
/// hashed under its own directory-name label so `backend/x.py` and
/// `omnivoice/x.py` can't collide). `None` if no `.py` files were found or
/// any of them couldn't be read — an unreadable/empty source location means
/// we can't vouch for a fingerprint either way.
///
/// Deterministic within a single process (files sorted by label before
/// hashing) but the digest is NOT a portable content hash — `DefaultHasher`
/// makes no cross-version stability guarantee. That's fine here: every
/// fingerprint that's ever compared was produced by a VoiceStudio process
/// hashing on its own toolchain, so "same code -> same digest" only ever
/// needs to hold within one build, never across arbitrary Rust versions.
/// Pure (no `AppHandle`) and unit-tested directly with tempdirs.
pub fn hash_python_sources(dirs: &[PathBuf]) -> Option<String> {
    let mut files: Vec<(String, PathBuf)> = Vec::new();
    for dir in dirs {
        let root_label = dir.file_name()?.to_string_lossy().into_owned();
        collect_py_files(dir, dir, &root_label, &mut files).ok()?;
    }
    if files.is_empty() {
        return None;
    }
    files.sort_by(|a, b| a.0.cmp(&b.0));
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    for (label, path) in &files {
        std::hash::Hash::hash(label, &mut hasher);
        let bytes = fs::read(path).ok()?;
        std::hash::Hash::hash(&bytes, &mut hasher);
    }
    Some(format!("{:016x}", std::hash::Hasher::finish(&hasher)))
}

static OWN_CODE_FINGERPRINT: std::sync::OnceLock<Option<String>> = std::sync::OnceLock::new();

/// Our own build's code fingerprint — computed once per process (the source
/// location doesn't change mid-session) from wherever `ensure_venv_ready`
/// would sync `backend/`/`omnivoice/` from: the live dev source tree under
/// `bun run tauri dev`, or the packaged bundle's resource dir otherwise.
/// `spawn_backend` passes this to the child as `OMNIVOICE_BUILD_FINGERPRINT`;
/// `prepare_backend_launch` compares it against what an already-running
/// backend reports. `None` when neither source location resolves — callers
/// must then skip fingerprint enforcement (see
/// `backend::code_fingerprint_is_current`), not treat every attach as stale.
pub fn own_backend_code_fingerprint<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> Option<String> {
    OWN_CODE_FINGERPRINT
        .get_or_init(|| {
            if let Some(dev_root) = find_dev_project_root() {
                let dirs: Vec<PathBuf> = [dev_root.join("backend"), dev_root.join("omnivoice")]
                    .into_iter()
                    .filter(|d| d.is_dir())
                    .collect();
                if !dirs.is_empty() {
                    return hash_python_sources(&dirs);
                }
            }
            let res = app.path().resource_dir().ok()?;
            let flat = res.clone();
            let up2 = res.join("_up_").join("_up_");
            let res_root = if flat.join("pyproject.toml").is_file() { flat } else { up2 };
            let dirs: Vec<PathBuf> = [res_root.join("backend"), res_root.join("omnivoice")]
                .into_iter()
                .filter(|d| d.is_dir())
                .collect();
            hash_python_sources(&dirs)
        })
        .clone()
}

// ── plan-03 (#130): restricted-network bootstrap resilience ────────────────

/// gh-proxy mirror for python-build-standalone, used as a fallback when the
/// default GitHub releases host is blocked/unresolvable (#60). Points
/// UV_PYTHON_INSTALL_MIRROR at the releases-download base behind the proxy.
const PY_INSTALL_MIRROR: &str =
    "https://gh-proxy.com/https://github.com/astral-sh/python-build-standalone/releases/download";

/// Shown when every managed-Python strategy AND the system-Python fallback fail
/// — actionable remediation instead of a raw `uv` exit code (#130 step 5).
const BOOTSTRAP_REMEDIATION: &str =
    "First-run setup couldn't download Python — your network may be blocking GitHub. \
Fix: install Python 3.11+ from https://www.python.org/downloads/ (tick \"Add to PATH\"), \
then relaunch — VoiceStudio will use your system Python. Advanced: set \
UV_PYTHON_INSTALL_MIRROR to a reachable mirror (see docs/install/troubleshooting.md).";

/// #889: PyTorch stopped shipping macOS x86_64 wheels after 2.2.x, and the
/// locked dependency set needs a far newer torch (transformers 5.x requires
/// ≥2.6) — so `uv sync` can never resolve on an Intel Mac and the local
/// backend is unsupported there. Surfaced *before* any venv create/sync so
/// Intel-Mac users see this immediately instead of a raw resolver error after
/// minutes of downloads. Deliberately NOT checked when a healthy venv already
/// exists, so any pre-torch-bump install that still works keeps working.
const INTEL_MAC_UNSUPPORTED_MSG: &str =
    "Intel Macs can't run the local AI backend — PyTorch no longer ships Intel-Mac (macOS x86_64) \
builds, so the Python environment can't be installed on this machine. The app UI works, but local \
voice generation is unavailable here. Options: point the app at a remote backend running on \
another machine (Settings → Sharing → Remote backend), or use an Apple Silicon Mac / Windows / \
Linux. See docs/install/macos.md (#889).";

/// True on macOS x86_64 builds (#889). `cfg!` (not `#[cfg]`) keeps the guard
/// compiled — and the message testable — on every platform.
fn intel_mac_backend_unsupported() -> bool {
    cfg!(all(target_os = "macos", target_arch = "x86_64"))
}

/// Strip the bundled-runtime Python env vars before spawning any `uv`/venv/pip
/// or venv-python subprocess (#144). On the Linux AppImage, the bundled runtime
/// exports PYTHONHOME / PYTHONPATH (and sometimes LD_LIBRARY_PATH) pointing at
/// the AppImage's *own* bundled Python. Those leak into the `uv` build
/// subprocess, so the freshly-built managed interpreter resolves its stdlib
/// against the wrong (AppImage) Python and dies with
/// `ModuleNotFoundError: No module named 'encodings'` while compiling a
/// transitive dep (e.g. dora-search/demucs) — surfacing downstream as
/// "Backend process exited (never started)". This mirrors the same scrub the
/// backend spawn already does in `backend.rs` before launching uvicorn.
///
/// Safe on every platform: these vars are normally unset on macOS/Windows, and
/// `env_remove` on an unset var is a no-op — so there's no cross-platform
/// divergence in default behavior.
fn scrub_python_env(cmd: &mut Command) {
    cmd.env_remove("PYTHONHOME")
        .env_remove("PYTHONPATH")
        .env_remove("LD_LIBRARY_PATH");
}

/// Longer timeouts + more retries so a slow/flaky mirror or PyPI doesn't kill
/// the first-run install on its first hiccup (#130 step 2).
fn apply_uv_http_env(cmd: &mut Command) {
    cmd.env("UV_HTTP_TIMEOUT", "120")
        .env("UV_HTTP_CONNECT_TIMEOUT", "30")
        .env("UV_HTTP_RETRIES", "5");
}

/// The one env applicator every `uv` invocation must go through: HTTP
/// resilience (above) + volume co-location. The latter pins UV_CACHE_DIR /
/// UV_PYTHON_INSTALL_DIR under the env root when the install is rooted on a
/// different volume than uv's default cache (D:-drive installs / portable
/// mode) — otherwise every wheel is downloaded+unpacked on the system drive
/// and then cross-volume *copied* into the venv, silently requiring the full
/// install size on C: and ENOSPC-ing installs the user deliberately pointed
/// at another drive. See `setup::uv_env_overrides_for` for the exact rules.
fn apply_uv_env<R: tauri::Runtime>(app: &tauri::AppHandle<R>, cmd: &mut Command) {
    apply_uv_http_env(cmd);
    for (k, v) in crate::setup::uv_env_overrides(app) {
        cmd.env(k, v);
    }
}

/// `<app_data>/wheels` — a local wheel-drop dir uv installs from via
/// `--find-links`. When a huge wheel can't be pulled on a restricted network
/// (the ~2.5 GB cu128 torch wheel from download.pytorch.org — #569), the user
/// downloads the matching wheel, drops it here, and a retry picks it up.
/// Created so the path always exists to name in the error/docs. It lives under
/// `app_data` (not `project/`), so it survives Clean & Retry.
///
/// Takes the already-resolved env root as a plain path (#1783) rather than
/// re-deriving it from `env_root(app)` — `ensure_venv_ready` may have
/// redirected to an ASCII-safe root, and this must agree with wherever
/// `uv sync` actually runs, or `UV_FIND_LINKS` and the error text naming
/// this path would point somewhere the user never sees.
fn wheels_drop_dir(app_data: &Path) -> PathBuf {
    let dir = app_data.join("wheels");
    let _ = fs::create_dir_all(&dir);
    dir
}

/// True when a `uv sync` failure tail looks like the CUDA torch wheel download
/// failing (#569). Lets us give torch-specific guidance instead of the generic
/// "set a PyPI mirror" advice — which can't redirect the explicit, *named*
/// pytorch-cuda index anyway (uv 0.11 rejects index-name override values, and
/// `--frozen` pins the exact download.pytorch.org wheel URLs).
fn sync_failure_is_torch_download(tail: &str) -> bool {
    let low = tail.to_lowercase();
    low.contains("download.pytorch.org")
        || low.contains("download-r2.pytorch.org")
        || low.contains("pytorch.org/whl")
        || (low.contains("torch") && (low.contains("failed to download") || low.contains("failed to fetch")))
}

/// Default PyTorch ROCm wheel index for the opt-in AMD path (#124).
/// ROCm 6.4, not 6.2: the app's pinned `torch==2.8.0` (pyproject.toml) has no
/// build on the rocm6.2 index (it tops out at 2.5.1), so that index silently
/// failed the reinstall and left the default CUDA build in place — which runs
/// on CPU on an AMD GPU (#972). rocm6.4 carries a matching 2.8.0 build.
/// Overridable via OMNIVOICE_TORCH_INDEX (e.g. a `--find-links` URL for
/// distro-matched ROCm builds torch's own index doesn't carry).
const ROCM_TORCH_INDEX: &str = "https://download.pytorch.org/whl/rocm6.4";

/// Args for the routine update-drift sync (#307 path) — the one that runs on
/// every app update when `uv.lock` changed. `--inexact` is the fix for #1029:
/// plain `uv sync` UNINSTALLS every package not in the lockfile, which
/// silently deleted user-pip-installed optional engines (voxcpm, kittentts —
/// packages the app's own Model Catalogue → Engines hints tell users to install
/// into this venv) on every single update. `--inexact` still installs/
/// upgrades everything the lockfile demands — locked deps stay exactly
/// correct — it just stops removing extras the user added on purpose.
///
/// Deliberately NOT applied to the repair sync (`repair_sync_args`): repair
/// runs when the venv is *broken*, and a user-installed extra is a plausible
/// cause — healing must restore the known-good locked state, extras
/// included-out. An engine lost to a repair is re-installable; a venv that
/// repair can't actually repair is a support thread.
const DRIFT_SYNC_ARGS: [&str; 5] = ["sync", "--frozen", "--inexact", "--no-dev", "--verbose"];

/// Exact-sync args for the venv-repair path — see `DRIFT_SYNC_ARGS` for why
/// repair stays exact while the update-drift sync preserves user extras.
const REPAIR_SYNC_ARGS_LOCKED: [&str; 4] = ["sync", "--frozen", "--no-dev", "--verbose"];
const REPAIR_SYNC_ARGS_UNLOCKED: [&str; 3] = ["sync", "--no-dev", "--verbose"];

/// `uv pip install` args that replace the default CUDA torch build with the AMD
/// ROCm wheel (#124). Opt-in (gated on OMNIVOICE_TORCH_VARIANT=rocm by the
/// caller); the detection side (`get_best_device`) already routes ROCm through
/// `torch.cuda`, so installing the ROCm wheel is all that's needed.
fn rocm_torch_reinstall_args(rocm_index_url: &str) -> Vec<String> {
    // Keep in sync with [tool.uv.constraint-dependencies] in pyproject.toml
    vec![
        "pip".into(), "install".into(), "--reinstall".into(),
        "torch==2.8.0".into(), "torchaudio==2.8.0".into(), "torchvision==0.23.0".into(),
        "--index-url".into(), rocm_index_url.into(),
    ]
}

/// Whether the user opted into the AMD ROCm torch build — via the
/// OMNIVOICE_TORCH_VARIANT env var (power users, takes precedence) or the
/// setup screen's Compute choice persisted in config (`configured_variant`).
/// Default (unset/"auto") → None (CUDA/CPU path unchanged). Returns the ROCm
/// wheel index to use when enabled.
fn rocm_opt_in(configured_variant: &str) -> Option<String> {
    let variant = std::env::var("OMNIVOICE_TORCH_VARIANT")
        .unwrap_or_else(|_| configured_variant.to_string());
    if !variant.eq_ignore_ascii_case("rocm") {
        return None;
    }
    Some(std::env::var("OMNIVOICE_TORCH_INDEX").unwrap_or_else(|_| ROCM_TORCH_INDEX.to_string()))
}

// ── #314: broken-venv detection + self-heal ────────────────────────────────

/// Cheap structural validity check for an existing venv — no subprocess
/// spawned. Returns a human-readable reason when the venv can never work and
/// must be rebuilt:
///   - `pyvenv.cfg` missing (interrupted creation / half-deleted dir — the
///     CPython venv launcher then exits 106 with "No pyvenv.cfg file"),
///   - the python executable missing entirely, or
///   - on Unix, `bin/python` left as a dangling symlink because the base
///     interpreter it was created from was removed.
///
/// Returns `None` both for a healthy venv (which must never be touched) and
/// for a venv path that doesn't exist at all (the first-run creation path
/// owns that case).
pub fn venv_structural_problem(venv_dir: &Path) -> Option<String> {
    if venv_dir.symlink_metadata().is_err() {
        return None; // no venv at all — first-run creation handles it
    }
    if !venv_dir.is_dir() {
        return Some(".venv exists but is not a directory".to_string());
    }
    if !venv_dir.join("pyvenv.cfg").is_file() {
        return Some("pyvenv.cfg is missing".to_string());
    }
    let py = venv_python_path(venv_dir);
    if py.symlink_metadata().is_err() {
        return Some(format!("python executable is missing ({})", py.display()));
    }
    // `is_file()` follows symlinks, so a `bin/python` whose target interpreter
    // was uninstalled (dangling symlink) fails here even though the
    // `symlink_metadata()` existence check above passed.
    if !py.is_file() {
        return Some(format!("python executable is a dangling symlink ({})", py.display()));
    }
    None
}

/// Remove a structurally broken venv so the creation path can rebuild it.
/// Only `.venv` itself is touched — project manifests, backend sources, and
/// all user data (`omnivoice_data/`) stay in place. If the directory can't be
/// deleted outright (e.g. a locked file on Windows), rename it aside instead
/// so `uv venv` still finds a clean path. Returns true when the original path
/// is gone.
fn quarantine_broken_venv(venv_dir: &Path) -> bool {
    if venv_dir.symlink_metadata().is_err() {
        return true; // already gone — nothing to do
    }
    match fs::remove_dir_all(venv_dir) {
        Ok(()) => {
            log::info!("Removed broken venv {} (#314)", venv_dir.display());
            true
        }
        Err(e) => {
            log::warn!(
                "remove_dir_all({}) failed: {} — renaming the broken venv aside instead",
                venv_dir.display(),
                e
            );
            let ts = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let quarantine = venv_dir.with_file_name(format!(".venv.broken-{}", ts));
            match fs::rename(venv_dir, &quarantine) {
                Ok(()) => {
                    log::info!("Renamed broken venv to {} (#314)", quarantine.display());
                    true
                }
                Err(e2) => {
                    log::error!("Could not rename broken venv aside: {}", e2);
                    false
                }
            }
        }
    }
}

/// Whether a dead backend process looks like it failed because the venv
/// itself is structurally broken — either the CPython venv launcher's
/// "No pyvenv.cfg file" + exit 106 (`RC_NO_PYVENV_CFG`), OR a relocated/copied/
/// restored venv whose interpreter can't bootstrap its own stdlib and aborts
/// very early with "No module named 'encodings'" (exit 1). Both are
/// unrunnable-interpreter cases that `uv sync` cannot fix — only a venv rebuild
/// can — so both route into the rebuild-once self-heal. Matches the message in
/// the captured stderr tail or the exit code in the `ExitStatus` display
/// ("exit code: 106" on Windows, "exit status: 106" on Unix). Kept deliberately
/// narrow (full quoted phrases) so an ordinary backend crash — or an app-level
/// import error of some 'encodings'-named package — never triggers a rebuild.
pub fn backend_exit_indicates_broken_venv(exit_info: &str, err_tail: &str) -> bool {
    err_tail.contains("No pyvenv.cfg file")
        || err_tail.contains("No module named 'encodings'")
        || exit_info.trim_end().ends_with(": 106")
}

// ── #1783: non-ASCII venv-path `.pth` crash detection ──────────────────────

/// True when a dead backend's stderr tail is Python 3.11's `site` module
/// dying on a `.pth` file it can't decode in the active (non-UTF-8) Windows
/// code page — the exact crash from #1771/#1783:
/// ```text
/// Fatal Python error: init_import_site: Failed to import the site module
///   File "<frozen site>", line 188, in addpackage
/// UnicodeDecodeError: 'gbk' codec can't decode byte 0x80 in position 11...
/// ```
/// Both phrases are required (narrow, quoted-phrase match, same discipline as
/// [`backend_exit_indicates_broken_venv`]) so an ordinary `UnicodeDecodeError`
/// raised by app code — unrelated to interpreter startup — never matches.
pub fn backend_exit_indicates_nonascii_pth_crash(err_tail: &str) -> bool {
    err_tail.contains("init_import_site") && err_tail.contains("UnicodeDecodeError")
}

/// #1783: unlike the broken-venv signature, rebuilding the SAME venv at the
/// SAME path can't fix this on its own — the path itself is the problem.
/// `ensure_venv_ready`'s `resolve_venv_root` already redirects away from a
/// non-ASCII path whenever an ASCII-safe alternative exists, so this
/// specific message only fires when that redirect couldn't help. It's also
/// the fallback if a spawned backend somehow still hits this crash despite
/// the pre-spawn probe in `ensure_venv_ready` (defense in depth, not the
/// primary path).
///
/// The remedy must be actionable from wherever the user actually sees this
/// message AND correct for whichever install mode they're actually in —
/// three separate bot findings on this exact PR, each "the text names an
/// action that doesn't work" for some reachable state:
///   - greptile P1: an earlier draft pointed at the first-run setup screen's
///     "Change…" picker, but this message is shown via
///     `BootstrapStage::Failed`, which only offers Retry / Clean & Retry
///     (neither changes where the environment is stored, so both fail
///     identically — the exact defect #1787 exists to catch).
///   - CodeRabbit: `fsutil 8dot3name` is per-volume and only affects
///     directories created AFTER the change, not the already-existing one
///     this failure is about — recommending "run this and retry" often
///     fixes nothing.
///   - greptile P1 (3rd instance): the previous fix told EVERY user to set
///     `env_dir` in `config.json` — but `setup::env_root` checks
///     `cfg.install_mode == "portable"` FIRST and returns
///     `portable_base().join("env")` without ever consulting `env_dir` (see
///     `env_root`'s body). A portable install following that advice hits
///     the identical crash on relaunch. `is_portable` is threaded in from
///     each call site (a config read, not free state) so the two mutually
///     exclusive remedies are never both shown, and neither is shown to the
///     mode it doesn't apply to.
///   - Self-caught while fixing the above (4th instance, same class): the
///     managed branch used to add "Still on first-run setup instead? Use
///     the Change… button…" — but BOTH call sites of this function are only
///     reachable once `!setup::is_first_run(app)` (`launch_backend_and_wait`
///     gates `ensure_venv_ready`/`spawn_backend_until_ready` behind
///     `first_run_gate`, which parks in `AwaitingSetup` — the FirstRunSetup
///     screen — instead of calling either). This message can therefore
///     NEVER be showing while that screen is; the clause described a state
///     that cannot coexist with its own display. Removed rather than kept
///     as "harmless extra info" — the whole point of this history is that
///     an unreachable action in a failure message is never harmless.
fn nonascii_pth_crash_msg(is_portable: bool) -> String {
    let cause = "The backend's Python environment lives at a path Windows' active \
language/region setting can't decode (commonly a non-English Windows username), so the \
interpreter crashes on startup before VoiceStudio's code ever runs — this can happen if \
Windows' 8.3 short-filename support is off on your system drive, or if this folder \
already existed before this fix shipped. \"Retry\" and \"Clean & Retry\" won't fix it — \
they don't change where the environment is stored.";
    let remedy = if is_portable {
        "Fix: quit VoiceStudio, then move the whole VoiceStudio folder (the app plus its \
\"OmniVoiceStudio-Data\" folder) to a location whose path uses only English letters and \
numbers (e.g. C:\\VoiceStudio), and run it from there — portable installs ignore \
`env_dir` in config.json entirely, so editing that file does nothing here. If you'd \
rather not move the whole folder, create a \"portable.path\" text file beside the app \
containing one line, an absolute ASCII-only path for the data folder (e.g. \
C:\\VoiceStudio\\Data), and relaunch."
    } else {
        "Fix: quit VoiceStudio, open (creating it if missing) \
%LOCALAPPDATA%\\com.debpalash.omnivoice-studio\\config.json in a text editor, add \
\"env_dir\": \"C:/VoiceStudio/env\" (any path using only English letters/numbers works — \
forward slashes are fine on Windows), save, and relaunch. This only moves the Python \
environment, not your voices/projects."
    };
    format!("{cause} {remedy} See docs/install/troubleshooting.md (#1783).")
}

/// What `ensure_venv_ready` should do about where the venv lives, decided by
/// [`resolve_venv_root`].
#[derive(Debug, PartialEq, Eq)]
enum VenvRootDecision {
    /// Use `true_root` exactly as before — nothing here relocates a venv
    /// that either doesn't need it or wouldn't be helped by it.
    Keep,
    /// Build/verify at this ASCII-safe root instead. The old location at
    /// `true_root` is left untouched — never deleted by this decision.
    Redirect(PathBuf),
    /// `true_root` has the #1783 crash signature AND redirecting wouldn't
    /// change anything (8.3 short names unavailable) — fail with the
    /// specific diagnosis instead of cascading into an unrelated repair.
    Unfixable,
}

/// #1783: decide whether `ensure_venv_ready` should trust/build the venv at
/// `true_root` or redirect to `ascii_safe_root` (`setup::ascii_safe_dir`'s
/// output for `true_root`). Pure — every input is an already-observed fact —
/// so this is unit-testable without a real venv, interpreter, or AppHandle.
///
/// The controlling invariant, matching the existing #314 self-heal's
/// `venv_rebuild_justified` (a venv that probes healthy is never destroyed):
/// **a venv that exists and doesn't show the #1783 signature is never
/// relocated**, no matter how it got a non-ASCII path or what `probe_stderr`
/// otherwise says. Only two situations redirect:
///   1. `venv_exists` is false — nothing usable is at `true_root` (a fresh
///      install, or one just quarantined by the #314 structural check) — so
///      building fresh there instead of at a byte-invalid path costs nothing.
///   2. `venv_exists` is true and `probe_stderr` carries the exact #1783
///      crash — the venv can never start where it is, so leaving it in place
///      (untouched, recoverable by hand) and building fresh elsewhere is the
///      only way forward.
/// Any other probe outcome — healthy (`Some("")`), a different failure, or
/// `None` (couldn't even spawn) — keeps `true_root`, exactly as before this
/// fix existed, and falls through to the pre-existing uvicorn/pkg_resources/
/// omnivoice repair logic unchanged.
///
/// One more case fails fast rather than falling to `Keep` (greptile P1): case
/// 1 above with `redirect_helps` false — a fresh install whose non-ASCII
/// `true_root` has no ASCII-safe alternative (8.3 short names unavailable).
/// `Keep` there used to mean "commit to a multi-GB `uv sync` at a path
/// already known unusable, then diagnose the identical crash afterwards" —
/// the information needed to fail fast was already available before the
/// download started. `is_ascii_path(true_root)` is checked explicitly here
/// (not inferred from `redirect_helps`, which is also false for the
/// perfectly-fine ASCII case) so an ASCII `true_root` still takes the
/// zero-cost, zero-behaviour-change `Keep` path.
fn resolve_venv_root(
    true_root: &Path,
    ascii_safe_root: &Path,
    venv_exists: bool,
    probe_stderr: Option<&str>,
) -> VenvRootDecision {
    let redirect_helps = ascii_safe_root != true_root;
    if venv_exists {
        match probe_stderr {
            Some(stderr) if backend_exit_indicates_nonascii_pth_crash(stderr) => {
                if redirect_helps {
                    VenvRootDecision::Redirect(ascii_safe_root.to_path_buf())
                } else {
                    VenvRootDecision::Unfixable
                }
            }
            _ => VenvRootDecision::Keep,
        }
    } else if redirect_helps {
        VenvRootDecision::Redirect(ascii_safe_root.to_path_buf())
    } else if !crate::setup::is_ascii_path(true_root) {
        // greptile P1: a fresh install at a non-ASCII path whose short-name
        // redirect genuinely failed (8.3 generation unavailable) used to
        // fall through to `Keep` here — which doesn't mean "this path is
        // fine", it means "nothing could be done about it". Committing to a
        // multi-GB `uv sync` at a path already known unusable just moves the
        // same diagnosis to AFTER the wait instead of skipping it. Fail fast
        // with the same conclusion the existing-venv branch above already
        // reaches for the identical unfixable case, before anything
        // downloads. An ASCII `true_root` never reaches this arm —
        // `redirect_helps` is trivially false for it too, but `is_ascii_path`
        // is checked explicitly rather than inferred from that, so this
        // stays correct even if `resolve_venv_root` is ever called directly
        // without the `ensure_venv_ready` pre-check that normally shields it.
        VenvRootDecision::Unfixable
    } else {
        VenvRootDecision::Keep
    }
}

/// Data-safe guard for the destructive half of the #314 self-heal
/// (feat/safe-updates): an exit-*signature* match alone is text matching on a
/// stderr tail — before it is allowed to delete a multi-GB venv, the venv must
/// be *confirmed* broken by direct evidence:
///
/// - a structural problem found by [`venv_structural_problem`] (missing
///   pyvenv.cfg / missing or dangling python) is definitive → rebuild;
/// - otherwise the venv's own interpreter is probed
///   ([`venv_interpreter_probe`]): if it provably starts and imports its
///   stdlib (`Some(true)`), the venv is NOT the problem — deleting it would
///   destroy a working ~6 GB install to "fix" an unrelated crash, so the
///   rebuild is refused and the real error is surfaced instead;
/// - a failed probe (`Some(false)`) or one that couldn't even spawn (`None`)
///   confirms the interpreter is unrunnable → rebuild.
pub fn venv_rebuild_justified(
    structural_problem: Option<&str>,
    interpreter_probe: Option<bool>,
) -> bool {
    if structural_problem.is_some() {
        return true;
    }
    !matches!(interpreter_probe, Some(true))
}

/// Run the venv's python directly to check the interpreter can bootstrap its
/// stdlib. `Some(true)` = healthy, `Some(false)` = starts but fails (e.g. the
/// venv launcher's exit 106, or the 'encodings' bootstrap abort), `None` = the
/// binary couldn't be spawned at all. Env is scrubbed (#144) so an AppImage's
/// bundled-Python vars can't fake a failure on a healthy venv.
fn venv_interpreter_probe(venv_py: &Path) -> Option<bool> {
    let mut cmd = Command::new(venv_py);
    scrub_python_env(&mut cmd);
    crate::tools::no_window(&mut cmd); // Windows: no flashing console for the probe
    cmd.args(["-c", "import encodings"])
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    match cmd.status() {
        Ok(status) => Some(status.success()),
        Err(_) => None,
    }
}

/// Like [`venv_interpreter_probe`] but captures stderr instead of discarding
/// it, so a failure can be pattern-matched (#1783's non-ASCII-path `.pth`
/// crash) rather than only reported healthy/unhealthy. `None` only when the
/// binary couldn't be spawned at all (same "leave it to the existing repair
/// path" case `venv_interpreter_probe`'s `None` gets elsewhere) — `Some("")`
/// is a clean, healthy exit. Deliberately NOT `-S` (skip-site): the crash
/// this exists to detect happens inside `site.py` during NORMAL interpreter
/// startup, exactly like the real backend spawn, so a skip-site probe would
/// never reproduce it.
fn venv_interpreter_probe_stderr(venv_py: &Path) -> Option<String> {
    let mut cmd = Command::new(venv_py);
    scrub_python_env(&mut cmd);
    crate::tools::no_window(&mut cmd);
    cmd.args(["-c", "import encodings"]).stdout(Stdio::null()).stderr(Stdio::piped());
    cmd.output().ok().map(|o| String::from_utf8_lossy(&o.stderr).into_owned())
}

// ── Linux/Windows: cuDNN 8 compat side-load ────────────────────────────────
//
// This used to live ONLY in scripts/setup.py, run via `bun run setup:api`
// (dev loop only). Neither `scripts/` nor `setup.py` is bundled as a Tauri
// resource (see tauri.conf.json's `bundle.resources`), and the real
// packaged-install bootstrap path below never called that script — so every
// actual installed user with an NVIDIA GPU got a venv with no cuDNN 8 compat
// libs (#827). Ported here so the real app-data venv gets them, matching what
// backend/main.py's cuDNN preload (#255) expects to find.
//
// (An earlier draft of #869 also ported setup.py's VC++ Redistributable
// check. Dropped as dead code per review: the Tauri exe itself dynamically
// links the MSVC CRT, so `LoadLibraryA("vcruntime140.dll")` from a *running*
// app is a tautology — and torch's real failure mode is msvcp140.dll inside
// the venv python process, not this one.)

/// Cross-platform pin, matches the wheel scripts/setup.py has always used —
/// keep both in sync if this ever needs to move.
const CUDNN8_COMPAT_PIN: &str = "nvidia-cudnn-cu12==8.9.7.29";

/// The `cudnn8_compat/` install target inside a venv's site-packages,
/// mirroring `_find_compat_dir()` in scripts/setup.py exactly (and what
/// backend/main.py's ctypes preload looks for). Linux's path is versioned by
/// the venv's own Python (`lib/pythonX.Y/site-packages`), so this queries the
/// live interpreter rather than assuming the version `uv venv` was asked for
/// — the system-Python fallback path can hand back a different one.
fn cudnn8_compat_dir(venv_dir: &Path, venv_py: &Path) -> Option<PathBuf> {
    if cfg!(windows) {
        return Some(venv_dir.join("Lib").join("site-packages").join("cudnn8_compat"));
    }
    let out = Command::new(venv_py)
        .args(["-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let pyver = String::from_utf8_lossy(&out.stdout).trim().to_string();
    Some(
        venv_dir
            .join("lib")
            .join(format!("python{}", pyver))
            .join("site-packages")
            .join("cudnn8_compat"),
    )
}

/// The subdirectory (within `cudnn8_compat/`) actually holding the shared
/// libraries, and the filename pattern that counts as "installed" — same
/// glob scripts/setup.py's `_count_cudnn8_libs()` uses.
fn cudnn8_lib_dir_and_pattern(compat_dir: &Path) -> (PathBuf, &'static str, &'static str) {
    if cfg!(windows) {
        (compat_dir.join("nvidia").join("cudnn").join("bin"), "cudnn", "64_8.dll")
    } else {
        (compat_dir.join("nvidia").join("cudnn").join("lib"), "libcudnn", ".so.8")
    }
}

fn count_cudnn8_libs(lib_dir: &Path, prefix: &str, suffix: &str) -> usize {
    fs::read_dir(lib_dir)
        .map(|entries| {
            entries
                .filter_map(|e| e.ok())
                .filter(|e| {
                    let name = e.file_name();
                    let name = name.to_string_lossy();
                    name.starts_with(prefix) && name.ends_with(suffix)
                })
                .count()
        })
        .unwrap_or(0)
}

/// Verdict from probing the venv's torch (see `CUDNN8_CUDA_PROBE_PY`).
#[derive(Debug, PartialEq, Eq)]
enum CudnnProbe {
    /// CUDA torch build with a live CUDA device: side-load cuDNN 8.
    Install,
    /// Definitive no — CPU-only box, no NVIDIA device, or a ROCm torch build
    /// (HIP reports `torch.cuda.is_available() == True`, but the ~700 MB CUDA
    /// `nvidia-cudnn-cu12` wheel is pure waste on an AMD box, #124). Cache it
    /// so the synchronous `import torch` never taxes this venv's launches
    /// again.
    CacheNegative,
    /// The probe didn't run cleanly (torch missing / broken venv / unexpected
    /// output) — skip this launch but do NOT cache, so a transient failure
    /// can't permanently disable the side-load on a real CUDA machine.
    SkipNoCache,
}

/// Prints exactly one verdict: `hip` (ROCm build — checked BEFORE
/// `cuda.is_available()`, which HIP spoofs), `cuda` (CUDA build with a live
/// device), or `none`.
const CUDNN8_CUDA_PROBE_PY: &str = "import torch; print('hip' if getattr(torch.version, 'hip', None) else 'cuda' if torch.cuda.is_available() else 'none')";

fn classify_cuda_probe(stdout: &str) -> CudnnProbe {
    match stdout.trim() {
        "cuda" => CudnnProbe::Install,
        "hip" | "none" => CudnnProbe::CacheNegative,
        _ => CudnnProbe::SkipNoCache,
    }
}

/// Marker recording a cached negative CUDA probe for this venv. Lives inside
/// `.venv/` so a full venv rebuild ("Clean & Retry") clears it implicitly;
/// anything that re-syncs the venv in place must call
/// `invalidate_cudnn8_probe_cache` (the torch build may have changed).
fn cudnn8_probe_marker(venv_dir: &Path) -> PathBuf {
    venv_dir.join(".cudnn8_probe_negative")
}

/// Call after ANY operation that can change the venv's torch build (drift /
/// repair / first-run `uv sync`, ROCm reinstall) so the next launch re-probes
/// exactly once per venv lifetime.
fn invalidate_cudnn8_probe_cache(venv_dir: &Path) {
    let _ = fs::remove_file(cudnn8_probe_marker(venv_dir));
}

/// CTranslate2 (faster-whisper / WhisperX) needs cuDNN 8, but PyTorch 2.8+
/// pulls in cuDNN 9. Side-loads cuDNN 8 into `cudnn8_compat/` next to the
/// venv's other packages — backend/main.py preloads it via ctypes at import
/// time (#255). Skipped entirely on macOS (no CUDA), on any machine without
/// a CUDA device, and on ROCm torch builds (#124) — and a negative probe is
/// cached per venv so CPU/AMD installs never pay the synchronous
/// `import torch` more than once (#869 review).
fn ensure_cudnn8_compat<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    uv_path: &Path,
    venv_py: &Path,
    venv_dir: &Path,
    project_dir: &Path,
) {
    if cfg!(target_os = "macos") {
        return;
    }
    // Cached negative from a previous launch (CPU/Intel/AMD — the majority of
    // installs): return before spending any subprocess. Cleared whenever the
    // venv is rebuilt or re-synced.
    let marker = cudnn8_probe_marker(venv_dir);
    if marker.is_file() {
        return;
    }
    let Some(compat_dir) = cudnn8_compat_dir(venv_dir, venv_py) else {
        log::warn!("cuDNN 8 compat: could not resolve venv site-packages layout — skipping");
        return;
    };
    let (lib_dir, prefix, suffix) = cudnn8_lib_dir_and_pattern(&compat_dir);
    if count_cudnn8_libs(&lib_dir, prefix, suffix) >= 5 {
        return;
    }

    let mut cuda_check = Command::new(venv_py);
    scrub_python_env(&mut cuda_check);
    crate::tools::no_window(&mut cuda_check); // Windows: no flashing console for the probe
    let verdict = cuda_check
        .args(["-c", CUDNN8_CUDA_PROBE_PY])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default();
    match classify_cuda_probe(&verdict) {
        CudnnProbe::Install => {}
        CudnnProbe::CacheNegative => {
            log::info!(
                "cuDNN 8 compat: torch probe says '{}' — caching the negative result for this venv",
                verdict
            );
            let _ = fs::write(&marker, format!("{}\n", verdict));
            return;
        }
        CudnnProbe::SkipNoCache => {
            log::warn!("cuDNN 8 compat: torch probe failed — skipping this launch (not cached)");
            return;
        }
    }

    log::info!("Installing cuDNN 8 compatibility libraries for CTranslate2 (#255)");
    emit_log(app, "installing_deps", "Installing cuDNN 8 compatibility libraries for CUDA transcription…");
    let mut cmd = Command::new(uv_path);
    scrub_python_env(&mut cmd);
    apply_uv_env(app, &mut cmd);
    cmd.arg("pip")
        .arg("install")
        .arg("--no-deps")
        .arg("--target")
        .arg(&compat_dir)
        .arg("--python")
        .arg(venv_py)
        .arg(CUDNN8_COMPAT_PIN)
        .current_dir(project_dir);
    match run_streaming(app, "installing_deps", &mut cmd) {
        Ok(ref s) if s.success() => {
            log::info!("cuDNN 8 compat installed: {} libraries", count_cudnn8_libs(&lib_dir, prefix, suffix));
        }
        other => {
            log::warn!("cuDNN 8 compat install failed ({:?}) — CUDA transcription may not work", other);
            emit_log(
                app, "installing_deps",
                "cuDNN 8 compat install failed — CUDA-based transcription may not work. \
Retry from Settings, or see docs/install/troubleshooting.md.",
            );
        }
    }
}

/// Prepare (and on first run, create) the Python venv that will host the
/// backend process. Returns (venv_python, backend_source_dir).
pub fn ensure_venv_ready<R: tauri::Runtime>(app: &tauri::AppHandle<R>, progress: Option<&Arc<Mutex<BootstrapStage>>>) -> Option<(PathBuf, PathBuf)> {
    let fail = |progress: Option<&Arc<Mutex<BootstrapStage>>>, msg: &str| {
        log::error!("{}", msg);
        if let Some(p) = progress {
            set_stage(p, BootstrapStage::Failed { message: msg.to_string() });
        }
    };
    if let Some(p) = progress {
        set_stage(p, BootstrapStage::Checking);
    }

    if let Some(dev_root) = find_dev_project_root() {
        let dev_venv = dev_root.join(".venv");
        let dev_py = venv_python_path(&dev_venv);
        if dev_py.is_file() {
            let backend_dir = dev_root.join("backend");
            if backend_dir.is_dir() {
                return Some((dev_py, backend_dir));
            }
        }
    }

    // Root chosen on the setup screen: app_local_data_dir by default, the
    // exe-adjacent folder in portable mode, or a user-picked custom dir. This
    // is the TRUE root (never redirected) — `resolve_venv_root` below decides
    // if this call should build/verify somewhere else instead.
    let true_app_data = crate::setup::env_root(app);
    let mut app_data = true_app_data.clone();
    let mut project_dir = app_data.join("project");
    let mut venv_dir = project_dir.join(".venv");
    let mut venv_py = venv_python_path(&venv_dir);
    let mut backend_dir = project_dir.join("backend");

    // #314: structural validation before trusting an existing venv. A venv
    // whose pyvenv.cfg is gone (interrupted install) or whose python is a
    // dangling symlink (its base interpreter was removed) can never recover
    // via `uv sync` — the interpreter itself is the broken part, and the
    // backend would just exit 106 ("No pyvenv.cfg file") forever. Quarantine
    // it and fall through to the creation path below, which rebuilds it with
    // the normal CreatingVenv/InstallingDeps progress. A healthy venv returns
    // None here and is never touched. Runs against the TRUE root — this is a
    // structural check on-disk, independent of #1783's ASCII redirect below.
    if let Some(problem) = venv_structural_problem(&venv_dir) {
        log::warn!(
            "Venv at {} is structurally broken ({}) — removing it and rebuilding (#314)",
            venv_dir.display(),
            problem
        );
        emit_log(
            app,
            "checking",
            &format!("Detected a broken Python environment ({}) — rebuilding it automatically", problem),
        );
        if !quarantine_broken_venv(&venv_dir) {
            fail(progress, &format!(
                "The Python environment at {} is broken ({}) but could not be removed \
automatically. Close any programs using that folder, or delete the .venv folder \
manually, then relaunch.",
                venv_dir.display(),
                problem
            ));
            return None;
        }
    }

    // #1783: a venv that exists here and probes healthy (or fails for any
    // OTHER reason) is NEVER relocated — see `resolve_venv_root`'s doc for
    // the full invariant. `is_ascii_path` is a cheap pre-check (string scan,
    // no subprocess): an ASCII `true_app_data` — every macOS/Linux install
    // and the overwhelming majority on Windows — can never produce this
    // crash, so it skips straight to `Keep` without spawning the venv's
    // interpreter a second time on every single launch just to confirm what
    // is already structurally impossible.
    let venv_exists_at_true_root = venv_py.is_file() && backend_dir.is_dir();
    let decision = if crate::setup::is_ascii_path(&true_app_data) {
        VenvRootDecision::Keep
    } else {
        let probe_stderr =
            if venv_exists_at_true_root { venv_interpreter_probe_stderr(&venv_py) } else { None };
        let ascii_safe_root = crate::setup::ascii_safe_dir(&true_app_data);
        resolve_venv_root(&true_app_data, &ascii_safe_root, venv_exists_at_true_root, probe_stderr.as_deref())
    };
    match decision {
        VenvRootDecision::Redirect(new_root) => {
            // CWE-532: this env root's non-ASCII component is, by definition
            // of this whole bug, a per-user identifier (most often a Windows
            // account name) — never log the path itself, only what happened
            // and why. The old environment's exact location stays in the
            // config/registry the app already tracks, not the log.
            if venv_exists_at_true_root {
                log::warn!(
                    "Existing venv can't start — its path isn't valid in the active Windows code \
page (#1783 .pth crash) — building a fresh environment at an ASCII-safe path instead. The old \
environment is left on disk, untouched, for manual recovery."
                );
            } else {
                log::info!(
                    "No usable environment yet, and its configured path isn't ASCII-safe — \
creating the new environment at an ASCII-safe path instead (#1783)"
                );
            }
            emit_log(app, "checking", "Building the Python environment at an ASCII-safe path (#1783)");
            app_data = new_root;
            project_dir = app_data.join("project");
            venv_dir = project_dir.join(".venv");
            venv_py = venv_python_path(&venv_dir);
            backend_dir = project_dir.join("backend");
        }
        VenvRootDecision::Unfixable => {
            let msg = nonascii_pth_crash_msg(crate::config::load_config(app).install_mode == "portable");
            fail(progress, &msg);
            return None;
        }
        VenvRootDecision::Keep => {}
    }

    if venv_py.is_file() && backend_dir.is_dir() {
        let mut uvicorn_check_cmd = Command::new(&venv_py);
        scrub_python_env(&mut uvicorn_check_cmd); // #144: don't inherit AppImage's bundled Python
        crate::tools::no_window(&mut uvicorn_check_cmd); // Windows: no flashing console
        let uvicorn_check = uvicorn_check_cmd
            .args(["-c", "import uvicorn"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        // #248: also verify pkg_resources is importable. Venvs created before the
        // setuptools<80 pin (commit 675cc20, fixes #224) have setuptools 80+, which
        // dropped the bundled pkg_resources. whisperx / ctranslate2 import it at
        // runtime, so dubbing/transcription crashes silently on those installs even
        // though uvicorn starts fine. We detect this here so we can force a repair
        // sync rather than handing back a broken venv.
        let pkg_resources_ok = if matches!(uvicorn_check, Ok(ref s) if s.success()) {
            let mut pr_check = Command::new(&venv_py);
            scrub_python_env(&mut pr_check);
            crate::tools::no_window(&mut pr_check); // Windows: no flashing console
            matches!(
                pr_check
                    .args(["-c", "import pkg_resources"])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status(),
                Ok(ref s) if s.success()
            )
        } else {
            false
        };
        // #564: a venv can pass the uvicorn + pkg_resources gates yet still be
        // unable to import its OWN `omnivoice` package — an interrupted/offline
        // `uv sync` installed deps but never laid the editable record, or an
        // antivirus quarantine removed `_editable_impl_omnivoice.pth`. The
        // backend then boots fine and only fails at the first model call with
        // "No module named 'omnivoice'". Verify it here so we force a repair
        // sync (which re-lays the editable install) instead of handing back a
        // broken venv. `find_spec` resolves the package WITHOUT importing it, so
        // this stays cheap — a real `import omnivoice` would pull in torch.
        let omnivoice_ok = if matches!(uvicorn_check, Ok(ref s) if s.success()) {
            let mut ov_check = Command::new(&venv_py);
            scrub_python_env(&mut ov_check);
            crate::tools::no_window(&mut ov_check); // Windows: no flashing console
            matches!(
                ov_check
                    .args([
                        "-c",
                        "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('omnivoice') else 1)",
                    ])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status(),
                Ok(ref s) if s.success()
            )
        } else {
            false
        };
        if matches!(uvicorn_check, Ok(ref s) if s.success()) && pkg_resources_ok && omnivoice_ok {
            // Always sync source dirs from bundle so code fixes land on
            // existing installs without requiring a full clean+reinstall.
            let resource_dir = app.path().resource_dir().ok();
            if let Some(ref res) = resource_dir {
                let flat = res.clone();
                let up2  = res.join("_up_").join("_up_");
                let res_root = if flat.join("pyproject.toml").is_file() {
                    flat
                } else {
                    up2
                };
                let res_omni = res_root.join("omnivoice");
                let res_backend = res_root.join("backend");
                if res_omni.is_dir() {
                    let omnivoice_dir = project_dir.join("omnivoice");
                    let _ = fs::remove_dir_all(&omnivoice_dir);
                    if let Err(e) = copy_dir_recursive(&res_omni, &omnivoice_dir) {
                        fail(progress, &format!("Failed to sync omnivoice/ sources: {}", e));
                        return None;
                    }
                    log::info!("Synced omnivoice/ from bundle");
                }
                if res_backend.is_dir() {
                    let _ = fs::remove_dir_all(&backend_dir);
                    if let Err(e) = copy_dir_recursive(&res_backend, &backend_dir) {
                        fail(progress, &format!("Failed to sync backend/ sources: {}", e));
                        return None;
                    }
                    log::info!("Synced backend/ from bundle");
                }
                if let Err(e) = sync_packaged_frontend(&res_root, &project_dir) {
                    fail(progress, &format!("Failed to sync frontend/dist: {}", e));
                    return None;
                }
                log::info!("Synced frontend/dist from bundle");
                // #307: the source dirs above track the bundle, so the
                // dependency manifests must too — otherwise an upgrade runs
                // new code against a venv that predates newly added deps.
                //
                // Data-safety note (feat/safe-updates): this drift path — and
                // the repair path below — reconcile the venv IN PLACE via
                // `uv sync` (add/remove packages inside `.venv`); neither ever
                // deletes the venv, and a failed sync keeps the old venv (see
                // the error arm). The only venv-destroying paths are the #314
                // broken-venv heal (guarded by venv_rebuild_justified: a venv
                // whose interpreter probes healthy is never deleted) and the
                // explicit user-initiated "Clean & Retry".
                if refresh_project_manifests(res, &project_dir) {
                    log::info!("uv.lock changed since the venv was synced — running uv sync (#307)");
                    if let Some(p) = progress {
                        set_stage(p, BootstrapStage::InstallingDeps);
                    }
                    match resolve_uv(app, &app_data, progress) {
                        Ok(uv_path) => {
                            let mut drift_cmd = Command::new(&uv_path);
                            scrub_python_env(&mut drift_cmd); // #144
                            apply_uv_env(app, &mut drift_cmd);
                            let user_cfg = crate::config::load_config(app);
                            if let Some(pypi) = user_cfg.mirrors.pypi_index.as_deref() {
                                drift_cmd.env("UV_INDEX_URL", pypi);
                            } else if get_effective_region(app) == "china" {
                                drift_cmd.env("UV_INDEX_URL", "https://mirrors.aliyun.com/pypi/simple/");
                            }
                            drift_cmd
                                .args(DRIFT_SYNC_ARGS)
                                .current_dir(&project_dir);
                            match run_streaming(app, "installing_deps", &mut drift_cmd) {
                                Ok(ref s) if s.success() => {
                                    log::info!("Dependency drift sync complete (#307)");
                                    // The torch build may have changed — let
                                    // ensure_cudnn8_compat() re-probe once.
                                    invalidate_cudnn8_probe_cache(&venv_dir);
                                }
                                other => {
                                    // Don't brick a previously-working install
                                    // (e.g. an offline upgrade): keep the old
                                    // venv and let the backend try.
                                    log::error!(
                                        "Dependency drift sync failed ({:?}) — continuing with \
the existing venv; newly added dependencies may be missing (#307)",
                                        other
                                    );
                                }
                            }
                        }
                        Err(e) => {
                            log::error!("Could not resolve uv for drift sync: {} (#307)", e);
                        }
                    }
                }
            }
            match resolve_uv(app, &app_data, None) {
                Ok(uv_path) => ensure_cudnn8_compat(app, &uv_path, &venv_py, &venv_dir, &project_dir),
                Err(e) => log::warn!("cuDNN 8 compat: could not resolve uv: {}", e),
            }
            return Some((venv_py, backend_dir));
        }
        if matches!(uvicorn_check, Ok(ref s) if s.success()) {
            // uvicorn is fine but pkg_resources (#248) and/or the omnivoice
            // editable install (#564) is missing. pkg_resources: setuptools>=80
            // (installed before the <80 pin in #224) dropped the bundled module.
            // omnivoice: an interrupted/offline sync never laid the editable
            // record. Either way a repair `uv sync` re-pins setuptools AND
            // re-lays the editable install, so force it rather than hand back a
            // venv that crashes at the first model call.
            log::warn!(
                "Venv at {} starts uvicorn but failed a runtime-import gate \
(pkg_resources_ok={}, omnivoice_ok={}) — re-running uv sync to repair (#248 #564)",
                venv_dir.display(), pkg_resources_ok, omnivoice_ok
            );
        } else {
            log::warn!(
                "Venv exists at {} but uvicorn is not importable — re-running uv sync",
                venv_dir.display()
            );
        }
        // #889: a repair sync on an Intel Mac would just re-fail on the torch
        // resolution — surface the real reason instead of the raw uv error.
        if intel_mac_backend_unsupported() {
            fail(progress, INTEL_MAC_UNSUPPORTED_MSG);
            return None;
        }
        if let Some(p) = progress {
            set_stage(p, BootstrapStage::InstallingDeps);
        }
        let uv_path = match resolve_uv(app, &app_data, progress) {
            Ok(p) => p,
            Err(e) => { fail(progress, &e); return None; }
        };
        // #307: repair against the *current* bundled manifests, not the stale
        // copies from when the venv was first created.
        if let Ok(res) = app.path().resource_dir() {
            let _ = refresh_project_manifests(&res, &project_dir);
            let flat = res.clone();
            let up2 = res.join("_up_").join("_up_");
            let res_root = if flat.join("pyproject.toml").is_file() {
                flat
            } else {
                up2
            };
            if let Err(e) = sync_packaged_frontend(&res_root, &project_dir) {
                fail(progress, &format!("Failed to sync frontend/dist: {}", e));
                return None;
            }
        }
        let mut repair_cmd = Command::new(&uv_path);
        scrub_python_env(&mut repair_cmd); // #144: don't inherit AppImage's bundled Python
        apply_uv_env(app, &mut repair_cmd);
        let has_lockfile = project_dir.join("uv.lock").is_file();
        if has_lockfile {
            repair_cmd.args(REPAIR_SYNC_ARGS_LOCKED);
        } else {
            repair_cmd.args(REPAIR_SYNC_ARGS_UNLOCKED);
        }
        repair_cmd.current_dir(&project_dir);
        let repair_status = run_streaming(app, "installing_deps", &mut repair_cmd);
        if matches!(repair_status, Ok(ref s) if s.success()) {
            // The repair sync may have changed the torch build — clear any
            // cached negative CUDA probe so ensure_cudnn8_compat() below
            // re-checks once.
            invalidate_cudnn8_probe_cache(&venv_dir);
            // #248: after the repair sync, ensure pkg_resources landed. The repair
            // path is also triggered when pkg_resources is missing (see above), so
            // we must verify here rather than trusting that uv sync alone fixed it
            // (e.g. if the bundled uv.lock still pins setuptools>=80 somehow).
            let mut pr_repair_check = Command::new(&venv_py);
            scrub_python_env(&mut pr_repair_check);
            crate::tools::no_window(&mut pr_repair_check); // Windows: no flashing console
            let pr_ok = matches!(
                pr_repair_check
                    .args(["-c", "import pkg_resources"])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status(),
                Ok(ref s) if s.success()
            );
            if !pr_ok {
                log::warn!("pkg_resources still missing after repair sync — installing setuptools<80 directly (#248)");
                emit_log(app, "installing_deps",
                    "Repairing pkg_resources: force-reinstalling setuptools<80 (#248)");
                let mut st_cmd = Command::new(&uv_path);
                scrub_python_env(&mut st_cmd);
                apply_uv_env(app, &mut st_cmd);
                st_cmd
                    // --reinstall: when the venv has setuptools's *metadata* but its
                // pkg_resources files were removed (antivirus quarantine, partial
                // extract), a plain `pip install` sees it "already satisfied" and
                // no-ops — only a forced reinstall re-extracts pkg_resources (#248).
                .args(["pip", "install", "--reinstall", "setuptools>=75,<80"])
                    .current_dir(&project_dir);
                match run_streaming(app, "installing_deps", &mut st_cmd) {
                    Ok(ref s) if s.success() => {
                        log::info!("setuptools<80 installed after repair sync; pkg_resources now available (#248)");
                    }
                    other => {
                        log::error!("Failed to install setuptools<80 after repair sync: {:?} — dubbing may fail (#248)", other);
                    }
                }
                // Re-verify pkg_resources is importable after the targeted install.
                let mut pr_post_check = Command::new(&venv_py);
                scrub_python_env(&mut pr_post_check);
                crate::tools::no_window(&mut pr_post_check); // Windows: no flashing console
                let pr_final_ok = matches!(
                    pr_post_check
                        .args(["-c", "import pkg_resources"])
                        .stdout(Stdio::null())
                        .stderr(Stdio::null())
                        .status(),
                    Ok(ref s) if s.success()
                );
                if !pr_final_ok {
                    // Repair could not restore pkg_resources — fail loudly instead of
                    // handing back a venv that will crash on the first ASR/dub call. The
                    // "pkg_resources" text routes to the PKG_RESOURCES_MISSING failure
                    // mapping (clear, doc-linked remediation in the UI). (#248)
                    fail(
                        progress,
                        "pkg_resources is missing from the backend venv and the automatic \
                         setuptools repair did not restore it — its files were likely removed \
                         by antivirus or left by a partial install (the metadata is still there, \
                         so a plain reinstall is skipped). Open a terminal in the backend venv \
                         and run `uv pip install --reinstall 'setuptools>=75,<80'`, then restart. \
                         If it recurs, add the backend `.venv` folder to your antivirus \
                         exclusions. (#248)",
                    );
                    return None;
                }
            }
            ensure_cudnn8_compat(app, &uv_path, &venv_py, &venv_dir, &project_dir);
            return Some((venv_py, backend_dir));
        }
        let output_tail = app
            .try_state::<BootstrapState>()
            .and_then(|state| {
                state.logs.lock().ok().map(|logs| {
                    buffered_log_tail(&logs, "installing_deps", 12)
                })
            })
            .unwrap_or_default();
        fail(
            progress,
            &command_failure_message("Repair uv sync failed", &repair_status, &output_tail),
        );
        return None;
    }

    // #889: pre-check before creating a venv or attempting any `uv sync`. A
    // first-run install on an Intel Mac can only ever end in an unresolvable
    // torch dependency, so fail fast with the honest message — before any
    // download starts.
    if intel_mac_backend_unsupported() {
        fail(progress, INTEL_MAC_UNSUPPORTED_MSG);
        return None;
    }

    let resource_dir = app.path().resource_dir().ok()?;
    let flat = resource_dir.clone();
    let up2  = resource_dir.join("_up_").join("_up_");

    let resource_root = if flat.join("pyproject.toml").is_file() {
        flat
    } else if up2.join("pyproject.toml").is_file() {
        up2
    } else {
        fail(progress, &format!(
            "Missing bootstrap resources — checked flat={} and _up_={}",
            flat.display(), up2.display()));
        return None;
    };
    let resource_pyproject = resource_root.join("pyproject.toml");
    let resource_uvlock = resource_root.join("uv.lock");
    let resource_readme = resource_root.join("README.md");
    let resource_changelog = resource_root.join("CHANGELOG.md");
    let resource_omnivoice = resource_root.join("omnivoice");
    let resource_backend = resource_root.join("backend");

    if !resource_pyproject.is_file() || !resource_backend.is_dir() {
        fail(progress, &format!(
            "Missing bootstrap resources (pyproject={}, backend={})",
            resource_pyproject.display(), resource_backend.display()));
        return None;
    }

    log::info!("First-run venv bootstrap in {}", project_dir.display());
    if let Err(e) = fs::create_dir_all(&project_dir) {
        fail(progress, &format!("mkdir {} failed: {}", project_dir.display(), e));
        return None;
    }
    if let Err(e) = fs::copy(&resource_pyproject, project_dir.join("pyproject.toml")) {
        fail(progress, &format!("copy pyproject.toml: {}", e));
        return None;
    }
    if resource_uvlock.is_file() {
        if let Err(e) = fs::copy(&resource_uvlock, project_dir.join("uv.lock")) {
            log::warn!("Could not copy uv.lock (will use non-frozen sync): {}", e);
        }
    } else {
        log::warn!("No uv.lock in bundle — uv sync will resolve from scratch");
    }
    if resource_readme.is_file() {
        let _ = fs::copy(&resource_readme, project_dir.join("README.md"));
    } else if !project_dir.join("README.md").exists() {
        let _ = fs::write(project_dir.join("README.md"), "# VoiceStudio\n");
        log::warn!("No README.md in bundle — created stub");
    }
    // Shipped release notes for the Settings → Updates "What's new" viewer
    // (GET /api/settings/changelog). Optional: the endpoint degrades to
    // `available: false` when absent.
    if resource_changelog.is_file() {
        let _ = fs::copy(&resource_changelog, project_dir.join("CHANGELOG.md"));
    }
    let omnivoice_dir = project_dir.join("omnivoice");
    if resource_omnivoice.is_dir() {
        if let Err(e) = copy_dir_recursive(&resource_omnivoice, &omnivoice_dir) {
            log::warn!("Could not copy omnivoice/ source package: {}", e);
        }
    } else {
        log::warn!("No omnivoice/ in bundle — model preload may fail");
    }
    if let Err(e) = copy_dir_recursive(&resource_backend, &backend_dir) {
        fail(progress, &format!("copy backend/: {}", e));
        return None;
    }
    if let Err(e) = sync_packaged_frontend(&resource_root, &project_dir) {
        fail(progress, &format!("copy frontend/dist: {}", e));
        return None;
    }

    let uv_path = match resolve_uv(app, &app_data, progress) {
        Ok(p) => p,
        Err(e) => { fail(progress, &e); return None; }
    };
    log::info!("Bootstrap uv: {}", uv_path.display());

    if let Some(p) = progress {
        set_stage(p, BootstrapStage::CreatingVenv);
    }
    // plan-03 (#130): mirror cascade + system-Python fallback so first-run
    // survives a GitHub-blocked network. Try in order: (0) the user's custom
    // mirror from the setup screen, when set, (1) default GitHub host,
    // (2) gh-proxy mirror, (3) system Python (only if >= 3.11) — each with
    // longer timeouts/retries. Stop at the first that succeeds.
    let user_cfg = crate::config::load_config(app);
    let custom_mirrors = user_cfg.mirrors.clone();
    let mut venv_attempts: Vec<(&str, Vec<&str>, Vec<(&str, String)>)> = Vec::new();
    if let Some(custom_py_mirror) = custom_mirrors.python_downloads.clone() {
        venv_attempts.push((
            "custom mirror (setup screen)",
            vec!["venv", "--python", "3.11", "--managed-python"],
            vec![("UV_PYTHON_INSTALL_MIRROR", custom_py_mirror)],
        ));
    }
    venv_attempts.push(("default", vec!["venv", "--python", "3.11", "--managed-python"], vec![]));
    venv_attempts.push((
        "gh-proxy mirror",
        vec!["venv", "--python", "3.11", "--managed-python"],
        vec![("UV_PYTHON_INSTALL_MIRROR", PY_INSTALL_MIRROR.to_string())],
    ));
    // Always try the system Python as the LAST resort (mirrors blocked too).
    // No `--python 3.11` pin and no pre-gate: uv's own interpreter discovery is
    // the authority — with `only-system` + the project's `requires-python =
    // ">=3.11"` it resolves any compatible system interpreter (3.12/3.13/3.14…),
    // or fails fast → the remediation message. A pre-gate that only probed
    // `python3`/`python` was stricter than uv (e.g. it missed a Homebrew 3.14
    // when `python3` was the macOS 3.9), wrongly skipping this fallback.
    venv_attempts.push((
        "system-python",
        vec!["venv"],
        vec![("UV_PYTHON_PREFERENCE", "only-system".to_string())],
    ));

    let mut venv_ok = false;
    for (label, args, envs) in &venv_attempts {
        let mut venv_cmd = Command::new(&uv_path);
        scrub_python_env(&mut venv_cmd); // #144: don't inherit AppImage's bundled Python
        apply_uv_env(app, &mut venv_cmd);
        for (k, v) in envs {
            venv_cmd.env(k, v);
        }
        venv_cmd.args(args.iter()).current_dir(&project_dir);
        log::info!("uv venv attempt ({})", label);
        if matches!(run_streaming(app, "creating_venv", &mut venv_cmd), Ok(ref s) if s.success()) {
            venv_ok = true;
            break;
        }
        log::warn!("uv venv attempt ({}) failed; trying next strategy", label);
    }
    if !venv_ok {
        fail(progress, BOOTSTRAP_REMEDIATION);
        return None;
    }

    if let Some(p) = progress {
        set_stage(p, BootstrapStage::InstallingDeps);
    }
    let wheels_dir = wheels_drop_dir(&app_data);
    let mut sync_cmd = Command::new(&uv_path);
    scrub_python_env(&mut sync_cmd); // #144: don't inherit AppImage's bundled Python
    apply_uv_env(app, &mut sync_cmd);
    // #569: let uv install from locally-dropped wheels. (--frozen ignores
    // find-links, but the non-frozen torch-recovery retry below honors it.)
    sync_cmd.env("UV_FIND_LINKS", &wheels_dir);
    let has_lockfile = project_dir.join("uv.lock").is_file();
    if has_lockfile {
        sync_cmd
            .args(["sync", "--frozen", "--no-dev", "--verbose"])
            .current_dir(&project_dir);
    } else {
        log::info!("No uv.lock present, running uv sync without --frozen");
        sync_cmd
            .args(["sync", "--no-dev", "--verbose"])
            .current_dir(&project_dir);
    }
    // PyPI index precedence: explicit setup-screen mirror > region preset.
    if let Some(pypi) = custom_mirrors.pypi_index.as_deref() {
        sync_cmd.env("UV_INDEX_URL", pypi);
    } else if get_effective_region(app) == "china" {
        sync_cmd.env("UV_INDEX_URL", "https://mirrors.aliyun.com/pypi/simple/");
    }
    let mut sync_ok = matches!(run_streaming(app, "installing_deps", &mut sync_cmd), Ok(ref s) if s.success());

    // #569: the big cu128 torch wheel (~2.5 GB) is the most common first-run
    // download failure on restricted networks. If the frozen sync failed on it
    // AND the user has dropped wheels in the local drop dir, retry NON-frozen
    // with --find-links so uv re-resolves using the local wheels (verified: a
    // non-frozen find-links sync installs from a local wheel offline; --frozen
    // does not). Best-effort: if it can't satisfy from the wheels, it fails
    // identically to before and the actionable error below still fires.
    if !sync_ok && has_lockfile {
        let tail = crate::backend::read_error_log_tail(40);
        let have_local_wheels = fs::read_dir(&wheels_dir)
            .map(|mut d| d.next().is_some())
            .unwrap_or(false);
        if have_local_wheels && sync_failure_is_torch_download(&tail) {
            log::warn!(
                "Frozen sync failed on a torch download; retrying non-frozen with local wheels in {} (#569)",
                wheels_dir.display()
            );
            emit_log(app, "installing_deps", "Retrying the install with the wheels you provided locally…");
            let mut retry = Command::new(&uv_path);
            scrub_python_env(&mut retry);
            apply_uv_env(app, &mut retry);
            retry.env("UV_FIND_LINKS", &wheels_dir);
            if let Some(pypi) = custom_mirrors.pypi_index.as_deref() {
                retry.env("UV_INDEX_URL", pypi);
            } else if get_effective_region(app) == "china" {
                retry.env("UV_INDEX_URL", "https://mirrors.aliyun.com/pypi/simple/");
            }
            retry.args(["sync", "--no-dev", "--verbose"]).current_dir(&project_dir);
            sync_ok = matches!(run_streaming(app, "installing_deps", &mut retry), Ok(ref s) if s.success());
        }
    }

    if !sync_ok {
        let tail = crate::backend::read_error_log_tail(40);
        let msg = if sync_failure_is_torch_download(&tail) {
            format!(
                "Couldn't download the CUDA PyTorch package (a ~2.5 GB wheel from download.pytorch.org). \
This is almost always a dropped or restricted network, not a bug. What to try, in order: \
(1) \"Clean & Retry\" — large downloads often succeed on a second attempt. \
(2) Connect through a VPN if your network blocks the PyTorch CDN. \
(3) Manually download the matching torch and torchaudio wheels (see the link in your error log / \
pytorch.org), drop them in {}, then \"Clean & Retry\" — the install will use them locally. \
Details: docs/install/troubleshooting.md (#569).",
                wheels_dir.display()
            )
        } else {
            "Dependency install (uv sync) failed — often a network drop or a partial cache. \
\"Clean & Retry\" rebuilds the environment from scratch. If your network blocks PyPI, set a PyPI \
mirror in Settings → region/mirrors (see docs/install/troubleshooting.md).".to_string()
        };
        fail(progress, &msg);
        return None;
    }

    // #248 belt-and-suspenders: after every uv sync, verify that pkg_resources is
    // importable. If it isn't (setuptools>=80 somehow landed — e.g. no lock file in
    // bundle, or the lock was resolved without our pin), run a targeted
    // `uv pip install "setuptools<80"` to repair the venv without touching anything
    // else. This is safe on all platforms (pure-Python wheel, no native code).
    {
        let mut pr_verify = Command::new(&venv_py);
        scrub_python_env(&mut pr_verify);
        crate::tools::no_window(&mut pr_verify); // Windows: no flashing console
        let pr_ok = matches!(
            pr_verify
                .args(["-c", "import pkg_resources"])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status(),
            Ok(ref s) if s.success()
        );
        if !pr_ok {
            log::warn!("pkg_resources not importable after uv sync — installing setuptools<80 (#248)");
            emit_log(app, "installing_deps",
                "pkg_resources missing — force-reinstalling setuptools<80 to fix (#248)");
            let mut st_cmd = Command::new(&uv_path);
            scrub_python_env(&mut st_cmd);
            apply_uv_env(app, &mut st_cmd);
            st_cmd
                // --reinstall: when the venv has setuptools's *metadata* but its
                // pkg_resources files were removed (antivirus quarantine, partial
                // extract), a plain `pip install` sees it "already satisfied" and
                // no-ops — only a forced reinstall re-extracts pkg_resources (#248).
                .args(["pip", "install", "--reinstall", "setuptools>=75,<80"])
                .current_dir(&project_dir);
            match run_streaming(app, "installing_deps", &mut st_cmd) {
                Ok(ref s) if s.success() => {
                    log::info!("setuptools<80 installed; pkg_resources now available (#248)");
                }
                other => {
                    log::error!("Failed to install setuptools<80: {:?} — dubbing may fail (#248)", other);
                }
            }
        }
    }

    // Fresh venv, fresh sync: a stale negative-probe marker (e.g. a venv
    // recreated in place over a previous one) must not suppress the probe.
    invalidate_cudnn8_probe_cache(&venv_dir);
    ensure_cudnn8_compat(app, &uv_path, &venv_py, &venv_dir, &project_dir);

    // Opt-in AMD ROCm (#124): the default install ships the CUDA torch build,
    // so AMD-only machines fall back to CPU. If the user set
    // OMNIVOICE_TORCH_VARIANT=rocm, reinstall torch/torchaudio from the ROCm
    // wheel index. Non-fatal: a failure keeps the working CUDA/CPU build rather
    // than breaking first-run. Default (unset) leaves everything unchanged.
    if let Some(rocm_url) = rocm_opt_in(&user_cfg.torch_variant) {
        log::info!("ROCm torch variant selected → reinstalling torch from {}", rocm_url);
        let mut rocm_cmd = Command::new(&uv_path);
        scrub_python_env(&mut rocm_cmd); // #144: don't inherit AppImage's bundled Python
        apply_uv_env(app, &mut rocm_cmd);
        rocm_cmd.args(rocm_torch_reinstall_args(&rocm_url)).current_dir(&project_dir);
        let rocm_status = run_streaming(app, "installing_deps", &mut rocm_cmd);
        if matches!(rocm_status, Ok(ref s) if s.success()) {
            // The torch build just switched to ROCm: re-probe on the next
            // launch (it reports 'hip' and re-caches the negative, so the
            // CUDA cuDNN wheel is never fetched on an AMD box, #124).
            invalidate_cudnn8_probe_cache(&venv_dir);
        } else {
            log::warn!("ROCm torch reinstall failed ({:?}); keeping default torch build", rocm_status);
            emit_log(
                app, "installing_deps",
                "ROCm torch reinstall failed — keeping the default torch build. \
See docs/install/linux.md (AMD GPU) to install the ROCm wheel manually.",
            );
        }
    }

    Some((venv_py, backend_dir))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[test]
    fn packaged_frontend_is_installed_for_the_lan_server() {
        let resources = tempfile::tempdir().unwrap();
        let project = tempfile::tempdir().unwrap();
        let source = resources.path().join("frontend").join("dist");
        fs::create_dir_all(source.join("assets")).unwrap();
        fs::write(source.join("index.html"), "new shell").unwrap();
        fs::write(source.join("assets").join("client.js"), "new client").unwrap();

        let installed = project.path().join("frontend").join("dist");
        fs::create_dir_all(&installed).unwrap();
        fs::write(installed.join("index.html"), "stale shell").unwrap();

        sync_packaged_frontend(resources.path(), project.path()).unwrap();

        assert_eq!(
            fs::read_to_string(installed.join("index.html")).unwrap(),
            "new shell"
        );
        assert_eq!(
            fs::read_to_string(installed.join("assets").join("client.js")).unwrap(),
            "new client"
        );
    }

    #[test]
    fn packaged_frontend_error_does_not_expose_resource_path() {
        let resources = tempfile::tempdir().unwrap();
        let project = tempfile::tempdir().unwrap();

        let error = sync_packaged_frontend(resources.path(), project.path()).unwrap_err();

        assert_eq!(error.kind(), io::ErrorKind::NotFound);
        assert_eq!(error.to_string(), "bundled frontend is missing index.html");
        assert!(!error.to_string().contains(&resources.path().display().to_string()));
    }

    #[cfg(unix)]
    #[test]
    fn failed_packaged_frontend_copy_preserves_installed_shell() {
        use std::os::unix::fs::symlink;

        let resources = tempfile::tempdir().unwrap();
        let project = tempfile::tempdir().unwrap();
        let source = resources.path().join("frontend").join("dist");
        fs::create_dir_all(source.join("assets")).unwrap();
        fs::write(source.join("index.html"), "new shell").unwrap();
        symlink("missing-client.js", source.join("assets").join("client.js")).unwrap();

        let installed = project.path().join("frontend").join("dist");
        fs::create_dir_all(&installed).unwrap();
        fs::write(installed.join("index.html"), "working shell").unwrap();

        sync_packaged_frontend(resources.path(), project.path()).unwrap_err();

        assert_eq!(
            fs::read_to_string(installed.join("index.html")).unwrap(),
            "working shell"
        );
    }

    #[cfg(unix)]
    #[test]
    fn interrupted_frontend_swap_recovers_backup_before_a_later_copy_failure() {
        use std::os::unix::fs::symlink;

        let resources = tempfile::tempdir().unwrap();
        let project = tempfile::tempdir().unwrap();
        let source = resources.path().join("frontend").join("dist");
        fs::create_dir_all(source.join("assets")).unwrap();
        fs::write(source.join("index.html"), "new shell").unwrap();
        symlink("missing-client.js", source.join("assets").join("client.js")).unwrap();

        let frontend = project.path().join("frontend");
        let installed = frontend.join("dist");
        let backup = frontend.join(".dist-backup");
        fs::create_dir_all(&backup).unwrap();
        fs::write(backup.join("index.html"), "working backup shell").unwrap();

        sync_packaged_frontend(resources.path(), project.path()).unwrap_err();

        assert_eq!(
            fs::read_to_string(installed.join("index.html")).unwrap(),
            "working backup shell"
        );
    }

    #[test]
    fn interrupted_backup_cleanup_failure_preserves_working_destination() {
        let resources = tempfile::tempdir().unwrap();
        let project = tempfile::tempdir().unwrap();
        let source = resources.path().join("frontend").join("dist");
        fs::create_dir_all(&source).unwrap();
        fs::write(source.join("index.html"), "new shell").unwrap();

        let frontend = project.path().join("frontend");
        let installed = frontend.join("dist");
        let backup = frontend.join(".dist-backup");
        fs::create_dir_all(&installed).unwrap();
        fs::write(installed.join("index.html"), "working shell").unwrap();
        // A non-directory at the interrupted backup path makes cleanup fail
        // and would also prevent the live destination from being renamed.
        fs::write(&backup, "partial backup").unwrap();

        sync_packaged_frontend(resources.path(), project.path()).unwrap_err();

        assert_eq!(
            fs::read_to_string(installed.join("index.html")).unwrap(),
            "working shell"
        );
    }

    #[test]
    fn update_drift_sync_preserves_user_installed_engines() {
        // #1029: the routine update sync must carry --inexact so a
        // user-pip-installed optional engine (voxcpm, kittentts — packages
        // the app's own Model Catalogue → Engines hints tell users to install into
        // this venv) survives every update instead of being silently
        // uninstalled. --frozen must stay (lockfile is the resolution truth).
        assert!(DRIFT_SYNC_ARGS.contains(&"--inexact"),
            "update-drift sync lost --inexact — user-installed engines get wiped on every update (#1029)");
        assert!(DRIFT_SYNC_ARGS.contains(&"--frozen"));
    }

    #[test]
    fn repair_sync_stays_exact() {
        // Deliberate asymmetry with the drift sync: repair runs when the venv
        // is BROKEN and a user-installed extra is a plausible cause — healing
        // must restore the known-good locked state, extras included-out.
        assert!(!REPAIR_SYNC_ARGS_LOCKED.contains(&"--inexact"),
            "repair sync must stay exact — it's the recovery path when an extra broke the venv");
        assert!(!REPAIR_SYNC_ARGS_UNLOCKED.contains(&"--inexact"));
        assert!(REPAIR_SYNC_ARGS_LOCKED.contains(&"--frozen"));
    }

    #[test]
    fn scrub_python_env_removes_bundled_runtime_vars() {
        // #144: every uv/venv/pip subprocess must drop the AppImage's bundled
        // Python env vars so the managed interpreter resolves its own stdlib.
        // `env_remove` queues a removal that `get_envs()` reports as (key, None).
        let mut cmd = Command::new("uv");
        scrub_python_env(&mut cmd);
        let removed: std::collections::HashSet<String> = cmd
            .get_envs()
            .filter(|(_, v)| v.is_none())
            .map(|(k, _)| k.to_string_lossy().into_owned())
            .collect();
        assert!(removed.contains("PYTHONHOME"), "PYTHONHOME must be scrubbed");
        assert!(removed.contains("PYTHONPATH"), "PYTHONPATH must be scrubbed");
        assert!(removed.contains("LD_LIBRARY_PATH"), "LD_LIBRARY_PATH must be scrubbed");
    }

    #[test]
    fn intel_mac_message_keeps_its_contract_phrases() {
        // #889: BootstrapSplash.jsx routes this failure to the localized
        // `bootstrap.hint_intel_mac` hint by matching the lead phrase, and the
        // message must keep pointing users at the docs + the remote-backend
        // escape hatch. Guard those load-bearing fragments against rewording.
        assert!(INTEL_MAC_UNSUPPORTED_MSG.contains("Intel Macs can't run the local AI backend"));
        assert!(INTEL_MAC_UNSUPPORTED_MSG.contains("docs/install/macos.md"));
        assert!(INTEL_MAC_UNSUPPORTED_MSG.contains("Sharing → Remote backend"));
        assert!(INTEL_MAC_UNSUPPORTED_MSG.contains("#889"));
    }

    #[test]
    fn apply_uv_http_env_sets_timeouts_and_retries() {
        let mut cmd = Command::new("uv");
        apply_uv_http_env(&mut cmd);
        let envs: HashMap<String, String> = cmd
            .get_envs()
            .filter_map(|(k, v)| {
                v.map(|v| (k.to_string_lossy().into_owned(), v.to_string_lossy().into_owned()))
            })
            .collect();
        assert_eq!(envs.get("UV_HTTP_TIMEOUT").map(String::as_str), Some("120"));
        assert_eq!(envs.get("UV_HTTP_CONNECT_TIMEOUT").map(String::as_str), Some("30"));
        assert_eq!(envs.get("UV_HTTP_RETRIES").map(String::as_str), Some("5"));
    }

    #[test]
    fn crash_loop_policy_is_three_deaths_in_ten_minutes() {
        // #941 escalation guard: ≥3 crashes inside 10 min must stop the
        // respawn loop and land on the Failed screen with the crash details —
        // the old 5-in-60s budget let slow crash loops spin silently forever.
        assert_eq!(MAX_RESTARTS, 3);
        assert_eq!(RESTART_WINDOW, Duration::from_secs(600));
    }

    #[test]
    fn restart_budget_caps_respawns_and_prunes_old_ones() {
        // Supervisor backoff policy (#567): fewer than MAX_RESTARTS deaths
        // inside the window keeps restarting; hitting the cap gives up.
        let t0 = Instant::now();
        let mut times: Vec<Instant> = (0..MAX_RESTARTS - 1).map(|_| t0).collect();
        assert!(
            !restart_budget_exhausted(&mut times, t0),
            "{} deaths in-window is under the cap",
            MAX_RESTARTS - 1
        );
        times.push(t0);
        assert!(
            restart_budget_exhausted(&mut times, t0),
            "{} deaths in-window must trip the cap",
            MAX_RESTARTS
        );

        // Restarts older than the window are pruned and never count toward the
        // cap, so an app left running for hours never crash-loops on stale
        // history. (Forward Instant arithmetic — always representable.)
        let later = t0 + RESTART_WINDOW + Duration::from_secs(1);
        let mut aged: Vec<Instant> = (0..MAX_RESTARTS).map(|_| t0).collect();
        assert!(
            !restart_budget_exhausted(&mut aged, later),
            "deaths older than the window must be pruned, not counted"
        );
        assert!(aged.is_empty(), "stale timestamps should have been dropped");
    }

    /// Env-mutating tests in THIS module serialize on their own lock (cargo
    /// runs tests in threads; the harness binary has its own).
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    #[test]
    fn timing_overrides_default_to_production_values() {
        // The env overrides exist for the fault-injection harness only —
        // production timing must not drift when they are unset.
        let _g = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::remove_var("OMNIVOICE_STARTUP_BUDGET_S");
        std::env::remove_var("OMNIVOICE_SUPERVISOR_POLL_MS");
        assert_eq!(startup_budget(), Duration::from_secs(300));
        assert_eq!(supervisor_poll(), Duration::from_secs(2));
        // Zero/garbage never yields a degenerate loop.
        std::env::set_var("OMNIVOICE_STARTUP_BUDGET_S", "0");
        std::env::set_var("OMNIVOICE_SUPERVISOR_POLL_MS", "abc");
        assert_eq!(startup_budget(), Duration::from_secs(300));
        assert_eq!(supervisor_poll(), Duration::from_secs(2));
        std::env::set_var("OMNIVOICE_STARTUP_BUDGET_S", "6");
        assert_eq!(startup_budget(), Duration::from_secs(6));
        std::env::remove_var("OMNIVOICE_STARTUP_BUDGET_S");
        std::env::remove_var("OMNIVOICE_SUPERVISOR_POLL_MS");
    }

    #[test]
    fn restart_backoff_escalates_but_first_respawn_is_immediate() {
        // A one-off crash self-heals with zero added latency; repeat deaths
        // inside the window get an escalating pause so a tight crash loop
        // can't burn the whole 3-in-600s budget in seconds.
        assert_eq!(restart_backoff_delay(0), Duration::ZERO);
        assert_eq!(restart_backoff_delay(1), Duration::from_secs(5));
        assert_eq!(restart_backoff_delay(2), Duration::from_secs(15));
        // Monotonic, and capped rather than unbounded — the budget check is
        // what ends a hopeless loop, not an ever-growing sleep.
        assert_eq!(restart_backoff_delay(50), Duration::from_secs(15));
        for n in 0..10 {
            assert!(restart_backoff_delay(n) <= restart_backoff_delay(n + 1));
        }
    }

    #[test]
    fn torch_download_failure_is_detected_for_targeted_help() {
        // #569: the cu128 torch wheel host (and a torch-named download/fetch
        // failure) get torch-specific guidance + the local-wheel retry.
        assert!(sync_failure_is_torch_download(
            "× Failed to download `torch==2.8.0+cu128`\n  https://download.pytorch.org/whl/cu128/torch-2.8.0%2Bcu128-cp311-cp311-win_amd64.whl"
        ));
        assert!(sync_failure_is_torch_download(
            "error sending request for url (https://download-r2.pytorch.org/whl/cu128/torch-2.8.0.whl)"
        ));
        assert!(sync_failure_is_torch_download("Failed to fetch torch wheel"));
        // An unrelated PyPI failure must NOT be mistaken for the torch case.
        assert!(!sync_failure_is_torch_download(
            "Failed to download `numpy==2.0.0` from https://pypi.org/simple"
        ));
        assert!(!sync_failure_is_torch_download("some unrelated venv error"));
    }

    #[test]
    fn rocm_reinstall_args_target_the_rocm_index() {
        let args = rocm_torch_reinstall_args(ROCM_TORCH_INDEX);
        assert_eq!(args[0], "pip");
        assert_eq!(args[1], "install");
        assert!(args.iter().any(|a| a == "--reinstall"));
        assert!(args.iter().any(|a| a == "torch==2.8.0"));
        assert!(args.iter().any(|a| a == "torchaudio==2.8.0"));
        assert!(args.iter().any(|a| a == "torchvision==0.23.0"));
        let i = args.iter().position(|a| a == "--index-url").expect("has --index-url");
        // rocm6.4, not rocm6.2: rocm6.2's index tops out at torch 2.5.1 and
        // can't satisfy the app's torch==2.8.0 pin (#972) — a regression to
        // rocm6.2 here would silently resurrect the CPU-fallback bug.
        assert!(args[i + 1].contains("rocm6.4"), "default index is the rocm6.4 wheel set (matches torch==2.8.0)");
    }

    #[test]
    fn rocm_opt_in_gates_on_env_var_or_config() {
        // This test owns OMNIVOICE_TORCH_VARIANT / _INDEX for its duration; no
        // other test reads them.
        std::env::remove_var("OMNIVOICE_TORCH_VARIANT");
        std::env::remove_var("OMNIVOICE_TORCH_INDEX");
        assert!(rocm_opt_in("auto").is_none(), "unset+auto → no ROCm (default CUDA/CPU path)");
        assert_eq!(
            rocm_opt_in("rocm").as_deref(),
            Some(ROCM_TORCH_INDEX),
            "setup-screen config alone opts in"
        );

        std::env::set_var("OMNIVOICE_TORCH_VARIANT", "cuda");
        assert!(rocm_opt_in("rocm").is_none(), "env var wins over config (explicit non-rocm)");

        std::env::set_var("OMNIVOICE_TORCH_VARIANT", "ROCm");
        assert_eq!(rocm_opt_in("auto").as_deref(), Some(ROCM_TORCH_INDEX), "case-insensitive env opt-in → default index");

        std::env::set_var("OMNIVOICE_TORCH_INDEX", "https://example.test/rocm6.3");
        assert_eq!(rocm_opt_in("auto").as_deref(), Some("https://example.test/rocm6.3"), "index override honored");

        std::env::remove_var("OMNIVOICE_TORCH_VARIANT");
        std::env::remove_var("OMNIVOICE_TORCH_INDEX");
    }

    /// Unique scratch dir under the OS temp dir for the #314 venv-validity tests.
    /// Caller removes it at the end of the test.
    fn temp_venv_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "omnivoice-test-314-{}-{}",
            tag,
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("create temp venv dir");
        dir
    }

    /// Lay down the minimal healthy-venv skeleton: pyvenv.cfg + the python
    /// executable at the platform-correct location.
    fn write_healthy_venv_skeleton(venv: &Path) {
        fs::write(venv.join("pyvenv.cfg"), "home = /usr/local/bin\n").unwrap();
        let py = venv_python_path(venv);
        fs::create_dir_all(py.parent().unwrap()).unwrap();
        fs::write(&py, "#!fake interpreter\n").unwrap();
    }

    #[test]
    fn venv_structural_problem_none_when_venv_missing() {
        // #314: a venv path that doesn't exist is the first-run case — the
        // creation path owns it, the validator must stay out of the way.
        let dir = temp_venv_dir("absent");
        let venv = dir.join(".venv");
        assert!(venv_structural_problem(&venv).is_none());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn venv_structural_problem_none_for_healthy_venv() {
        // #314 / backward-compat hard rule: a healthy venv must never be
        // flagged (and therefore never deleted).
        let dir = temp_venv_dir("healthy");
        let venv = dir.join(".venv");
        fs::create_dir_all(&venv).unwrap();
        write_healthy_venv_skeleton(&venv);
        assert!(venv_structural_problem(&venv).is_none());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn venv_structural_problem_detects_missing_pyvenv_cfg() {
        // #314: the exact field condition of the bug report — python present,
        // pyvenv.cfg gone → venv launcher exits 106 "No pyvenv.cfg file".
        let dir = temp_venv_dir("no-cfg");
        let venv = dir.join(".venv");
        fs::create_dir_all(&venv).unwrap();
        write_healthy_venv_skeleton(&venv);
        fs::remove_file(venv.join("pyvenv.cfg")).unwrap();
        let problem = venv_structural_problem(&venv).expect("must flag missing pyvenv.cfg");
        assert!(problem.contains("pyvenv.cfg"), "reason names pyvenv.cfg: {}", problem);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn venv_structural_problem_detects_missing_python() {
        let dir = temp_venv_dir("no-python");
        let venv = dir.join(".venv");
        fs::create_dir_all(&venv).unwrap();
        write_healthy_venv_skeleton(&venv);
        fs::remove_file(venv_python_path(&venv)).unwrap();
        let problem = venv_structural_problem(&venv).expect("must flag missing python");
        assert!(problem.contains("python"), "reason names python: {}", problem);
        let _ = fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn venv_structural_problem_detects_dangling_python_symlink() {
        // #314: `bin/python` symlinks to a managed base interpreter; if that
        // interpreter was removed, the symlink dangles and the venv is dead.
        let dir = temp_venv_dir("dangling");
        let venv = dir.join(".venv");
        fs::create_dir_all(&venv).unwrap();
        write_healthy_venv_skeleton(&venv);
        let py = venv_python_path(&venv);
        fs::remove_file(&py).unwrap();
        std::os::unix::fs::symlink(dir.join("no-such-interpreter"), &py).unwrap();
        let problem = venv_structural_problem(&venv).expect("must flag dangling symlink");
        assert!(problem.contains("dangling"), "reason names the dangling link: {}", problem);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn quarantine_broken_venv_removes_only_the_venv() {
        // #314 safety property: only `.venv` goes away; sibling project files
        // (manifests, backend sources) are untouched.
        let dir = temp_venv_dir("quarantine");
        let venv = dir.join(".venv");
        fs::create_dir_all(venv.join("lib")).unwrap();
        fs::write(venv.join("lib").join("junk.py"), "x").unwrap();
        fs::write(dir.join("pyproject.toml"), "[project]\n").unwrap();
        assert!(quarantine_broken_venv(&venv), "quarantine must succeed");
        assert!(!venv.exists(), ".venv must be gone");
        assert!(dir.join("pyproject.toml").is_file(), "sibling files must survive");
        // Idempotent: quarantining an already-gone venv is a no-op success.
        assert!(quarantine_broken_venv(&venv));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn broken_venv_exit_signature_matches_106_and_pyvenv_message_only() {
        // #314: Windows venv launcher display + message.
        assert!(backend_exit_indicates_broken_venv("exit code: 106", ""));
        // Unix ExitStatus display.
        assert!(backend_exit_indicates_broken_venv("exit status: 106", ""));
        // Message in stderr tail wins regardless of the exit code text.
        assert!(backend_exit_indicates_broken_venv(
            "exit status: 1",
            "Fatal error: No pyvenv.cfg file"
        ));
        // Deliberately narrow: ordinary crashes must NOT trigger a rebuild.
        assert!(!backend_exit_indicates_broken_venv("exit status: 1", "Traceback ..."));
        assert!(!backend_exit_indicates_broken_venv("exit status: 1060", ""));
        assert!(!backend_exit_indicates_broken_venv("signal: 6 (SIGABRT)", ""));
        assert!(!backend_exit_indicates_broken_venv("never started", ""));
        // A relocated/copied venv whose interpreter can't bootstrap its stdlib
        // aborts with this exact phrase (exit 1, not 106) — must rebuild.
        assert!(backend_exit_indicates_broken_venv(
            "exit status: 1",
            "ModuleNotFoundError: No module named 'encodings'"
        ));
        // ...but an app-level import of an 'encodings'-prefixed package must NOT
        // (the full quoted phrase guards against this).
        assert!(!backend_exit_indicates_broken_venv(
            "exit status: 1",
            "ModuleNotFoundError: No module named 'encodings_helper'"
        ));
    }

    #[test]
    fn nonascii_pth_crash_signature_matches_the_real_traceback_only() {
        // #1783: the exact crash captured in #1771.
        let real_tail = "Fatal Python error: init_import_site: Failed to import the site module\n  \
File \"<frozen site>\", line 188, in addpackage\n\
UnicodeDecodeError: 'gbk' codec can't decode byte 0x80 in position 11: illegal multibyte sequence";
        assert!(backend_exit_indicates_nonascii_pth_crash(real_tail));
        // Both phrases are required — narrow match, same discipline as the
        // broken-venv signature.
        assert!(!backend_exit_indicates_nonascii_pth_crash("UnicodeDecodeError: ..."));
        assert!(!backend_exit_indicates_nonascii_pth_crash(
            "Fatal Python error: init_import_site: Failed to import the site module"
        ));
        // An ordinary app-level UnicodeDecodeError (unrelated to interpreter
        // startup) must not match.
        assert!(!backend_exit_indicates_nonascii_pth_crash(
            "UnicodeDecodeError: 'utf-8' codec can't decode byte in transcript.txt"
        ));
        assert!(!backend_exit_indicates_nonascii_pth_crash("Traceback ..."));
        assert!(!backend_exit_indicates_nonascii_pth_crash(""));
    }

    #[test]
    fn nonascii_pth_crash_diagnosis_is_correct_for_a_managed_install() {
        // #1783 acceptance: "the setup screen shows a specific message and a
        // fix, not the generic stall text." Pin the load-bearing ingredients
        // so a future edit can't silently drop them.
        //
        // greptile P1: the primary remedy must be reachable from the FAILED
        // screen this message is actually shown on (Retry / Clean & Retry
        // only — no "Change…" picker there), so the config-file edit is the
        // one asserted as the concrete action.
        let msg = nonascii_pth_crash_msg(false);
        assert!(msg.contains("config.json"));
        assert!(msg.contains("env_dir"));
        // Both call sites of this function are only reachable once
        // `!setup::is_first_run(app)` — the FirstRunSetup screen and its
        // "Change…"/"App environment" picker can never be showing at the
        // same time as this message, so it must never cite that control
        // (the exact defect this whole finding chain is about: naming an
        // action unreachable in the state the message actually appears in).
        assert!(!msg.contains("App environment"));
        // The portable-only remedy must never leak into the managed message
        // — moving "the whole VoiceStudio folder" would be nonsense advice
        // for an install that already has an ASCII-safe custom env_dir lever.
        assert!(!msg.contains("OmniVoiceStudio-Data"));
        assert!(!msg.contains("portable.path"));
        // CodeRabbit: never tell the user to run a specific fsutil command —
        // its retroactivity/per-volume semantics mean it may fix nothing.
        assert!(!msg.contains("fsutil"));
        assert!(msg.contains("#1783"));
    }

    #[test]
    fn nonascii_pth_crash_diagnosis_is_correct_for_a_portable_install() {
        // greptile P1 (third instance of the same defect class on this PR:
        // "the text names an action that doesn't work" for the state the
        // message is actually shown in). Verified directly against
        // `env_root`'s body: for `install_mode == "portable"` it returns
        // `portable_base().join("env")` and returns BEFORE the `env_dir`
        // check ever runs — so a portable user told to edit `env_dir` in
        // config.json follows the instructions exactly and hits the
        // identical crash on relaunch. The portable message must instead
        // name something that actually changes `portable_base()`'s
        // resolution (moving the app+data folder, or the `portable.path`
        // pointer file — see `portable_base`'s resolution order).
        let msg = nonascii_pth_crash_msg(true);
        assert!(msg.contains("OmniVoiceStudio-Data"));
        assert!(msg.contains("portable.path"));
        // The managed-only remedy must never leak into the portable message
        // without its "ignores env_dir" caveat — bare mention of the JSON
        // snippet here would be exactly the misleading advice this test
        // exists to catch.
        assert!(!msg.contains("\"env_dir\": \"C:/VoiceStudio/env\""));
        assert!(!msg.contains("App environment"));
        assert!(!msg.contains("fsutil"));
        assert!(msg.contains("#1783"));
    }

    // ── #1783: resolve_venv_root — the relocation-safety invariant ─────────

    #[test]
    fn a_healthy_existing_venv_at_a_nonascii_path_is_never_relocated() {
        // This is the regression a blunter fix would create: a Windows user
        // on a non-ASCII path whose venv already works (e.g. a code page
        // that happens to decode those bytes) must NOT lose a 9 GiB
        // environment and be forced to re-download it just because this
        // landed. `ascii_safe_root` differs from `true_root` (proving a
        // redirect target genuinely exists) — the decision must still be
        // Keep, because the venv exists and its probe carries no #1783
        // signature.
        let true_root = Path::new("/nonascii/root");
        let ascii_root = Path::new("/ascii/root");
        assert_eq!(
            resolve_venv_root(true_root, ascii_root, true, Some("")), // clean probe exit
            VenvRootDecision::Keep
        );
        // Same for a venv that fails to probe for an unrelated reason — that
        // is the EXISTING uvicorn/pkg_resources/omnivoice repair machinery's
        // job, untouched by this fix.
        assert_eq!(
            resolve_venv_root(true_root, ascii_root, true, Some("Traceback (most recent call last)...")),
            VenvRootDecision::Keep
        );
        // And when the probe couldn't even spawn the interpreter (None) —
        // also left to the existing logic, never relocated.
        assert_eq!(resolve_venv_root(true_root, ascii_root, true, None), VenvRootDecision::Keep);
    }

    #[test]
    fn identical_ascii_safe_and_true_roots_never_redirect() {
        // The overwhelming majority case: `ascii_safe_dir` is a no-op for an
        // ASCII path, so `ascii_safe_root == true_root` — nothing usable to
        // redirect TO, so this decides purely on whether the venv needs
        // fixing at all, never on relocating it.
        let root = Path::new("/ascii/root");
        assert_eq!(resolve_venv_root(root, root, true, Some("")), VenvRootDecision::Keep);
        assert_eq!(resolve_venv_root(root, root, false, None), VenvRootDecision::Keep);
        // A #1783-signature probe couldn't occur for a genuinely ASCII path
        // in practice, but the decision function doesn't get to assume that
        // — with nowhere better to go, it must fail specifically rather than
        // silently return Keep (which would hand back a venv that can never
        // start) or claim a redirect that changes nothing.
        assert_eq!(
            resolve_venv_root(root, root, true, Some("UnicodeDecodeError in init_import_site")),
            VenvRootDecision::Unfixable
        );
    }

    #[test]
    fn a_nonexistent_venv_redirects_to_an_available_ascii_safe_root() {
        // Case 1: nothing usable at the true (non-ASCII) path — a genuine
        // first run, or one just quarantined by the #314 structural check.
        // Building fresh at the ASCII-safe root costs nothing since there is
        // nothing to abandon.
        let true_root = Path::new("/nonascii/root");
        let ascii_root = Path::new("/ascii/root");
        assert_eq!(
            resolve_venv_root(true_root, ascii_root, false, None),
            VenvRootDecision::Redirect(ascii_root.to_path_buf())
        );
    }

    #[test]
    fn a_nonexistent_venv_at_a_nonascii_root_fails_fast_when_no_safe_alternative_exists() {
        // greptile P1: `ascii_safe_dir` returning the path UNCHANGED for a
        // non-ASCII `true_root` (8.3 short names unavailable) used to fall
        // through to `Keep` here — silently committing to a multi-GB
        // `uv sync` at a path already known unusable, and only diagnosing
        // the identical #1783 crash after the whole download completed. The
        // information needed to fail fast — true_root is non-ASCII AND
        // ascii_safe_root changed nothing — was already available before
        // any of that started, so this must be `Unfixable`, not `Keep`.
        //
        // Genuinely non-ASCII bytes (`\u{...}` escapes, matching setup.rs's
        // convention) — "/nonascii/root" elsewhere in this file is only a
        // descriptive LABEL, not actual non-ASCII content, and would have
        // let this exact bug slip past a less careful fixture.
        let true_root = PathBuf::from(format!("/{}/root", "\u{65e5}\u{672c}\u{8a9e}"));
        assert_eq!(
            resolve_venv_root(&true_root, &true_root, false, None),
            VenvRootDecision::Unfixable
        );
    }

    #[test]
    fn an_ascii_root_with_nothing_built_yet_is_always_kept_zero_cost() {
        // The other half of the same greptile finding: fixing the
        // non-ASCII-fails-fast case above must NOT over-trigger on the
        // overwhelming-majority ASCII case, which is trivially "redirect
        // changes nothing" too (`ascii_safe_root == true_root`) but for a
        // completely different, harmless reason. `is_ascii_path` is checked
        // explicitly inside `resolve_venv_root` rather than inferred from
        // `redirect_helps` alone, specifically so this stays `Keep`.
        let root = Path::new("/ascii/root");
        assert_eq!(resolve_venv_root(root, root, false, None), VenvRootDecision::Keep);
    }

    #[test]
    fn a_venv_with_the_1783_signature_redirects_when_a_safe_root_exists() {
        // Case 2: the venv exists but can never start where it is.
        let true_root = Path::new("/nonascii/root");
        let ascii_root = Path::new("/ascii/root");
        let crash_stderr = "Fatal Python error: init_import_site: ...\nUnicodeDecodeError: ...";
        assert_eq!(
            resolve_venv_root(true_root, ascii_root, true, Some(crash_stderr)),
            VenvRootDecision::Redirect(ascii_root.to_path_buf())
        );
    }

    #[test]
    fn a_venv_with_the_1783_signature_is_unfixable_when_redirect_cannot_help() {
        // 8.3 short names disabled (or any other reason `ascii_safe_dir`
        // couldn't produce a different path) — redirecting to the SAME path
        // achieves nothing, so this must fail with the specific diagnosis
        // rather than loop into the unrelated pkg_resources repair cascade.
        let root = Path::new("/nonascii/root");
        let crash_stderr = "Fatal Python error: init_import_site: ...\nUnicodeDecodeError: ...";
        assert_eq!(resolve_venv_root(root, root, true, Some(crash_stderr)), VenvRootDecision::Unfixable);
    }

    #[test]
    fn venv_rebuild_requires_confirmed_breakage() {
        // feat/safe-updates: an exit-signature match alone must not destroy a
        // venv. A structural problem is definitive evidence → rebuild.
        assert!(venv_rebuild_justified(Some("pyvenv.cfg is missing"), Some(true)));
        assert!(venv_rebuild_justified(Some("python executable is missing"), None));
        // No structural problem + interpreter provably healthy → NEVER delete
        // (the data-safety property this guard exists for).
        assert!(!venv_rebuild_justified(None, Some(true)));
        // Interpreter starts but can't bootstrap (exit 106 / encodings abort)
        // → confirmed broken → rebuild.
        assert!(venv_rebuild_justified(None, Some(false)));
        // Interpreter can't even be spawned → confirmed unrunnable → rebuild.
        assert!(venv_rebuild_justified(None, None));
    }

    #[cfg(unix)]
    #[test]
    fn venv_interpreter_probe_maps_exit_status_and_spawn_failure() {
        use std::os::unix::fs::PermissionsExt;
        // A nonexistent binary can't spawn → None (still justifies a rebuild).
        let missing = std::env::temp_dir().join("omnivoice-test-probe-missing-python");
        assert_eq!(venv_interpreter_probe(&missing), None);
        // Fake interpreters (exit 0 = healthy, exit 106 = the venv launcher's
        // "No pyvenv.cfg" code) exercise the status mapping without needing a
        // real python on the test runner.
        let dir = temp_venv_dir("probe");
        for (name, code, expected) in [("py-ok", 0, Some(true)), ("py-106", 106, Some(false))] {
            let script = dir.join(name);
            fs::write(&script, format!("#!/bin/sh\nexit {}\n", code)).unwrap();
            fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();
            assert_eq!(venv_interpreter_probe(&script), expected, "{}", name);
        }
        let _ = fs::remove_dir_all(&dir);
    }

    /// #248: verify that the setuptools repair install uses the correct specifier.
    /// The specifier `"setuptools>=75,<80"` must be passed as a single argument so
    /// pip/uv interprets the range constraint as one requirement, not two.
    #[test]
    fn setuptools_repair_uses_correct_specifier() {
        // Mirror the exact args slice used in both repair branches so a regression
        // (e.g. accidentally splitting into ["setuptools>=75", ",<80"]) is caught
        // here rather than silently installing the latest setuptools.
        let repair_args: &[&str] = &["pip", "install", "setuptools>=75,<80"];

        // The version specifier must be the third positional argument — one string,
        // not split. This is the key property the review bot flagged: a split arg
        // would make uv install the latest setuptools and leave pkg_resources absent.
        assert_eq!(repair_args[0], "pip");
        assert_eq!(repair_args[1], "install");
        assert_eq!(repair_args[2], "setuptools>=75,<80",
            "specifier must be a single arg; splitting it would bypass the <80 bound");

        // The single-string specifier must contain both bounds.
        let specifier = repair_args[2];
        assert!(specifier.contains("setuptools"), "arg must name the package");
        assert!(specifier.contains(">=75"), "lower bound must be >=75");
        assert!(specifier.contains("<80"), "upper bound must be <80 to keep pkg_resources");
        // No comma-split: the entire range is in one argument with no spaces.
        assert!(!specifier.contains(' '), "specifier must not contain spaces (would be split by shell)");

        // Verify 79.x satisfies the range
        let v79: (u32, u32) = (79, 0);
        assert!(v79.0 >= 75 && v79.0 < 80, "79.x must satisfy >=75,<80");
        // Verify 80.x does NOT satisfy
        let v80: (u32, u32) = (80, 0);
        assert!(!(v80.0 >= 75 && v80.0 < 80), "80.x must NOT satisfy <80");
        // Verify 82.x (what was installed before #224 fix) does NOT satisfy
        let v82: (u32, u32) = (82, 0);
        assert!(!(v82.0 >= 75 && v82.0 < 80), "82.x (pre-fix version) must NOT satisfy <80");
    }

    // -- cuDNN 8 compat side-load (real prod bootstrap, not just dev) --------

    #[cfg(windows)]
    #[test]
    fn cudnn8_compat_dir_matches_backend_main_py_layout() {
        // backend/main.py hardcodes `.venv/Lib/site-packages/cudnn8_compat` on
        // Windows (no pyver in the path) -- this must match exactly or the
        // ctypes preload never finds what we just installed.
        let venv_dir = PathBuf::from(r"C:\fake\project\.venv");
        let venv_py = venv_python_path(&venv_dir);
        let dir = cudnn8_compat_dir(&venv_dir, &venv_py).expect("windows path is pure, no subprocess needed");
        assert_eq!(dir, venv_dir.join("Lib").join("site-packages").join("cudnn8_compat"));
    }

    #[test]
    fn cudnn8_lib_dir_and_pattern_matches_platform_glob() {
        // Mirrors scripts/setup.py's _cudnn8_lib_dir()/_count_cudnn8_libs() and
        // backend/main.py's _cudnn8_glob exactly -- a divergence here means the
        // Rust installer and the Python ctypes preload disagree on what counts
        // as "installed".
        let compat_dir = PathBuf::from("compat");
        let (lib_dir, prefix, suffix) = cudnn8_lib_dir_and_pattern(&compat_dir);
        if cfg!(windows) {
            assert_eq!(lib_dir, compat_dir.join("nvidia").join("cudnn").join("bin"));
            assert_eq!((prefix, suffix), ("cudnn", "64_8.dll"));
            assert!("cudnn_ops64_8.dll".starts_with(prefix) && "cudnn_ops64_8.dll".ends_with(suffix));
        } else {
            assert_eq!(lib_dir, compat_dir.join("nvidia").join("cudnn").join("lib"));
            assert_eq!((prefix, suffix), ("libcudnn", ".so.8"));
            assert!("libcudnn_ops.so.8".starts_with(prefix) && "libcudnn_ops.so.8".ends_with(suffix));
        }
    }

    #[test]
    fn count_cudnn8_libs_counts_only_matching_files() {
        let dir = temp_venv_dir("cudnn-count");
        let (_, prefix, suffix) = cudnn8_lib_dir_and_pattern(Path::new(""));
        // Two real matches...
        fs::write(dir.join(format!("{prefix}_a{suffix}")), b"").unwrap();
        fs::write(dir.join(format!("{prefix}_b{suffix}")), b"").unwrap();
        // ...one file that only matches the prefix, one that only matches the
        // suffix, and one totally unrelated file -- none of these should count.
        fs::write(dir.join(format!("{prefix}_only_prefix.txt")), b"").unwrap();
        fs::write(dir.join(format!("unrelated{suffix}")), b"").unwrap();
        fs::write(dir.join("readme.md"), b"").unwrap();
        assert_eq!(count_cudnn8_libs(&dir, prefix, suffix), 2);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn count_cudnn8_libs_zero_when_dir_missing() {
        // First-run case: the compat dir doesn't exist yet -- must report 0,
        // not error, so the caller's ">= 5" threshold cleanly triggers install.
        let missing = std::env::temp_dir().join("omnivoice-test-cudnn8-does-not-exist");
        let _ = fs::remove_dir_all(&missing);
        assert_eq!(count_cudnn8_libs(&missing, "cudnn", "64_8.dll"), 0);
    }

    #[test]
    fn classify_cuda_probe_gates_install_on_cuda_only() {
        // 'cuda' (CUDA build + live device) is the ONLY verdict that triggers
        // the ~700 MB nvidia-cudnn-cu12 download.
        assert_eq!(classify_cuda_probe("cuda"), CudnnProbe::Install);
        assert_eq!(classify_cuda_probe("cuda\n"), CudnnProbe::Install); // print() newline
        // ROCm torch spoofs torch.cuda.is_available(); the probe reports
        // 'hip' first so opt-in AMD installs (#124) never fetch the CUDA
        // wheel -- and the negative is cacheable.
        assert_eq!(classify_cuda_probe("hip\n"), CudnnProbe::CacheNegative);
        // Plain no-CUDA box: cache so `import torch` never re-runs at launch.
        assert_eq!(classify_cuda_probe("none"), CudnnProbe::CacheNegative);
        // Broken venv / import error / garbage: skip this launch but never
        // cache -- a transient failure must not wedge a real CUDA machine.
        assert_eq!(classify_cuda_probe(""), CudnnProbe::SkipNoCache);
        assert_eq!(
            classify_cuda_probe("Traceback (most recent call last):"),
            CudnnProbe::SkipNoCache
        );
    }

    #[test]
    fn cudnn8_probe_cache_marker_roundtrip() {
        let venv_dir = temp_venv_dir("cudnn-probe-cache");
        let marker = cudnn8_probe_marker(&venv_dir);
        // Must live INSIDE the venv so a full rebuild clears it implicitly.
        assert!(marker.starts_with(&venv_dir));
        assert!(!marker.is_file());
        fs::write(&marker, "none\n").unwrap();
        assert!(marker.is_file());
        // Re-sync invalidation: marker gone, next launch re-probes.
        invalidate_cudnn8_probe_cache(&venv_dir);
        assert!(!marker.is_file());
        // Idempotent when the marker is already absent.
        invalidate_cudnn8_probe_cache(&venv_dir);
        assert!(!marker.is_file());
        let _ = fs::remove_dir_all(&venv_dir);
    }

    #[test]
    fn failed_command_message_carries_the_newest_relevant_output() {
        let logs = vec![
            LogPayload {
                stage: "downloading_uv".into(),
                line: "unrelated".into(),
            },
            LogPayload {
                stage: "installing_deps".into(),
                line: "resolver context".into(),
            },
            LogPayload {
                stage: "installing_deps".into(),
                line: "actual dependency conflict".into(),
            },
        ];
        let tail = buffered_log_tail(&logs, "installing_deps", 1);
        let failure = command_failure_message(
            "Repair uv sync failed",
            &Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "access denied",
            )),
            &tail,
        );

        assert!(failure.contains("command error: access denied"));
        assert!(failure.contains("Last output:\nactual dependency conflict"));
        assert!(!failure.contains("resolver context"));
        assert!(!failure.contains("unrelated"));
    }
}

#[cfg(test)]
mod failure_preservation_tests {
    use super::*;

    fn stage(s: BootstrapStage) -> Arc<Mutex<BootstrapStage>> {
        Arc::new(Mutex::new(s))
    }

    /// #1112: the venv bootstrap diagnoses the REAL reason (Intel Mac, uv sync
    /// failure, blocked GitHub) and records it as Failed. The spawn watcher, on
    /// seeing "no child ever started", must NOT replace that with the generic
    /// "never started — no error output captured": doing so left the user with a
    /// causeless message AND stopped the UI's hint matcher (which keys off the
    /// specific text) from ever firing, so they were offered a Retry that could
    /// never work.
    #[test]
    fn a_specific_failure_is_recognised_as_already_diagnosed() {
        let s = stage(BootstrapStage::Failed {
            message: INTEL_MAC_UNSUPPORTED_MSG.to_string(),
        });
        assert!(already_diagnosed(&s));
    }

    #[test]
    fn a_non_failed_stage_is_not_diagnosed_so_the_generic_message_still_forms() {
        // A real crash after a successful start, or a raw exec failure: nobody
        // diagnosed it, so the spawn watcher's message is the only one there is.
        for st in [
            BootstrapStage::Checking,
            BootstrapStage::StartingBackend,
            BootstrapStage::Ready,
            BootstrapStage::InstallingDeps,
        ] {
            assert!(!already_diagnosed(&stage(st)));
        }
    }

    /// The Intel-Mac message must keep the exact wording the frontend hint
    /// matcher greps for — if this drifts, the user silently loses the only
    /// hint that tells them retrying is pointless.
    #[test]
    fn intel_mac_message_matches_what_the_ui_hint_matcher_greps_for() {
        assert!(INTEL_MAC_UNSUPPORTED_MSG.contains("Intel Macs can't run the local AI backend"));
    }

    /// #1177: a `Failed` diagnosis must outlive the stage that carried it.
    ///
    /// `Failed` is not durable — a Retry sets `Checking` and the supervisor
    /// sets `StartingBackend` before every respawn, each overwriting the only
    /// copy of why the last start failed. The frontend asks for the diagnosis
    /// when a request finally gives up, which is routinely AFTER one of those
    /// transitions; without retention it finds nothing and the user is back to
    /// an evidence-free "can't reach the backend".
    ///
    /// Drives a test-owned retention slot via `set_stage_into` rather than the
    /// process-global one: `cargo test` runs this binary's tests in parallel,
    /// so mutating the global here would race any future test that asserts on
    /// `last_failure_message()`, and would leak a value with no teardown.
    #[test]
    fn a_failed_diagnosis_survives_later_stage_transitions() {
        let s = stage(BootstrapStage::Checking);
        let slot: Mutex<Option<String>> = Mutex::new(None);
        let retained = || slot.lock().unwrap().clone();

        set_stage_into(&s, &slot, BootstrapStage::Failed { message: "uv sync failed".into() });
        assert_eq!(retained().as_deref(), Some("uv sync failed"));

        // The supervisor moves on to a respawn — the stage stops being Failed…
        set_stage_into(&s, &slot, BootstrapStage::StartingBackend);
        assert!(!already_diagnosed(&s));
        // …but the reason is still retrievable.
        assert_eq!(retained().as_deref(), Some("uv sync failed"));

        // A newer failure replaces the older one (the newest is the actionable
        // one; a stale reason would misdiagnose the current state).
        let intel = BootstrapStage::Failed { message: INTEL_MAC_UNSUPPORTED_MSG.to_string() };
        set_stage_into(&s, &slot, intel);
        assert_eq!(retained().as_deref(), Some(INTEL_MAC_UNSUPPORTED_MSG));
    }

    /// A non-failed stage must never write the retention slot — otherwise a
    /// healthy transition would erase the diagnosis the slot exists to keep.
    #[test]
    fn a_non_failed_stage_never_touches_the_retention_slot() {
        let s = stage(BootstrapStage::Checking);
        let slot: Mutex<Option<String>> = Mutex::new(Some("earlier reason".into()));
        for st in [BootstrapStage::Checking, BootstrapStage::StartingBackend, BootstrapStage::Ready]
        {
            set_stage_into(&s, &slot, st);
        }
        assert_eq!(slot.lock().unwrap().as_deref(), Some("earlier reason"));
    }

    /// Wiring check: the public `set_stage` must write the SAME global slot
    /// that `last_failure_message()` (and the `last_bootstrap_failure` command)
    /// reads back, or the frontend asks the shell and always gets `None`.
    ///
    /// The only test that touches the process-global slot. It asserts a value
    /// it wrote itself and never asserts absence, so a parallel test writing a
    /// different message cannot make it flake. Any FUTURE test asserting on the
    /// global must use `set_stage_into` with its own slot instead.
    #[test]
    fn set_stage_wires_the_global_slot_to_the_public_reader() {
        let s = stage(BootstrapStage::Checking);
        let unique = format!("wiring probe {:?}", std::thread::current().id());
        set_stage(&s, BootstrapStage::Failed { message: unique.clone() });
        assert_eq!(last_failure_message().as_deref(), Some(unique.as_str()));
    }
}

/// #1770: attach-handshake code fingerprint — plain functions, no
/// `AppHandle` (a `tauri::test::mock_builder` test aborts the whole test
/// binary on the Windows CI runner, which broke a PR earlier today; the fix
/// there was exactly this — split the pure decision out and test that).
#[cfg(test)]
mod code_fingerprint_tests {
    use super::*;

    #[test]
    fn hash_python_sources_changes_with_content_and_ignores_noise() {
        let backend = tempfile::tempdir().unwrap();
        fs::write(backend.path().join("main.py"), "print('v1')").unwrap();
        fs::create_dir_all(backend.path().join("api")).unwrap();
        fs::write(backend.path().join("api").join("schemas.py"), "class X: pass").unwrap();

        let baseline = hash_python_sources(&[backend.path().to_path_buf()]).unwrap();

        // Same content hashed again -> identical fingerprint (deterministic).
        assert_eq!(hash_python_sources(&[backend.path().to_path_buf()]).unwrap(), baseline);

        // __pycache__ / dotdirs (bytecode caches, .pytest_cache — exactly what
        // dev mode litters a live-executed source tree with) never move it.
        fs::create_dir_all(backend.path().join("__pycache__")).unwrap();
        fs::write(backend.path().join("__pycache__").join("main.cpython-311.pyc"), "junk").unwrap();
        fs::create_dir_all(backend.path().join(".pytest_cache")).unwrap();
        fs::write(backend.path().join(".pytest_cache").join("v"), "junk").unwrap();
        assert_eq!(hash_python_sources(&[backend.path().to_path_buf()]).unwrap(), baseline);

        // Actual code changing -> the fingerprint changes (the whole point:
        // this is what `same_app_version` alone cannot see, #1770).
        fs::write(backend.path().join("main.py"), "print('v2')").unwrap();
        assert_ne!(hash_python_sources(&[backend.path().to_path_buf()]).unwrap(), baseline);
    }

    #[test]
    fn hash_python_sources_is_none_for_no_py_files() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("README.md"), "no python here").unwrap();
        assert_eq!(hash_python_sources(&[dir.path().to_path_buf()]), None);
    }

    #[test]
    fn hash_python_sources_separates_directories_by_label() {
        // backend/x.py and omnivoice/x.py must not collide onto the same
        // fingerprint entry.
        let backend = tempfile::tempdir().unwrap();
        let backend_dir = backend.path().join("backend");
        fs::create_dir_all(&backend_dir).unwrap();
        fs::write(backend_dir.join("x.py"), "A").unwrap();

        let omnivoice = tempfile::tempdir().unwrap();
        let omnivoice_dir = omnivoice.path().join("omnivoice");
        fs::create_dir_all(&omnivoice_dir).unwrap();
        fs::write(omnivoice_dir.join("x.py"), "B").unwrap();

        let both = hash_python_sources(&[backend_dir.clone(), omnivoice_dir.clone()]).unwrap();
        let backend_only = hash_python_sources(&[backend_dir]).unwrap();
        assert_ne!(both, backend_only);
    }
}
