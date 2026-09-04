//! Backend-lifecycle fault-injection harness.
//!
//! Runs `spawn_backend_and_wait` / `supervise_backend` against REAL dying
//! child processes (via the `OMNIVOICE_BACKEND_CMD` seam) and asserts the
//! user receives the CORRECT NAMED DIAGNOSIS — not merely that recovery
//! happened. Diagnosis quality is the bar: 61% of the historical "can't
//! reach the backend" class was closed undiagnosed.
//!
//! The scenario "backend" is this test binary re-invoking itself
//! (`scenario_child`), so exit codes, Unix signals, and pipe-close ordering
//! are the genuine OS articles on all three platforms — no system python,
//! no mocks of the behaviors under test.
//!
//! Every test mutates process-global state (env vars, the crash store, the
//! kill-intended flag), so they hold one mutex AND CI runs this binary with
//! `--test-threads=1`.

use std::io::{Read, Write};
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use tauri::Listener;
use tauri::Manager;

use app_lib::bootstrap::{
    run_streaming, set_backend_kill_intended, spawn_backend_and_wait, with_backend_stopped,
    BootstrapStage, BootstrapState, LogPayload,
};
use app_lib::uninstall::{purge_uninstall_targets, UninstallTarget};
use app_lib::{
    shutdown_backend_for_exit, AppFlags, AttachedHealthState, BackendState,
    CaptureDispatchState,
};

static HARNESS: Mutex<()> = Mutex::new(());

#[cfg(unix)]
static SCENARIO_TERM_REQUESTED: AtomicBool = AtomicBool::new(false);

#[cfg(unix)]
extern "C" fn scenario_term_handler(_: libc::c_int) {
    SCENARIO_TERM_REQUESTED.store(true, std::sync::atomic::Ordering::SeqCst);
}

#[cfg(unix)]
fn finish_graceful_scenario_shutdown() -> bool {
    if !SCENARIO_TERM_REQUESTED.load(std::sync::atomic::Ordering::SeqCst) {
        return false;
    }
    if let Ok(path) = std::env::var("OMNIVOICE_SCENARIO_SHUTDOWN_SENTINEL") {
        let _ = std::fs::write(path, b"clean");
    }
    true
}

#[cfg(not(unix))]
fn finish_graceful_scenario_shutdown() -> bool {
    false
}

// ── Scenario child ────────────────────────────────────────────────────────

/// Not a real test: when `OMNIVOICE_SCENARIO` is set, this plays the backend
/// — optionally serving minimal HTTP on `OMNIVOICE_PORT`, printing a stderr
/// script, then dying the scripted death. A no-op in a normal test pass.
#[test]
fn scenario_child() {
    // The gate value is the PID of the process that ARMED the scenario (the
    // parent harness). The parent's own libtest also runs this test — in a
    // parallel local `cargo test` it could observe the armed env and start
    // fault-injecting itself (binding the port, idling 600s). Only a
    // DIFFERENT process — the spawned child — may play the backend.
    match std::env::var("OMNIVOICE_SCENARIO") {
        Ok(v) if v.parse::<u32>() == Ok(std::process::id()) => return, // the parent itself
        Ok(_) => {}
        Err(_) => return,
    }
    if std::env::var("OMNIVOICE_SCENARIO_DRAIN_WRAPPER").as_deref() == Ok("1") {
        #[cfg(unix)]
        unsafe {
            assert!(libc::setsid() >= 0, "drain wrapper failed to escape outer group");
        }
        if let Some(path) = std::env::var_os("OMNIVOICE_SCENARIO_DRAIN_WRAPPER_LOG") {
            std::fs::write(path, std::process::id().to_string())
                .expect("record drain wrapper pid");
        }
        #[cfg(unix)]
        unsafe {
            libc::raise(libc::SIGSTOP);
        }
        return;
    }
    if std::env::var("OMNIVOICE_SCENARIO_DESCENDANT").as_deref() == Ok("1") {
        #[cfg(unix)]
        unsafe {
            if std::env::var_os("OMNIVOICE_SCENARIO_DESCENDANT_SETSID").is_some()
                && std::env::var("OMNIVOICE_DESKTOP_CONTAINED").as_deref() != Ok("1")
            {
                assert!(
                    libc::setsid() >= 0,
                    "scenario descendant failed to escape session"
                );
            }
            // Exercise the bounded SIGKILL fallback: the backend parent still
            // handles SIGTERM and writes its cleanup sentinel, while this
            // engine-like descendant deliberately ignores the graceful phase.
            libc::signal(libc::SIGTERM, libc::SIG_IGN);
        }
        if let Some(ready) = std::env::var_os("OMNIVOICE_SCENARIO_DESCENDANT_READY") {
            std::fs::write(ready, b"ready").expect("record escaped descendant readiness");
        }
        std::thread::sleep(Duration::from_secs(600));
        return;
    }
    let get = |k: &str| std::env::var(k).unwrap_or_default();
    let get_ms = |k: &str| get(k).parse::<u64>().ok();

    let spawn_log = get("OMNIVOICE_SCENARIO_SPAWN_LOG");
    if !spawn_log.is_empty() {
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(spawn_log)
            .expect("open scenario spawn log");
        writeln!(file, "{}", std::process::id()).expect("record scenario child");
    }

    let descendant_log = get("OMNIVOICE_SCENARIO_DESCENDANT_LOG");
    if !descendant_log.is_empty() {
        let spawn_descendant = move || {
            let trigger = get("OMNIVOICE_SCENARIO_DESCENDANT_TRIGGER");
            while !trigger.is_empty() && !std::path::Path::new(&trigger).exists() {
                std::thread::sleep(Duration::from_millis(10));
            }
            let exe = std::env::current_exe().expect("current test executable");
            let child = std::process::Command::new(exe)
                .args(["scenario_child", "--exact", "--nocapture"])
                .env("OMNIVOICE_SCENARIO_DESCENDANT", "1")
                .spawn()
                .expect("spawn scenario descendant");
            std::fs::write(descendant_log, child.id().to_string())
                .expect("record scenario descendant");
            drop(child);
        };
        if get("OMNIVOICE_SCENARIO_DESCENDANT_TRIGGER").is_empty() {
            spawn_descendant();
        } else {
            std::thread::spawn(spawn_descendant);
        }
    }

    let drain_wrapper_log = get("OMNIVOICE_SCENARIO_DRAIN_WRAPPER_LOG");
    if !drain_wrapper_log.is_empty() {
        let exe = std::env::current_exe().expect("current test executable");
        std::process::Command::new(exe)
            .args(["scenario_child", "--exact", "--nocapture"])
            .env("OMNIVOICE_SCENARIO_DRAIN_WRAPPER", "1")
            .spawn()
            .expect("spawn escaped drain wrapper");
    }

    #[cfg(unix)]
    if !get("OMNIVOICE_SCENARIO_SHUTDOWN_SENTINEL").is_empty() {
        SCENARIO_TERM_REQUESTED.store(false, std::sync::atomic::Ordering::SeqCst);
        unsafe {
            libc::signal(
                libc::SIGTERM,
                scenario_term_handler as *const () as libc::sighandler_t,
            );
        }
    }

    if let Some(delay) = get_ms("OMNIVOICE_SCENARIO_START_DELAY_MS") {
        std::thread::sleep(Duration::from_millis(delay));
    }

    // Serve /system/info + /profiles (the two probes behind backend_ready)
    // and /startup/progress (marker-stamped) for the given window; 0 = serve
    // forever.
    if let Some(serve_ms) = get_ms("OMNIVOICE_SCENARIO_SERVE_MS") {
        let port: u16 = get("OMNIVOICE_PORT").parse().expect("OMNIVOICE_PORT");
        let progress_only = get("OMNIVOICE_SCENARIO_PROGRESS_ONLY") == "1";
        let listener = match std::net::TcpListener::bind(("127.0.0.1", port)) {
            Ok(listener) => listener,
            Err(error) if error.kind() == std::io::ErrorKind::AddrInUse => {
                eprintln!("FATAL: scenario backend could not bind port {port}: {error}");
                std::process::exit(app_lib::backend::EXIT_PORT_IN_USE);
            }
            Err(error) => panic!("bind scenario port: {error}"),
        };
        listener.set_nonblocking(true).unwrap();
        let deadline = if serve_ms == 0 {
            None
        } else {
            Some(Instant::now() + Duration::from_millis(serve_ms))
        };
        loop {
            if finish_graceful_scenario_shutdown() {
                return;
            }
            if let Some(d) = deadline {
                if Instant::now() >= d {
                    break;
                }
            }
            match listener.accept() {
                Ok((mut stream, _)) => {
                    let mut buf = [0u8; 512];
                    let _ = stream.set_read_timeout(Some(Duration::from_millis(200)));
                    let n = stream.read(&mut buf).unwrap_or(0);
                    let req = String::from_utf8_lossy(&buf[..n]);
                    let resp = if get("OMNIVOICE_SCENARIO_FOREIGN") == "1" {
                        "HTTP/1.1 200 OK\r\nContent-Length: 21\r\n\r\n{\"service\":\"foreign\"}"
                            .to_string()
                    } else if req.starts_with("GET /startup/progress") {
                        let body = r#"{"status": "starting", "step": "ml_imports", "label": "Loading ML runtime (PyTorch)_"}"#;
                        format!(
                            "HTTP/1.1 200 OK\r\nx-omnivoice-backend: 0.0.0\r\nContent-Length: {}\r\n\r\n{}",
                            body.len(), body
                        )
                    } else if progress_only {
                        "HTTP/1.1 503 X\r\nContent-Length: 0\r\n\r\n".to_string()
                    } else if req.starts_with("GET /system/info") {
                        // #1770: this scenario child is a genuine, current
                        // build of this binary — but launched via the
                        // OMNIVOICE_BACKEND_CMD fault-injection seam (or as
                        // a hand-spawned "external" process in the attach
                        // tests below), never through spawn_backend's normal
                        // path, so it never receives OMNIVOICE_BUILD_FINGERPRINT.
                        // That's exactly the "current schema, no env var"
                        // shape a legitimate external/manually-started
                        // backend has, so it serves `code_fingerprint: ""`
                        // by default — the one case OMNIVOICE_SCENARIO_NO_CODE_FINGERPRINT
                        // opts out of, to model a backend that predates the
                        // fingerprinting mechanism outright.
                        let body = if get("OMNIVOICE_SCENARIO_NO_CODE_FINGERPRINT") == "1" {
                            format!(
                                r#"{{"data_dir": "/x", "app_version": "{}"}}"#,
                                env!("CARGO_PKG_VERSION")
                            )
                        } else {
                            format!(
                                r#"{{"data_dir": "/x", "app_version": "{}", "code_fingerprint": ""}}"#,
                                env!("CARGO_PKG_VERSION")
                            )
                        };
                        format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{}", body.len(), body)
                    } else if req.starts_with("GET /profiles")
                        && std::env::var_os("OMNIVOICE_SCENARIO_HEALTH_FAIL_FILE")
                            .is_some_and(|path| std::path::Path::new(&path).exists())
                    {
                        "HTTP/1.1 503 X\r\nContent-Length: 0\r\n\r\n".to_string()
                    } else {
                        "HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n[]".to_string()
                    };
                    let _ = stream.write_all(resp.as_bytes());
                }
                Err(_) => std::thread::sleep(Duration::from_millis(20)),
            }
        }
    }

    let stderr_script = get("OMNIVOICE_SCENARIO_STDERR");
    if !stderr_script.is_empty() {
        // \n-encoded so a multi-line traceback fits in one env var.
        eprintln!("{}", stderr_script.replace("\\n", "\n"));
        let _ = std::io::stderr().flush();
        // Let the shell's drainer thread pull the pipe before death.
        std::thread::sleep(Duration::from_millis(150));
    }

    #[cfg(unix)]
    if get("OMNIVOICE_SCENARIO_SIGNAL") == "9" {
        unsafe { libc::raise(libc::SIGKILL) };
    }
    if let Some(code) = get_ms("OMNIVOICE_SCENARIO_EXIT") {
        std::process::exit(code as i32);
    }
    if !get("OMNIVOICE_SCENARIO_SHUTDOWN_SENTINEL").is_empty() {
        loop {
            if finish_graceful_scenario_shutdown() {
                return;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }
    // Scripted to serve forever / be killed externally: idle out.
    std::thread::sleep(Duration::from_secs(600));
}

// ── Harness plumbing ──────────────────────────────────────────────────────

struct Scenario<'a> {
    stderr: &'a str,
    exit: Option<i32>,
    signal9: bool,
    serve_ms: Option<u64>,
    progress_only: bool,
    foreign: bool,
    /// #1770: serve `/system/info` with NO `code_fingerprint` key at all —
    /// the pre-fingerprinting shape a same-version backend from before this
    /// fix has. Default (false) serves `code_fingerprint: ""`, modeling a
    /// current-build backend that just wasn't launched with
    /// `OMNIVOICE_BUILD_FINGERPRINT` set (every scenario child here, since
    /// none go through spawn_backend's normal path).
    no_code_fingerprint: bool,
}

impl Default for Scenario<'_> {
    fn default() -> Self {
        Scenario {
            stderr: "",
            exit: None,
            signal9: false,
            serve_ms: None,
            progress_only: false,
            foreign: false,
            no_code_fingerprint: false,
        }
    }
}

const SCENARIO_ENV: &[&str] = &[
    "OMNIVOICE_SCENARIO",
    "OMNIVOICE_SCENARIO_STDERR",
    "OMNIVOICE_SCENARIO_EXIT",
    "OMNIVOICE_SCENARIO_SIGNAL",
    "OMNIVOICE_SCENARIO_SERVE_MS",
    "OMNIVOICE_SCENARIO_PROGRESS_ONLY",
    "OMNIVOICE_SCENARIO_FOREIGN",
    "OMNIVOICE_SCENARIO_NO_CODE_FINGERPRINT",
    "OMNIVOICE_SCENARIO_START_DELAY_MS",
    "OMNIVOICE_SCENARIO_SPAWN_LOG",
    "OMNIVOICE_SCENARIO_DESCENDANT",
    "OMNIVOICE_SCENARIO_DESCENDANT_LOG",
    "OMNIVOICE_SCENARIO_DESCENDANT_SETSID",
    "OMNIVOICE_SCENARIO_DESCENDANT_READY",
    "OMNIVOICE_SCENARIO_DESCENDANT_TRIGGER",
    "OMNIVOICE_SCENARIO_SHUTDOWN_SENTINEL",
    "OMNIVOICE_SCENARIO_DRAIN_WRAPPER",
    "OMNIVOICE_SCENARIO_DRAIN_WRAPPER_LOG",
    "OMNIVOICE_SCENARIO_HEALTH_FAIL_FILE",
    "OMNIVOICE_TEST_BEFORE_TRACK_ENTERED",
    "OMNIVOICE_TEST_BEFORE_TRACK_RELEASE",
    "OMNIVOICE_TEST_AFTER_TRACK_ENTERED",
    "OMNIVOICE_TEST_AFTER_TRACK_RELEASE",
    "OMNIVOICE_BACKEND_CMD",
    "OMNIVOICE_LOG_DIR",
    "OMNIVOICE_PORT",
    "OMNIVOICE_STARTUP_BUDGET_S",
    "OMNIVOICE_SUPERVISOR_POLL_MS",
    "OMNIVOICE_ATTACHED_FAILURE_GRACE_MS",
    "OMNIVOICE_TEST_FORCE_STOP_ERROR",
    "OMNIVOICE_TEST_FORCE_INCOMPLETE_STOP_ERROR",
    "OMNIVOICE_TEST_NESTED_DRAIN_TIMEOUT_MS",
];

struct TestApp {
    app: tauri::App<tauri::test::MockRuntime>,
    stage: Arc<Mutex<BootstrapStage>>,
    logs: Arc<Mutex<Vec<LogPayload>>>,
    _logdir: tempfile::TempDir,
    _guard: MutexGuard<'static, ()>,
}

impl TestApp {
    fn new(scenario: &Scenario) -> Self {
        let guard = HARNESS.lock().unwrap_or_else(|e| e.into_inner());
        for k in SCENARIO_ENV {
            std::env::remove_var(k);
        }
        // Reset the retry-flow flag a previous scenario may have left set.
        set_backend_kill_intended(false);

        let logdir = tempfile::tempdir().expect("logdir");
        std::env::set_var("OMNIVOICE_LOG_DIR", logdir.path());
        // Fresh ephemeral port per scenario.
        let port = {
            let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            l.local_addr().unwrap().port()
        };
        std::env::set_var("OMNIVOICE_PORT", port.to_string());
        std::env::set_var("OMNIVOICE_STARTUP_BUDGET_S", "6");
        std::env::set_var("OMNIVOICE_SUPERVISOR_POLL_MS", "100");
        std::env::set_var("OMNIVOICE_ATTACHED_FAILURE_GRACE_MS", "300");

        let exe = std::env::current_exe().expect("current_exe");
        std::env::set_var(
            "OMNIVOICE_BACKEND_CMD",
            serde_json::to_string(&[
                exe.to_string_lossy().as_ref(),
                "scenario_child",
                "--exact",
                "--nocapture",
            ])
            .unwrap(),
        );
        // Armed with OUR pid: the in-process scenario_child test sees its own
        // pid and stays inert; only the spawned child (a different pid) runs.
        std::env::set_var("OMNIVOICE_SCENARIO", std::process::id().to_string());
        if !scenario.stderr.is_empty() {
            std::env::set_var("OMNIVOICE_SCENARIO_STDERR", scenario.stderr);
        }
        if let Some(code) = scenario.exit {
            std::env::set_var("OMNIVOICE_SCENARIO_EXIT", code.to_string());
        }
        if scenario.signal9 {
            std::env::set_var("OMNIVOICE_SCENARIO_SIGNAL", "9");
        }
        if let Some(ms) = scenario.serve_ms {
            std::env::set_var("OMNIVOICE_SCENARIO_SERVE_MS", ms.to_string());
        }
        if scenario.progress_only {
            std::env::set_var("OMNIVOICE_SCENARIO_PROGRESS_ONLY", "1");
        }
        if scenario.foreign {
            std::env::set_var("OMNIVOICE_SCENARIO_FOREIGN", "1");
        }
        if scenario.no_code_fingerprint {
            std::env::set_var("OMNIVOICE_SCENARIO_NO_CODE_FINGERPRINT", "1");
        }

        let app = tauri::test::mock_builder()
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .expect("mock app");
        app.manage(BackendState {
            lifecycle: Mutex::new(()),
            process: Mutex::new(None),
            owned_tree: Mutex::new(None),
            attached: AtomicBool::new(false),
            attached_health: Mutex::new(AttachedHealthState::default()),
            spawned_at: Mutex::new(None),
        });
        app.manage(AppFlags {
            quitting: AtomicBool::new(false),
            uninstalling: AtomicBool::new(false),
            uninstall_owner: std::sync::atomic::AtomicU64::new(0),
            dictating: AtomicBool::new(false),
            capture: Mutex::new(CaptureDispatchState::default()),
            output: app_lib::dictation_output::DictationOutput::default(),
        });
        let stage = Arc::new(Mutex::new(BootstrapStage::Checking));
        let logs: Arc<Mutex<Vec<LogPayload>>> = Arc::new(Mutex::new(Vec::new()));
        app.manage(BootstrapState { stage: stage.clone(), logs: logs.clone() });
        TestApp { app, stage, logs, _logdir: logdir, _guard: guard }
    }

    fn handle(&self) -> tauri::AppHandle<tauri::test::MockRuntime> {
        self.app.handle().clone()
    }

    /// Run the bootstrap on a thread; the returned closure joins it with a
    /// hard timeout so a wiring regression fails red instead of hanging CI.
    fn run_bootstrap(&self) -> std::thread::JoinHandle<()> {
        let handle = self.handle();
        let stage = self.stage.clone();
        std::thread::spawn(move || spawn_backend_and_wait(&handle, &stage))
    }

    fn stage_snapshot(&self) -> BootstrapStage {
        self.stage.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    fn failed_message(&self) -> Option<String> {
        match self.stage_snapshot() {
            BootstrapStage::Failed { message } => Some(message),
            _ => None,
        }
    }

    fn markers(&self) -> app_lib::crash::CrashStore {
        app_lib::crash::load_store_from(&app_lib::crash::markers_path())
    }

    fn record_events(&self, name: &'static str) -> Arc<Mutex<Vec<String>>> {
        let seen: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
        let seen2 = seen.clone();
        self.app.handle().listen(name, move |_ev| {
            seen2.lock().unwrap_or_else(|e| e.into_inner()).push(name.to_string());
        });
        seen
    }

    fn kill_tracked_child(&self) {
        let state = self.app.state::<BackendState>();
        let guard = state.process.lock();
        if let Ok(mut guard) = guard {
            if let Some(child) = guard.as_mut() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }

    /// Model a real root crash: signal through Child's stable handle but leave
    /// the zombie unreaped so its process-group identity cannot be reused
    /// before the supervisor drains every descendant.
    fn signal_tracked_child(&self) {
        let state = self.app.state::<BackendState>();
        if let Ok(mut guard) = state.process.lock() {
            if let Some(child) = guard.as_mut() {
                let _ = child.kill();
            }
        };
    }

    fn quit(&self) {
        self.app
            .state::<AppFlags>()
            .quitting
            .store(true, std::sync::atomic::Ordering::SeqCst);
    }
}

impl Drop for TestApp {
    fn drop(&mut self) {
        self.quit(); // stop any still-running supervisor loop promptly
        self.kill_tracked_child();
        for k in SCENARIO_ENV {
            std::env::remove_var(k);
        }
        set_backend_kill_intended(false);
    }
}

fn wait_until(timeout: Duration, mut pred: impl FnMut() -> bool) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if pred() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    false
}

fn join_with_timeout(h: std::thread::JoinHandle<()>, timeout: Duration, what: &str) {
    let start = Instant::now();
    while !h.is_finished() {
        assert!(
            start.elapsed() < timeout,
            "{what}: bootstrap thread still running after {timeout:?} — a lifecycle \
             regression is hanging instead of diagnosing"
        );
        std::thread::sleep(Duration::from_millis(100));
    }
    let _ = h.join();
}

fn recorded_spawn_count(path: &std::path::Path) -> usize {
    std::fs::read_to_string(path)
        .unwrap_or_default()
        .lines()
        .filter(|line| !line.trim().is_empty())
        .count()
}

fn process_is_alive(pid: u32) -> bool {
    let pid = sysinfo::Pid::from_u32(pid);
    let mut system = sysinfo::System::new();
    system.refresh_processes(sysinfo::ProcessesToUpdate::Some(&[pid]), false);
    system.process(pid).is_some()
}

fn force_kill_pid_for_cleanup(pid: u32) {
    #[cfg(unix)]
    unsafe {
        libc::kill(pid as i32, libc::SIGKILL);
    }
    #[cfg(windows)]
    {
        let pid = pid.to_string();
        let _ = app_lib::tools::no_window(
            std::process::Command::new("taskkill")
                .args(["/PID", pid.as_str(), "/T", "/F"]),
        )
        .status();
    }
}

// ── Scenarios ─────────────────────────────────────────────────────────────

/// #1635 — launch bootstrap and Retry used to probe an empty port together,
/// spawn independently, and overwrite the one tracked child. The untracked
/// winner stayed healthy while the loser wrote a false port-conflict crash.
#[test]
fn concurrent_bootstrap_and_retry_share_one_backend_child() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0), // serve until the harness stops the child
        ..Default::default()
    });
    let spawn_log = t._logdir.path().join("scenario-spawns.log");
    std::env::set_var("OMNIVOICE_SCENARIO_SPAWN_LOG", &spawn_log);
    // Hold both real child processes before bind so both launch owners have
    // time to reach spawn on the broken implementation.
    std::env::set_var("OMNIVOICE_SCENARIO_START_DELAY_MS", "300");

    let gate = Arc::new(std::sync::Barrier::new(3));
    let launch = |gate: Arc<std::sync::Barrier>, handle, stage| {
        std::thread::spawn(move || {
            gate.wait();
            spawn_backend_and_wait(&handle, &stage);
        })
    };
    let bootstrap = launch(gate.clone(), t.handle(), t.stage.clone());
    let retry = launch(gate.clone(), t.handle(), t.stage.clone());
    gate.wait();

    assert!(
        wait_until(Duration::from_secs(20), || matches!(
            t.stage_snapshot(),
            BootstrapStage::Ready
        )),
        "concurrent launch never produced a healthy backend"
    );
    assert!(
        wait_until(Duration::from_secs(10), || bootstrap.is_finished() || retry.is_finished()),
        "the losing launch owner did not attach to the healthy child"
    );
    assert_eq!(
        recorded_spawn_count(&spawn_log),
        1,
        "bootstrap + Retry must create exactly one OS child"
    );
    assert!(
        app_lib::backend::backend_ready(
            std::env::var("OMNIVOICE_PORT").unwrap().parse().unwrap()
        ),
        "the shared child must pass both readiness probes"
    );
    assert!(
        t.markers().markers.is_empty(),
        "a serialized launch must not manufacture a bind-crash marker"
    );

    t.quit();
    t.kill_tracked_child();
    join_with_timeout(bootstrap, Duration::from_secs(10), "concurrent bootstrap shutdown");
    join_with_timeout(retry, Duration::from_secs(10), "concurrent retry shutdown");
}

/// A healthy same-version listener may predate the current launch (for
/// example after the webview shell crashed). Attaching without owning its PID
/// used to return Ready with no supervisor, so its next death was permanent.
#[test]
fn healthy_external_backend_is_attached_with_supervision_and_replaced_after_death() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0),
        ..Default::default()
    });
    let spawn_log = t._logdir.path().join("scenario-attached-spawns.log");
    std::env::set_var("OMNIVOICE_SCENARIO_SPAWN_LOG", &spawn_log);
    let exe = std::env::current_exe().expect("current test executable");
    let mut external = std::process::Command::new(exe)
        .args(["scenario_child", "--exact", "--nocapture"])
        .spawn()
        .expect("spawn external healthy backend");
    let port = std::env::var("OMNIVOICE_PORT").unwrap().parse().unwrap();
    assert!(
        wait_until(Duration::from_secs(10), || {
            app_lib::backend::backend_ready(port) && recorded_spawn_count(&spawn_log) == 1
        }),
        "external backend never became healthy"
    );

    let bootstrap = t.run_bootstrap();
    assert!(
        wait_until(Duration::from_secs(10), || {
            let state = t.app.state::<BackendState>();
            matches!(t.stage_snapshot(), BootstrapStage::Ready)
                && state.attached.load(std::sync::atomic::Ordering::SeqCst)
                && state.process.lock().unwrap().is_none()
                && state.owned_tree.lock().unwrap().is_none()
        }),
        "launch reported Ready without health-supervising the external backend"
    );

    external.kill().expect("kill attached backend");
    let _ = external.wait();
    assert!(
        wait_until(Duration::from_secs(20), || {
            recorded_spawn_count(&spawn_log) == 2
                && app_lib::backend::backend_ready(port)
                && matches!(t.stage_snapshot(), BootstrapStage::Ready)
        }),
        "attached backend death was not replaced by a healthy supervised child"
    );

    shutdown_backend_for_exit(&t.handle());
    join_with_timeout(bootstrap, Duration::from_secs(10), "attached-backend shutdown");
    assert!(!app_lib::backend::port_in_use(port));
}

/// #1770 at the integration level: a same-version external backend whose
/// `/system/info` has NO `code_fingerprint` key at all — the shape a
/// backend from before this fix has, since a present field always
/// serializes even blank — must NOT be silently attached to. This is the
/// actual bug two independent reports traced to a stale `destination_path`
/// 422: an already-running same-version backend running weeks-old code was
/// attached to instead of refused.
///
/// The outcome is a REFUSAL, not a kill-and-replace: this backend was never
/// tracked or attached (it's a plain external process on the port), and
/// `kill_orphan_on_port` deliberately never signals a PID discovered only
/// through the port — the exact "reuse race" guard
/// `orphan_cleanup_refuses_a_foreign_listener` covers for the pre-existing
/// version-mismatch case. So a stale-fingerprint match takes the SAME path
/// a stale-version match already does: `stop_backend_locked` cannot free
/// the port, and the launch fails with a diagnosis rather than adopting
/// stale code OR terminating an unowned process.
#[test]
fn stale_code_fingerprint_external_backend_is_refused_not_attached() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0),
        no_code_fingerprint: true,
        ..Default::default()
    });
    let spawn_log = t._logdir.path().join("scenario-stale-fingerprint-spawns.log");
    std::env::set_var("OMNIVOICE_SCENARIO_SPAWN_LOG", &spawn_log);
    let exe = std::env::current_exe().expect("current test executable");
    let mut external = std::process::Command::new(exe)
        .args(["scenario_child", "--exact", "--nocapture"])
        .spawn()
        .expect("spawn external stale-fingerprint backend");
    let external_pid = external.id();
    let port = std::env::var("OMNIVOICE_PORT").unwrap().parse().unwrap();
    assert!(
        wait_until(Duration::from_secs(10), || {
            app_lib::backend::backend_ready(port) && recorded_spawn_count(&spawn_log) == 1
        }),
        "external stale-fingerprint backend never became healthy"
    );

    let bootstrap = t.run_bootstrap();
    assert!(
        wait_until(Duration::from_secs(10), || {
            matches!(t.stage_snapshot(), BootstrapStage::Failed { .. })
        }),
        "a same-version backend with no code_fingerprint field must be refused (the same outcome \
         a version mismatch already gets), not silently attached — got {:?} / {:?}",
        t.stage_snapshot(),
        t.failed_message()
    );
    assert!(
        !t.app.state::<BackendState>().attached.load(std::sync::atomic::Ordering::SeqCst),
        "a refused attach must never mark the backend attached"
    );
    assert_eq!(
        recorded_spawn_count(&spawn_log),
        1,
        "an unowned, untracked process must never be killed by PID — no replacement child may spawn"
    );
    assert!(
        process_is_alive(external_pid),
        "the stale-fingerprint external backend must be left running untouched, not killed"
    );

    join_with_timeout(bootstrap, Duration::from_secs(10), "stale-fingerprint refusal");
    external.kill().expect("kill external stale-fingerprint backend");
    let _ = external.wait();
}

/// Greptile P1 on #1796: `backend_deep_healthy` (GET /profiles) and
/// `running_backend_identity` (GET /system/info) are two independent
/// requests — never atomic. If the process on the port changed between
/// them, an identity-THEN-health ordering could pair one process's
/// (already stale) identity with a DIFFERENT process's health and attach
/// without ever validating that second process — exactly what this
/// fingerprint check exists to prevent, reached by a different route.
/// `prepare_backend_launch` closes most of that window by probing health
/// FIRST and identity LAST, immediately before the attach decision, so the
/// identity that governs the decision is always the freshest read of
/// whatever currently answers the port.
///
/// A genuine multi-process bind-swap race is non-deterministic to trigger
/// on demand (the second process must win the port back inside a
/// microsecond gap), so this models the same OBSERVABLE property with a
/// single deterministic stub: it serves a MATCHING identity right up until
/// it answers the health probe, then flips to a body with no
/// `code_fingerprint` key at all — "the port changed hands" from the
/// launcher's point of view is indistinguishable from "the same process
/// changed what it reports". With identity probed last, the launcher must
/// read the post-flip (stale) state and refuse. Had identity still been
/// probed FIRST (the pre-fix ordering), it would have captured the
/// pre-flip (matching) identity and attached despite the divergence.
#[test]
fn identity_probed_after_health_reflects_the_port_at_decision_time() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0),
        ..Default::default()
    });
    let port: u16 = std::env::var("OMNIVOICE_PORT").unwrap().parse().unwrap();
    let flipped = Arc::new(AtomicBool::new(false));
    let stop = Arc::new(AtomicBool::new(false));
    let listener = std::net::TcpListener::bind(("127.0.0.1", port)).expect("bind identity-flip stub");
    listener.set_nonblocking(true).unwrap();
    let stub = {
        let flipped = flipped.clone();
        let stop = stop.clone();
        std::thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(10);
            loop {
                if stop.load(std::sync::atomic::Ordering::SeqCst) || Instant::now() >= deadline {
                    return;
                }
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        // Whether an accepted socket inherits the listening
                        // socket's non-blocking flag is OS-dependent (Linux,
                        // macOS, and Windows disagree) — this job runs on all
                        // three, so leave nothing to inheritance. Force
                        // blocking mode explicitly; the read timeout below
                        // then bounds it deterministically everywhere.
                        stream.set_nonblocking(false).expect("accepted stream to blocking mode");
                        let mut buf = [0u8; 512];
                        let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
                        let n = stream.read(&mut buf).unwrap_or(0);
                        let req = String::from_utf8_lossy(&buf[..n]);
                        let resp = if req.starts_with("GET /profiles") {
                            // Answering the health probe is the trigger:
                            // flip identity for whatever comes next,
                            // modeling the port changing hands right after
                            // this response.
                            flipped.store(true, std::sync::atomic::Ordering::SeqCst);
                            "HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n[]".to_string()
                        } else if req.starts_with("GET /system/info") {
                            let body = if flipped.load(std::sync::atomic::Ordering::SeqCst) {
                                // Post-flip: same version, but no
                                // code_fingerprint key at all — the
                                // pre-fingerprinting shape, as if a
                                // different (stale) process now answers.
                                format!(
                                    r#"{{"data_dir": "/x", "app_version": "{}"}}"#,
                                    env!("CARGO_PKG_VERSION")
                                )
                            } else {
                                format!(
                                    r#"{{"data_dir": "/x", "app_version": "{}", "code_fingerprint": ""}}"#,
                                    env!("CARGO_PKG_VERSION")
                                )
                            };
                            format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{}", body.len(), body)
                        } else {
                            "HTTP/1.1 404 X\r\nContent-Length: 0\r\n\r\n".to_string()
                        };
                        let _ = stream.write_all(resp.as_bytes());
                    }
                    Err(_) => std::thread::sleep(Duration::from_millis(10)),
                }
            }
        })
    };

    let bootstrap = t.run_bootstrap();
    assert!(
        wait_until(Duration::from_secs(10), || {
            flipped.load(std::sync::atomic::Ordering::SeqCst)
                && matches!(t.stage_snapshot(), BootstrapStage::Failed { .. })
        }),
        "identity read AFTER the health probe must reflect the post-flip (stale) state and \
         refuse the attach — got stage {:?}",
        t.stage_snapshot()
    );
    assert!(
        !t.app.state::<BackendState>().attached.load(std::sync::atomic::Ordering::SeqCst),
        "a post-flip identity mismatch must never be attached to"
    );

    stop.store(true, std::sync::atomic::Ordering::SeqCst);
    join_with_timeout(bootstrap, Duration::from_secs(10), "identity-after-health refusal");
    let _ = stub.join();
}

#[test]
fn attached_backend_health_grace_recovers_without_false_crash_or_port_failure() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0),
        ..Default::default()
    });
    let spawn_log = t._logdir.path().join("scenario-attached-health-spawns.log");
    let fail_file = t._logdir.path().join("scenario-attached-health-fail");
    std::env::set_var("OMNIVOICE_SCENARIO_SPAWN_LOG", &spawn_log);
    std::env::set_var("OMNIVOICE_SCENARIO_HEALTH_FAIL_FILE", &fail_file);
    let exe = std::env::current_exe().expect("current test executable");
    let mut external = std::process::Command::new(exe)
        .args(["scenario_child", "--exact", "--nocapture"])
        .spawn()
        .expect("spawn external healthy backend");
    let external_pid = external.id();
    let port = std::env::var("OMNIVOICE_PORT").unwrap().parse().unwrap();
    assert!(wait_until(Duration::from_secs(10), || {
        app_lib::backend::backend_ready(port)
    }));

    let restarts = t.record_events("backend-restarting");
    let bootstrap = t.run_bootstrap();
    assert!(wait_until(Duration::from_secs(10), || {
        t.app
            .state::<BackendState>()
            .attached
            .load(std::sync::atomic::Ordering::SeqCst)
            && matches!(t.stage_snapshot(), BootstrapStage::Ready)
    }));
    let markers_before = t.markers().markers.len();

    std::fs::write(&fail_file, b"fail deep health").unwrap();
    std::thread::sleep(Duration::from_millis(1200));
    std::fs::remove_file(&fail_file).unwrap();
    assert!(wait_until(Duration::from_secs(5), || {
        app_lib::backend::backend_ready(port)
            && t
                .app
                .state::<BackendState>()
                .attached
                .load(std::sync::atomic::Ordering::SeqCst)
            && t
                .app
                .state::<BackendState>()
                .attached_health
                .lock()
                .unwrap()
                .failures
                == 0
    }));

    assert!(process_is_alive(external_pid));
    assert_eq!(recorded_spawn_count(&spawn_log), 1);
    assert_eq!(t.markers().markers.len(), markers_before);
    assert!(restarts.lock().unwrap().is_empty());
    assert!(matches!(t.stage_snapshot(), BootstrapStage::Ready));
    assert_eq!(
        t.app
            .state::<BackendState>()
            .attached_health
            .lock()
            .unwrap()
            .failures,
        0,
        "a healthy sample must reset the consecutive-failure policy"
    );

    shutdown_backend_for_exit(&t.handle());
    join_with_timeout(bootstrap, Duration::from_secs(10), "attached health recovery");
    assert!(process_is_alive(external_pid));
    external.kill().unwrap();
    let _ = external.wait();
}

/// A matching API is sufficient for a safe attachment, never for ownership.
/// Terminal desktop teardown must leave the external process untouched.
#[test]
fn healthy_external_attachment_is_not_killed_on_desktop_exit() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0),
        ..Default::default()
    });
    let spawn_log = t._logdir.path().join("scenario-safe-attachment.log");
    std::env::set_var("OMNIVOICE_SCENARIO_SPAWN_LOG", &spawn_log);
    let exe = std::env::current_exe().expect("current test executable");
    let mut external = std::process::Command::new(exe)
        .args(["scenario_child", "--exact", "--nocapture"])
        .spawn()
        .expect("spawn external healthy backend");
    let external_pid = external.id();
    let port = std::env::var("OMNIVOICE_PORT").unwrap().parse().unwrap();
    assert!(wait_until(Duration::from_secs(10), || {
        app_lib::backend::backend_ready(port)
    }));

    let bootstrap = t.run_bootstrap();
    assert!(wait_until(Duration::from_secs(10), || {
        t.app
            .state::<BackendState>()
            .attached
            .load(std::sync::atomic::Ordering::SeqCst)
    }));
    shutdown_backend_for_exit(&t.handle());
    join_with_timeout(bootstrap, Duration::from_secs(10), "safe external detach");

    assert!(process_is_alive(external_pid));
    assert!(app_lib::backend::port_in_use(port));
    external.kill().unwrap();
    let _ = external.wait();
}

/// A backend may launch an engine after Ready and crash immediately afterwards
/// (before any supervisor poll). Launch-time containment, rather than sampled
/// ancestry, must drain that late child before a replacement starts.
#[test]
fn crash_replacement_and_exit_terminate_descendants_from_both_generations() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0),
        ..Default::default()
    });
    let spawn_log = t._logdir.path().join("scenario-crash-tree-spawns.log");
    let descendant_log = t._logdir.path().join("scenario-crash-tree-descendant.log");
    let descendant_ready = t._logdir.path().join("scenario-crash-tree-descendant-ready");
    let descendant_trigger = t._logdir.path().join("scenario-crash-tree-trigger");
    std::env::set_var("OMNIVOICE_SCENARIO_SPAWN_LOG", &spawn_log);
    std::env::set_var("OMNIVOICE_SCENARIO_DESCENDANT_LOG", &descendant_log);
    std::env::set_var("OMNIVOICE_SCENARIO_DESCENDANT_SETSID", "1");
    std::env::set_var("OMNIVOICE_SCENARIO_DESCENDANT_READY", &descendant_ready);
    std::env::set_var("OMNIVOICE_SCENARIO_DESCENDANT_TRIGGER", &descendant_trigger);

    let bootstrap = t.run_bootstrap();
    assert!(
        wait_until(Duration::from_secs(20), || {
            matches!(t.stage_snapshot(), BootstrapStage::Ready)
        }),
        "first backend generation never became ready"
    );
    std::fs::write(&descendant_trigger, b"spawn now").unwrap();
    assert!(
        wait_until(Duration::from_secs(10), || descendant_ready.exists()),
        "late descendant did not start after Ready"
    );
    let original_descendant: u32 = std::fs::read_to_string(&descendant_log)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    assert!(process_is_alive(original_descendant));

    t.signal_tracked_child();
    assert!(
        wait_until(Duration::from_secs(20), || {
            recorded_spawn_count(&spawn_log) == 2
                && matches!(t.stage_snapshot(), BootstrapStage::Ready)
                && std::fs::read_to_string(&descendant_log)
                    .ok()
                    .and_then(|pid| pid.trim().parse::<u32>().ok())
                    .is_some_and(|pid| pid != original_descendant)
        }),
        "crashed backend was not replaced with a ready generation"
    );
    assert!(
        wait_until(Duration::from_secs(3), || !process_is_alive(original_descendant)),
        "late descendant from crashed root survived into the replacement generation"
    );
    let replacement_descendant: u32 = std::fs::read_to_string(&descendant_log)
        .unwrap()
        .trim()
        .parse()
        .unwrap();

    shutdown_backend_for_exit(&t.handle());
    join_with_timeout(bootstrap, Duration::from_secs(10), "crash-tree shutdown");
    let original_stopped = wait_until(Duration::from_secs(3), || {
        !process_is_alive(original_descendant)
    });
    let replacement_stopped = wait_until(Duration::from_secs(3), || {
        !process_is_alive(replacement_descendant)
    });
    if !original_stopped {
        force_kill_pid_for_cleanup(original_descendant);
    }
    if !replacement_stopped {
        force_kill_pid_for_cleanup(replacement_descendant);
    }
    assert!(
        original_stopped,
        "escaped descendant from crashed root {original_descendant} survived replacement and exit"
    );
    assert!(
        replacement_stopped,
        "escaped descendant from replacement {replacement_descendant} survived exit"
    );
}

/// A nested supervisor is intentionally outside the backend's Rust process
/// group. Teardown may complete only after its inherited drain writer closes;
/// a stopped wrapper must therefore preserve retryable handles and block the
/// caller's destructive action/replacement rather than relying on a sleep.
#[cfg(unix)]
#[test]
fn stopped_nested_wrapper_blocks_mutation_until_drain_retry() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0),
        ..Default::default()
    });
    let wrapper_log = t._logdir.path().join("scenario-stopped-drain-wrapper.log");
    std::env::set_var("OMNIVOICE_SCENARIO_DRAIN_WRAPPER_LOG", &wrapper_log);
    std::env::set_var("OMNIVOICE_TEST_NESTED_DRAIN_TIMEOUT_MS", "300");
    let bootstrap = t.run_bootstrap();
    assert!(wait_until(Duration::from_secs(20), || {
        matches!(t.stage_snapshot(), BootstrapStage::Ready) && wrapper_log.exists()
    }));
    let wrapper_pid: u32 = std::fs::read_to_string(&wrapper_log)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    assert!(process_is_alive(wrapper_pid));

    let mutated = Arc::new(AtomicBool::new(false));
    let mutated_first = mutated.clone();
    let first = with_backend_stopped(&t.handle(), move || {
        mutated_first.store(true, std::sync::atomic::Ordering::SeqCst);
    });
    let mutated_before_retry = mutated.load(std::sync::atomic::Ordering::SeqCst);
    let refused = first
        .as_ref()
        .is_err_and(|message| message.contains("nested backend operations did not drain"));
    let state = t.app.state::<BackendState>();
    let retryable = state.process.lock().unwrap().is_some()
        && state.owned_tree.lock().unwrap().is_some();
    let wrapper_survived_timeout = process_is_alive(wrapper_pid);

    unsafe {
        libc::kill(wrapper_pid as libc::pid_t, libc::SIGCONT);
    }
    assert!(wait_until(Duration::from_secs(5), || !process_is_alive(wrapper_pid)));
    let mutated_retry = mutated.clone();
    let retry = with_backend_stopped(&t.handle(), move || {
        mutated_retry.store(true, std::sync::atomic::Ordering::SeqCst);
    });

    assert!(refused, "first teardown unexpectedly completed: {first:?}");
    assert!(!mutated_before_retry, "mutation ran before nested drain EOF");
    assert!(retryable, "drain timeout discarded the stable retry handles");
    assert!(wrapper_survived_timeout, "timeout test wrapper was not still stopped");
    assert!(retry.is_ok(), "drain retry failed after wrapper exit: {retry:?}");
    assert!(mutated.load(std::sync::atomic::Ordering::SeqCst));
    assert!(state.process.lock().unwrap().is_none());
    assert!(state.owned_tree.lock().unwrap().is_none());

    t.quit();
    join_with_timeout(bootstrap, Duration::from_secs(10), "nested drain retry");
}

/// A configured-port conflict is user input, not authority to terminate an
/// arbitrary application. The orphan cleanup path must leave a stable foreign
/// LISTEN owner alive even when it is the exact PID returned by lsof/netstat.
#[test]
fn orphan_cleanup_refuses_a_foreign_listener() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0),
        foreign: true,
        ..Default::default()
    });
    let spawn_log = t._logdir.path().join("scenario-foreign-listener.log");
    std::env::set_var("OMNIVOICE_SCENARIO_SPAWN_LOG", &spawn_log);
    let bootstrap = t.run_bootstrap();
    let port = std::env::var("OMNIVOICE_PORT").unwrap().parse().unwrap();
    assert!(
        wait_until(Duration::from_secs(10), || {
            app_lib::backend::port_in_use(port) && recorded_spawn_count(&spawn_log) == 1
        }),
        "foreign scenario never bound the configured port"
    );
    let pid: u32 = std::fs::read_to_string(&spawn_log)
        .unwrap()
        .trim()
        .parse()
        .unwrap();

    app_lib::backend::kill_orphan_on_port(port);
    std::thread::sleep(Duration::from_millis(300));
    assert!(
        process_is_alive(pid),
        "orphan cleanup killed a foreign process"
    );
    assert!(app_lib::backend::port_in_use(port));

    t.quit();
    t.kill_tracked_child();
    join_with_timeout(
        bootstrap,
        Duration::from_secs(10),
        "foreign-listener cleanup",
    );
}

/// A stop failure happens after the tracked slot has been taken. Every
/// non-terminal caller of the lifecycle guard must therefore schedule a fresh
/// serialized launch instead of trusting the old supervisor to survive.
#[test]
fn failed_backend_stop_rearms_service_for_all_guard_callers() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0),
        ..Default::default()
    });
    let spawn_log = t._logdir.path().join("scenario-stop-recovery.log");
    std::env::set_var("OMNIVOICE_SCENARIO_SPAWN_LOG", &spawn_log);
    let bootstrap = t.run_bootstrap();
    assert!(
        wait_until(Duration::from_secs(20), || matches!(
            t.stage_snapshot(),
            BootstrapStage::Ready
        )),
        "initial backend never became ready"
    );

    std::env::set_var("OMNIVOICE_TEST_FORCE_STOP_ERROR", "1");
    let action_ran = std::cell::Cell::new(false);
    let result = with_backend_stopped(&t.handle(), || action_ran.set(true));
    std::env::remove_var("OMNIVOICE_TEST_FORCE_STOP_ERROR");
    assert!(result.is_err(), "fault seam did not fail the stop");
    assert!(!action_ran.get(), "caller mutation ran after a failed stop");
    assert!(
        wait_until(Duration::from_secs(20), || {
            recorded_spawn_count(&spawn_log) == 2
                && matches!(t.stage_snapshot(), BootstrapStage::Ready)
        }),
        "failed stop did not restore a supervised backend"
    );

    t.quit();
    t.kill_tracked_child();
    join_with_timeout(bootstrap, Duration::from_secs(10), "failed-stop recovery");
}

/// A surviving backend tree is categorically different from a recoverable
/// post-stop error: spawning a replacement could duplicate engine workers or
/// race files still held by the survivor. Keep the deliberate-kill fence up
/// and require a later explicit retry after the teardown problem is resolved.
#[test]
fn incomplete_backend_stop_does_not_spawn_a_replacement() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0),
        ..Default::default()
    });
    let spawn_log = t._logdir.path().join("scenario-incomplete-stop.log");
    std::env::set_var("OMNIVOICE_SCENARIO_SPAWN_LOG", &spawn_log);
    let bootstrap = t.run_bootstrap();
    assert!(
        wait_until(Duration::from_secs(20), || matches!(
            t.stage_snapshot(),
            BootstrapStage::Ready
        )),
        "initial backend never became ready"
    );

    std::env::set_var("OMNIVOICE_TEST_FORCE_INCOMPLETE_STOP_ERROR", "1");
    let result = with_backend_stopped(&t.handle(), || ());
    std::env::remove_var("OMNIVOICE_TEST_FORCE_INCOMPLETE_STOP_ERROR");
    assert!(result.is_err(), "fault seam did not fail the stop");
    std::thread::sleep(Duration::from_millis(500));
    assert_eq!(
        recorded_spawn_count(&spawn_log),
        1,
        "incomplete teardown must not launch a replacement"
    );
    assert!(t.app.state::<BackendState>().process.lock().unwrap().is_none());

    t.quit();
    join_with_timeout(bootstrap, Duration::from_secs(10), "incomplete-stop shutdown");
}

/// #1635 follow-up — uninstall used to kill by port only, so a tracked child
/// still inside its pre-bind delay survived while its live environment was
/// deleted. Uninstall must wait for the launch owner, stop that exact child,
/// and keep lifecycle ownership through deletion.
#[test]
fn uninstall_waits_for_an_unbound_tracked_child_before_deleting() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0),
        ..Default::default()
    });
    let spawn_log = t._logdir.path().join("scenario-uninstall-spawns.log");
    let descendant_log = t._logdir.path().join("scenario-uninstall-descendant.log");
    let descendant_ready = t._logdir.path().join("scenario-uninstall-descendant-ready");
    let after_track = t._logdir.path().join("uninstall-after-track-entered");
    let release_track = t._logdir.path().join("uninstall-release-after-track");
    let target_path = t._logdir.path().join("OmniVoice");
    std::fs::create_dir_all(&target_path).expect("create synthetic uninstall target");
    std::fs::write(target_path.join("live-env.txt"), b"live").expect("seed uninstall target");
    std::env::set_var("OMNIVOICE_SCENARIO_SPAWN_LOG", &spawn_log);
    std::env::set_var("OMNIVOICE_SCENARIO_DESCENDANT_LOG", &descendant_log);
    std::env::set_var("OMNIVOICE_SCENARIO_DESCENDANT_SETSID", "1");
    std::env::set_var("OMNIVOICE_SCENARIO_DESCENDANT_READY", &descendant_ready);
    std::env::set_var("OMNIVOICE_SCENARIO_START_DELAY_MS", "5000");
    std::env::set_var("OMNIVOICE_TEST_AFTER_TRACK_ENTERED", &after_track);
    std::env::set_var("OMNIVOICE_TEST_AFTER_TRACK_RELEASE", &release_track);

    let bootstrap = t.run_bootstrap();
    let reached_gate = wait_until(Duration::from_secs(10), || {
        after_track.exists() && descendant_ready.exists() && recorded_spawn_count(&spawn_log) == 1
    });
    if !reached_gate {
        let _ = std::fs::write(&release_track, b"release");
    }
    assert!(reached_gate, "scenario child never reached the tracked pre-bind gate");
    let pid: u32 = std::fs::read_to_string(&spawn_log)
        .unwrap()
        .lines()
        .next()
        .unwrap()
        .parse()
        .unwrap();
    let descendant_pid: u32 = std::fs::read_to_string(&descendant_log)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    let port = std::env::var("OMNIVOICE_PORT").unwrap().parse().unwrap();
    assert!(
        !app_lib::backend::port_in_use(port),
        "precondition: the tracked child must still be unbound"
    );

    // Production `uninstall_purge` suppresses launches without claiming a
    // terminal exit before entering this shared deletion core.
    t.app
        .state::<AppFlags>()
        .uninstalling
        .store(true, std::sync::atomic::Ordering::SeqCst);
    let app = t.handle();
    let target_string = target_path.to_string_lossy().into_owned();
    let target = UninstallTarget {
        key: "env".to_string(),
        path: target_string.clone(),
        size_bytes: 4,
        exists: true,
        shared: false,
    };
    let purge = std::thread::spawn(move || purge_uninstall_targets(&app, vec![target], false));

    let purged_while_prebind = wait_until(Duration::from_secs(2), || purge.is_finished());
    let target_survived_until_launch_released = target_path.exists();
    std::fs::write(&release_track, b"release").unwrap();
    join_with_timeout(bootstrap, Duration::from_secs(10), "uninstall overlap shutdown");
    let report = purge.join().expect("purge thread panicked").expect("purge failed");
    assert!(
        !purged_while_prebind && target_survived_until_launch_released,
        "uninstall deleted the live environment before joining the tracked pre-bind child"
    );
    assert_eq!(report.removed, vec![target_string]);
    assert!(!target_path.exists(), "target must be removed after the child stops");
    assert!(
        t.app.state::<BackendState>().process.lock().unwrap().is_none(),
        "uninstall must not leave the tracked child orphaned"
    );
    assert!(!process_is_alive(pid), "uninstall left backend pid {pid} orphaned");
    assert!(
        !process_is_alive(descendant_pid),
        "uninstall left escaped descendant pid {descendant_pid} orphaned"
    );
    assert!(
        wait_until(Duration::from_secs(5), || !app_lib::backend::port_in_use(port)),
        "the stopped child must release its backend port"
    );
    assert_eq!(recorded_spawn_count(&spawn_log), 1, "uninstall must not spawn a replacement");
    assert!(t.markers().markers.is_empty(), "an intentional uninstall is not a crash");
}

/// #1635 production-exit follow-up — ExitRequested used to inspect only the
/// process slot, outside lifecycle ownership. If exit landed after OS spawn
/// but before tracking, bootstrap installed the child afterwards and left it
/// alive. The production exit path must join launch, then stop that child.
#[test]
fn production_exit_joins_a_prebind_spawn_and_leaves_no_orphan() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0),
        ..Default::default()
    });
    let spawn_log = t._logdir.path().join("scenario-exit-spawns.log");
    let before_track = t._logdir.path().join("before-track-entered");
    let release_track = t._logdir.path().join("release-before-track");
    std::env::set_var("OMNIVOICE_SCENARIO_SPAWN_LOG", &spawn_log);
    std::env::set_var("OMNIVOICE_SCENARIO_START_DELAY_MS", "1500");
    std::env::set_var("OMNIVOICE_TEST_BEFORE_TRACK_ENTERED", &before_track);
    std::env::set_var("OMNIVOICE_TEST_BEFORE_TRACK_RELEASE", &release_track);

    let bootstrap = t.run_bootstrap();
    let reached_gate = wait_until(Duration::from_secs(10), || {
        before_track.exists() && recorded_spawn_count(&spawn_log) == 1
    });
    if !reached_gate {
        let _ = std::fs::write(&release_track, b"release");
    }
    assert!(reached_gate, "backend never reached the spawned-but-untracked test gate");
    let pid: u32 = std::fs::read_to_string(&spawn_log)
        .unwrap()
        .lines()
        .next()
        .unwrap()
        .parse()
        .unwrap();
    let port = std::env::var("OMNIVOICE_PORT").unwrap().parse().unwrap();
    let child_is_prebind = !app_lib::backend::port_in_use(port);
    if !child_is_prebind {
        let _ = std::fs::write(&release_track, b"release");
    }
    assert!(child_is_prebind, "precondition: child is pre-bind");

    let app = t.handle();
    let shutdown = std::thread::spawn(move || shutdown_backend_for_exit(&app));
    let quitting = wait_until(Duration::from_secs(5), || {
        t.app
            .state::<AppFlags>()
            .quitting
            .load(std::sync::atomic::Ordering::SeqCst)
    });
    if quitting {
        assert!(!shutdown.is_finished(), "exit must join the active lifecycle owner");
    }
    std::fs::write(&release_track, b"release").unwrap();
    assert!(quitting, "production exit did not raise quitting before teardown");

    join_with_timeout(bootstrap, Duration::from_secs(10), "production exit bootstrap");
    join_with_timeout(shutdown, Duration::from_secs(10), "production exit teardown");
    assert!(
        t.app.state::<BackendState>().process.lock().unwrap().is_none(),
        "production exit must clear the tracked child"
    );
    assert!(
        wait_until(Duration::from_secs(5), || !process_is_alive(pid)),
        "production exit left backend pid {pid} orphaned"
    );
    assert!(!app_lib::backend::port_in_use(port), "orphan must not claim the port later");
    assert_eq!(recorded_spawn_count(&spawn_log), 1, "shutdown must not respawn");
    assert!(t.markers().markers.is_empty(), "intentional exit is not a crash");
}

/// Graceful-first shutdown must let the backend's lifespan cleanup run, then
/// prove the whole backend process tree is gone — killing only the tracked
/// parent leaves subprocess-isolated engines alive and keeps Windows files
/// locked.
#[cfg(unix)]
#[test]
fn production_exit_runs_cleanup_and_stops_backend_descendants() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0),
        ..Default::default()
    });
    let spawn_log = t._logdir.path().join("scenario-graceful-parent.log");
    let descendant_log = t._logdir.path().join("scenario-graceful-descendant.log");
    let descendant_ready = t._logdir.path().join("scenario-graceful-descendant-ready");
    let sentinel = t._logdir.path().join("scenario-clean-shutdown");
    std::env::set_var("OMNIVOICE_SCENARIO_SPAWN_LOG", &spawn_log);
    std::env::set_var("OMNIVOICE_SCENARIO_DESCENDANT_LOG", &descendant_log);
    std::env::set_var("OMNIVOICE_SCENARIO_DESCENDANT_SETSID", "1");
    std::env::set_var("OMNIVOICE_SCENARIO_DESCENDANT_READY", &descendant_ready);
    std::env::set_var("OMNIVOICE_SCENARIO_SHUTDOWN_SENTINEL", &sentinel);

    let bootstrap = t.run_bootstrap();
    assert!(
        wait_until(Duration::from_secs(20), || matches!(
            t.stage_snapshot(),
            BootstrapStage::Ready
        ) && descendant_ready.exists()),
        "backend tree never became ready"
    );
    let parent_pid: u32 = std::fs::read_to_string(&spawn_log).unwrap().trim().parse().unwrap();
    let descendant_pid: u32 = std::fs::read_to_string(&descendant_log)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    let port = std::env::var("OMNIVOICE_PORT").unwrap().parse().unwrap();

    shutdown_backend_for_exit(&t.handle());
    join_with_timeout(bootstrap, Duration::from_secs(10), "graceful process-tree shutdown");
    let cleaned = sentinel.exists();
    let descendant_stopped = wait_until(Duration::from_secs(3), || !process_is_alive(descendant_pid));
    if !descendant_stopped {
        force_kill_pid_for_cleanup(descendant_pid);
    }

    assert!(cleaned, "SIGTERM must run the backend lifespan cleanup sentinel");
    assert!(!process_is_alive(parent_pid), "tracked backend parent survived shutdown");
    assert!(descendant_stopped, "backend descendant pid {descendant_pid} survived shutdown");
    assert!(!app_lib::backend::port_in_use(port), "backend port survived tree shutdown");
    assert_eq!(recorded_spawn_count(&spawn_log), 1, "shutdown must not respawn");
    assert!(t.markers().markers.is_empty(), "graceful exit is not a crash");
}

/// First-run `uv venv` / `uv sync` runs while launch owns lifecycle. Its wait
/// must notice quitting, terminate and reap the whole subprocess tree, and
/// release lifecycle so production ExitRequested cannot hang indefinitely.
#[test]
fn production_exit_interrupts_a_running_bootstrap_install_tree() {
    let t = TestApp::new(&Scenario::default());
    let spawn_log = t._logdir.path().join("scenario-install-parent.log");
    let descendant_log = t._logdir.path().join("scenario-install-descendant.log");
    let descendant_ready = t._logdir.path().join("scenario-install-descendant-ready");
    std::env::set_var("OMNIVOICE_SCENARIO_SPAWN_LOG", &spawn_log);
    std::env::set_var("OMNIVOICE_SCENARIO_DESCENDANT_LOG", &descendant_log);
    std::env::set_var("OMNIVOICE_SCENARIO_DESCENDANT_SETSID", "1");
    std::env::set_var("OMNIVOICE_SCENARIO_DESCENDANT_READY", &descendant_ready);

    let outcome = Arc::new(Mutex::new(None));
    let outcome2 = outcome.clone();
    let app = t.handle();
    let installer = std::thread::spawn(move || {
        let state = app.state::<BackendState>();
        let _lifecycle = state.lifecycle.lock().unwrap_or_else(|e| e.into_inner());
        let exe = std::env::current_exe().expect("current test executable");
        let mut cmd = std::process::Command::new(exe);
        cmd.args(["scenario_child", "--exact", "--nocapture"]);
        let result = run_streaming(&app, "installing_deps", &mut cmd);
        *outcome2.lock().unwrap() = Some(match result {
            Ok(status) => Ok(status.success()),
            Err(error) => Err(error.kind()),
        });
    });
    assert!(
        wait_until(Duration::from_secs(10), || {
            spawn_log.exists() && descendant_ready.exists()
        }),
        "bootstrap install tree never started"
    );
    let parent_pid: u32 = std::fs::read_to_string(&spawn_log).unwrap().trim().parse().unwrap();
    let descendant_pid: u32 = std::fs::read_to_string(&descendant_log)
        .unwrap()
        .trim()
        .parse()
        .unwrap();

    let app = t.handle();
    let shutdown = std::thread::spawn(move || shutdown_backend_for_exit(&app));
    let exit_completed = wait_until(Duration::from_secs(3), || shutdown.is_finished());
    if !exit_completed {
        force_kill_pid_for_cleanup(descendant_pid);
        force_kill_pid_for_cleanup(parent_pid);
    }
    join_with_timeout(installer, Duration::from_secs(10), "cancelled bootstrap install");
    join_with_timeout(shutdown, Duration::from_secs(10), "exit during bootstrap install");

    assert!(exit_completed, "ExitRequested blocked behind a bootstrap subprocess wait");
    assert_eq!(
        *outcome.lock().unwrap(),
        Some(Err(std::io::ErrorKind::Interrupted)),
        "quitting must interrupt the bootstrap subprocess"
    );
    assert!(!process_is_alive(parent_pid), "bootstrap subprocess parent survived exit");
    assert!(!process_is_alive(descendant_pid), "bootstrap subprocess descendant survived exit");
    assert!(t.markers().markers.is_empty(), "cancelled install is not a backend crash");
}

/// S1 — the backend exits EXIT_PORT_IN_USE: the user must read a port
/// conflict (in the exact phrasing BootstrapSplash.detectHints localizes),
/// not a traceback whose one meaningful line is an OS-translated errno.
#[test]
fn port_conflict_is_named_as_a_port_conflict() {
    let t = TestApp::new(&Scenario {
        stderr: "FATAL: port is already in use",
        exit: Some(app_lib::backend::EXIT_PORT_IN_USE),
        ..Default::default()
    });
    let h = t.run_bootstrap();
    join_with_timeout(h, Duration::from_secs(30), "port conflict");

    let msg = t.failed_message().expect("stage must be Failed");
    assert!(
        msg.contains("is already in use, so the backend could not"),
        "diagnosis must carry the detectHints-matchable port phrasing, got: {msg}"
    );
    let store = t.markers();
    assert_eq!(store.markers.len(), 1, "one real death → one marker");
    assert_eq!(store.markers.last().unwrap().exit_code, Some(app_lib::backend::EXIT_PORT_IN_USE));
}

/// S3 — generic startup traceback: the Failed message must carry the stderr
/// tail INCLUDING the chained-traceback root cause, and the marker must
/// record the death's shape.
#[test]
fn generic_traceback_surfaces_the_root_cause() {
    let t = TestApp::new(&Scenario {
        stderr: "Traceback (most recent call last):\\n  File \"main.py\", line 1\\nImportError: libcublas.so.12: cannot open shared object file\\n\\nThe above exception was the direct cause of the following exception:\\n\\nTraceback (most recent call last):\\n  File \"wrapper.py\", line 9\\nRuntimeError: failed to initialize CUDA backend",
        exit: Some(1),
        ..Default::default()
    });
    let h = t.run_bootstrap();
    join_with_timeout(h, Duration::from_secs(30), "generic traceback");

    let msg = t.failed_message().expect("stage must be Failed");
    assert!(msg.contains("Backend process exited"), "got: {msg}");
    assert!(
        msg.contains("libcublas.so.12"),
        "the root-cause line must survive into the diagnosis, got: {msg}"
    );
    let store = t.markers();
    assert_eq!(store.markers.len(), 1);
    let m = store.markers.last().unwrap();
    assert_eq!(m.exit_code, Some(1));
    assert!(m.last_stderr.contains("Traceback"), "marker carries the evidence");
    assert!(m.last_stderr.contains("libcublas.so.12"));
}

/// S4 — spawn failure (the program does not exist): the spawn diagnostic
/// must reach the user, and NO crash marker is written — nothing ever ran.
#[test]
fn spawn_failure_diagnoses_and_writes_no_bogus_marker() {
    let t = TestApp::new(&Scenario::default());
    // Point the seam at a program that cannot exist.
    let missing = t._logdir.path().join("no-such-backend");
    std::env::set_var(
        "OMNIVOICE_BACKEND_CMD",
        serde_json::to_string(&[missing.to_string_lossy().as_ref()]).unwrap(),
    );
    let h = t.run_bootstrap();
    join_with_timeout(h, Duration::from_secs(30), "spawn failure");

    let msg = t.failed_message().expect("stage must be Failed");
    assert!(
        msg.contains("Failed to launch the backend process"),
        "spawn_failure_diagnostic must reach the user, got: {msg}"
    );
    assert_eq!(
        t.markers().markers.len(),
        0,
        "never-started is not a crash — no marker may be written"
    );
}

/// S5 — slow start past the budget: the timeout diagnosis must name the
/// budget and carry the last stderr, and no death marker exists (the
/// process is alive, just slow).
#[test]
fn slow_start_times_out_with_the_last_stderr() {
    let t = TestApp::new(&Scenario {
        stderr: "Loading checkpoint shards_ 10%",
        serve_ms: None,
        ..Default::default()
    });
    // The child prints, then idles far past the 6s budget without serving.
    let h = t.run_bootstrap();
    join_with_timeout(h, Duration::from_secs(60), "slow start");

    let msg = t.failed_message().expect("stage must be Failed");
    assert!(msg.contains("did not respond within 6 s"), "got: {msg}");
    assert!(
        msg.contains("Loading checkpoint shards"),
        "the last stderr must ride along so triage sees WHERE it was, got: {msg}"
    );
    assert_eq!(t.markers().markers.len(), 0, "no death → no marker");
}

/// S6 — post-Ready crash loop: markers are recorded BEFORE each restart,
/// restarts are announced, and budget exhaustion lands on a Failed message
/// naming the pattern and the last exit.
#[test]
fn crash_loop_exhausts_the_budget_with_a_named_diagnosis() {
    let t = TestApp::new(&Scenario {
        stderr: "RuntimeError: CUDA error: out of memory",
        exit: Some(1),
        serve_ms: Some(1500),
        ..Default::default()
    });
    let restarts = t.record_events("backend-restarting");
    let gave_up = t.record_events("backend-restart-failed");
    let h = t.run_bootstrap();

    assert!(
        wait_until(Duration::from_secs(20), || matches!(
            t.stage_snapshot(),
            BootstrapStage::Ready | BootstrapStage::StartingBackend | BootstrapStage::Failed { .. }
        )),
        "backend never reached Ready"
    );
    join_with_timeout(h, Duration::from_secs(120), "crash loop");

    let msg = t.failed_message().expect("budget exhaustion must land on Failed");
    assert!(msg.contains("kept crashing"), "got: {msg}");
    assert!(msg.contains("exit code 1"), "the last death must be named, got: {msg}");
    assert_eq!(restarts.lock().unwrap().len(), 3, "3 respawns before giving up");
    assert_eq!(gave_up.lock().unwrap().len(), 1);
    let store = t.markers();
    assert!(
        !store.markers.is_empty(),
        "every real death records forensics BEFORE the restart decision"
    );
    assert!(
        store.markers.iter().all(|m| m.exit_code == Some(1)),
        "markers carry the actual exit"
    );
    assert!(
        store.markers.last().unwrap().last_stderr.contains("out of memory"),
        "the OOM evidence must be in the marker"
    );
}

/// S7 (unix) — SIGKILL (the OS OOM killer's signature): the death must be
/// named as signal 9, not exit-code noise.
#[cfg(unix)]
#[test]
fn sigkill_is_named_as_signal_nine() {
    let t = TestApp::new(&Scenario {
        signal9: true,
        serve_ms: Some(1500),
        ..Default::default()
    });
    let h = t.run_bootstrap();
    join_with_timeout(h, Duration::from_secs(120), "sigkill loop");

    let msg = t.failed_message().expect("stage must be Failed");
    assert!(msg.contains("signal 9"), "signal deaths must be named, got: {msg}");
    let store = t.markers();
    let m = store.markers.last().expect("marker written");
    assert_eq!(m.exit_code, None);
    assert_eq!(m.signal, Some(9));
}

/// S8 — a deliberate kill (Retry/Clean&Retry owns the respawn): the
/// supervisor must yield silently — no crash marker, no restart, the stage
/// never Failed.
#[test]
fn deliberate_kill_yields_without_a_crash_marker() {
    let t = TestApp::new(&Scenario {
        serve_ms: Some(0), // serve forever
        ..Default::default()
    });
    let restarts = t.record_events("backend-restarting");
    let h = t.run_bootstrap();

    assert!(
        wait_until(Duration::from_secs(20), || matches!(
            t.stage_snapshot(),
            BootstrapStage::Ready
        )),
        "backend never reached Ready"
    );
    let before = t.markers().markers.len();
    set_backend_kill_intended(true);
    t.signal_tracked_child();
    join_with_timeout(h, Duration::from_secs(30), "deliberate kill");

    assert_eq!(t.markers().markers.len(), before, "no marker for an intentional kill");
    assert_eq!(restarts.lock().unwrap().len(), 0, "no respawn — the retry flow owns it");
    assert!(
        matches!(t.stage_snapshot(), BootstrapStage::Ready),
        "the stage must never flip to Failed for a deliberate replace"
    );
}

/// S9 — early-bind narration + a deferred-startup FATAL: the splash log
/// narrates the step the backend reported, and when it dies the named step
/// reaches both the user-facing diagnosis and the crash forensics.
#[test]
fn deferred_startup_failure_names_the_step() {
    let t = TestApp::new(&Scenario {
        stderr: "Traceback (most recent call last):\\n  File \"main.py\"\\nImportError: torch\\nFATAL: backend startup failed during 'ml_imports': ImportError: torch",
        exit: Some(1),
        serve_ms: Some(1500),
        progress_only: true, // /startup/progress answers; health probes do not
        ..Default::default()
    });
    let h = t.run_bootstrap();
    join_with_timeout(h, Duration::from_secs(60), "deferred FATAL");

    let msg = t.failed_message().expect("stage must be Failed");
    assert!(
        msg.contains("FATAL: backend startup failed during 'ml_imports'"),
        "the named step must reach the user, got: {msg}"
    );
    let store = t.markers();
    assert!(store
        .markers
        .last()
        .expect("marker written")
        .last_stderr
        .contains("failed during 'ml_imports'"));
    let logs = t.logs.lock().unwrap_or_else(|e| e.into_inner());
    assert!(
        logs.iter().any(|l| l.line.contains("Startup: Loading ML runtime")),
        "the launch poll must narrate the step the backend reported; logs: {:?}",
        logs.iter().map(|l| &l.line).collect::<Vec<_>>()
    );
}
