//! Sidecar detection, FFmpeg/ffprobe resolution, and on-demand downloads.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Output, Stdio};
#[cfg(target_os = "macos")]
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

#[cfg(unix)]
use std::os::fd::{AsRawFd, FromRawFd, IntoRawFd, OwnedFd, RawFd};
#[cfg(unix)]
use std::os::unix::net::UnixStream;

use crate::config::get_effective_region;
#[allow(unused_imports)] // Used in cfg(linux) and cfg(windows) blocks
use crate::config::resolve_github_url;
use crate::bootstrap::{BootstrapStage, set_stage};

/// Windows: run a child process with **no console window**.
///
/// A GUI app (no attached console) that spawns a console subprocess makes
/// Windows allocate a fresh console for it — a black `cmd`-style window that
/// flashes on screen for the child's lifetime. During first-run bootstrap we
/// spawn *dozens* of them (`uv venv`, `uv sync`, and a string of short
/// `python -c` capability probes), so the user sees a storm of terminal
/// windows popping up while dependencies install. `CREATE_NO_WINDOW`
/// (0x08000000) runs the child with no console at all; every caller already
/// pipes/among nulls stdout+stderr, so nothing visible or logged is lost.
///
/// Short-lived bootstrap/tools spawns route through this chokepoint;
/// long-running children use [`spawn_process_tree`], which preserves the same
/// flag while also making descendants terminable. No-op on macOS/Linux —
/// there is no per-process console to hide there, so behaviour is unchanged on
/// those platforms (default-parity rule: no stray windows on any OS).
///
/// Returns the same `&mut Command` so it chains inline:
/// `no_window(Command::new(p).args([..])).output()`.
#[inline]
pub fn no_window(cmd: &mut Command) -> &mut Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

/// A spawned child plus its non-reusable OS containment primitive. Unix uses
/// an inherited process group plus a CLOEXEC drain channel for deliberately
/// nested operation groups; Windows uses a Job Object assigned while the root
/// is suspended, before it can create any descendants.
pub struct ContainedChild {
    pub child: Child,
    pub tree: OwnedProcessTree,
}

pub struct OwnedProcessTree {
    root_pid: u32,
    terminated: bool,
    #[cfg(unix)]
    process_group: libc::pid_t,
    #[cfg(unix)]
    nested_drain: OwnedFd,
    #[cfg(target_os = "macos")]
    exit_kqueue: OwnedFd,
    #[cfg(target_os = "macos")]
    root_exit_state: AtomicU8,
    #[cfg(windows)]
    job: std::os::windows::io::OwnedHandle,
}

// How long a Darwin EPERM waits for the root's exit to register. XNU stops
// signalling a process as soon as it starts exiting, but posts NOTE_EXIT only
// later in the same exit, so a KILL landing in between sees EPERM while the
// exit probe still reads "alive". The gap is normally microseconds; this
// bounds the wait for a root that really is alive and unsignalable.
#[cfg(unix)]
const DARWIN_EXIT_SETTLE: Duration = Duration::from_millis(250);

// Keep the delivery and post-error ownership probe together: the root may
// exit between any earlier liveness check and either TERM or KILL delivery.
#[cfg(unix)]
fn signal_process_group_with(
    signal: libc::c_int,
    darwin: bool,
    settle: Duration,
    send: impl FnOnce(libc::c_int) -> io::Result<()>,
    mut root_exited_unreaped: impl FnMut() -> io::Result<bool>,
) -> io::Result<()> {
    match send(signal) {
        Ok(()) => Ok(()),
        Err(error) if error.raw_os_error() == Some(libc::ESRCH) => Ok(()),
        Err(error) if darwin && error.raw_os_error() == Some(libc::EPERM) => {
            // Darwin can report EPERM when the last signalable member exited
            // during delivery. Accept only a newly verified unreaped root:
            // live roots, lost identity and probe failures remain errors.
            // Callers must still join nested drain before reaping that root.
            let deadline = std::time::Instant::now() + settle;
            loop {
                if root_exited_unreaped()? {
                    return Ok(());
                }
                if std::time::Instant::now() >= deadline {
                    return Err(error);
                }
                std::thread::sleep(Duration::from_millis(1));
            }
        }
        Err(error) => Err(error),
    }
}

impl OwnedProcessTree {
    #[cfg(unix)]
    fn root_exited_unreaped(&self) -> io::Result<bool> {
        #[cfg(target_os = "macos")]
        {
            // Darwin may reject waitid(WNOWAIT) with EPERM. The kqueue was
            // registered while this Child was created, so NOTE_EXIT observes
            // the same process identity without consuming its wait status.
            // NOTE_REAP keeps the filter alive through wait(2), allowing us
            // to reject a group ID after another caller consumed that status.
            const EXITED: u8 = 1;
            const REAPED: u8 = 2;
            if self.root_exit_state.load(Ordering::Acquire) == REAPED {
                return Err(io::Error::from_raw_os_error(libc::ECHILD));
            }
            let mut event: libc::kevent = unsafe { std::mem::zeroed() };
            let timeout = libc::timespec {
                tv_sec: 0,
                tv_nsec: 0,
            };
            let result = unsafe {
                libc::kevent(
                    self.exit_kqueue.as_raw_fd(),
                    std::ptr::null(),
                    0,
                    &mut event,
                    1,
                    &timeout,
                )
            };
            if result < 0 {
                return Err(io::Error::last_os_error());
            }
            if result == 0 {
                return Ok(self.root_exit_state.load(Ordering::Acquire) == EXITED);
            }
            if event.flags & libc::EV_ERROR != 0 {
                return Err(io::Error::from_raw_os_error(event.data as i32));
            }
            if event.fflags & libc::NOTE_REAP != 0 {
                self.root_exit_state.store(REAPED, Ordering::Release);
                return Err(io::Error::from_raw_os_error(libc::ECHILD));
            }
            if event.fflags & libc::NOTE_EXIT != 0 {
                self.root_exit_state.store(EXITED, Ordering::Release);
            }
            return Ok(self.root_exit_state.load(Ordering::Acquire) == EXITED);
        }
        #[cfg(not(target_os = "macos"))]
        {
            let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
            let result = unsafe {
                libc::waitid(
                    libc::P_PID,
                    self.root_pid as libc::id_t,
                    &mut info,
                    libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
                )
            };
            if result != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(unsafe { info.si_pid() } != 0)
        }
    }

    #[cfg(unix)]
    fn signal_group(&self, signal: libc::c_int) -> io::Result<()> {
        signal_process_group_with(
            signal,
            cfg!(target_os = "macos"),
            DARWIN_EXIT_SETTLE,
            |signal| {
                if unsafe { libc::kill(-self.process_group, signal) } == 0 {
                    Ok(())
                } else {
                    Err(io::Error::last_os_error())
                }
            },
            || self.root_exited_unreaped(),
        )
    }

    fn force_terminate(&mut self) -> io::Result<()> {
        if self.terminated {
            return Ok(());
        }
        #[cfg(unix)]
        self.signal_group(libc::SIGKILL)?;
        #[cfg(windows)]
        {
            use std::os::windows::io::AsRawHandle;
            use windows::Win32::Foundation::HANDLE;
            use windows::Win32::System::JobObjects::TerminateJobObject;

            unsafe {
                TerminateJobObject(HANDLE(self.job.as_raw_handle()), 1)
                    .map_err(windows_error)?;
            }
        }
        self.terminated = true;
        Ok(())
    }

    fn wait_nested_drain(&mut self, timeout: Duration) -> io::Result<()> {
        #[cfg(unix)]
        {
            let fd = self.nested_drain.as_raw_fd();
            let deadline = std::time::Instant::now() + timeout;
            loop {
                if nested_drain_eof(fd)? {
                    return Ok(());
                }
                let remaining = deadline.saturating_duration_since(std::time::Instant::now());
                if remaining.is_zero() {
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        format!(
                            "nested backend operations did not drain within {timeout:?}"
                        ),
                    ));
                }
                let millis = remaining.as_millis().clamp(1, i32::MAX as u128) as i32;
                let mut pollfd = libc::pollfd {
                    fd,
                    events: libc::POLLIN | libc::POLLHUP | libc::POLLERR,
                    revents: 0,
                };
                let result = unsafe { libc::poll(&mut pollfd, 1, millis) };
                if result > 0 {
                    continue;
                }
                if result == 0 {
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        format!(
                            "nested backend operations did not drain within {timeout:?}"
                        ),
                    ));
                }
                let error = io::Error::last_os_error();
                if error.kind() != io::ErrorKind::Interrupted {
                    return Err(error);
                }
            }
        }
        #[cfg(not(unix))]
        {
            let _ = timeout;
            Ok(())
        }
    }
}

#[cfg(unix)]
fn nested_drain_eof(fd: RawFd) -> io::Result<bool> {
    let mut buffer = [0u8; 64];
    loop {
        let read = unsafe { libc::read(fd, buffer.as_mut_ptr().cast(), buffer.len()) };
        if read == 0 {
            return Ok(true);
        }
        if read > 0 {
            continue;
        }
        let error = io::Error::last_os_error();
        if error.kind() == io::ErrorKind::WouldBlock {
            return Ok(false);
        }
        if error.kind() != io::ErrorKind::Interrupted {
            return Err(error);
        }
    }
}

#[cfg(unix)]
fn create_nested_drain() -> io::Result<(OwnedFd, OwnedFd)> {
    // UnixStream::pair creates both descriptors CLOEXEC atomically inside the
    // standard library on Linux and macOS. A pipe()+fcntl sequence would have
    // an inheritance race with another thread spawning between those calls.
    let (read, write) = UnixStream::pair()?;
    read.set_nonblocking(true)?;
    let read = unsafe { OwnedFd::from_raw_fd(read.into_raw_fd()) };
    let write = unsafe { OwnedFd::from_raw_fd(write.into_raw_fd()) };
    Ok((read, write))
}

#[cfg(target_os = "macos")]
fn watch_process_exit(pid: u32) -> io::Result<OwnedFd> {
    let raw_fd = unsafe { libc::kqueue() };
    if raw_fd < 0 {
        return Err(io::Error::last_os_error());
    }
    let queue = unsafe { OwnedFd::from_raw_fd(raw_fd) };
    let change = libc::kevent {
        ident: pid as libc::uintptr_t,
        filter: libc::EVFILT_PROC,
        flags: libc::EV_ADD | libc::EV_ENABLE,
        fflags: libc::NOTE_EXIT | libc::NOTE_REAP,
        data: 0,
        udata: std::ptr::null_mut(),
    };
    let result = unsafe {
        libc::kevent(
            queue.as_raw_fd(),
            &change,
            1,
            std::ptr::null_mut(),
            0,
            std::ptr::null(),
        )
    };
    if result < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(queue)
}

fn nested_drain_timeout() -> Duration {
    #[cfg(debug_assertions)]
    if let Some(timeout) = std::env::var("OMNIVOICE_TEST_NESTED_DRAIN_TIMEOUT_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|&millis| millis > 0)
    {
        return Duration::from_millis(timeout);
    }
    Duration::from_secs(5)
}

impl Drop for OwnedProcessTree {
    fn drop(&mut self) {
        #[cfg(unix)]
        if !self.terminated && self.root_exited_unreaped().is_ok() {
            // Cancellation/panic fallback. The waitid success proves the root
            // is still our unreaped child, so its group ID cannot have been
            // reused. ECHILD deliberately falls through without signalling.
            let _ = self.signal_group(libc::SIGKILL);
        }
        // Windows JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE provides this fallback
        // automatically when `job` is dropped.
    }
}

/// Spawn into deterministic containment. No descendant discovery is involved:
/// ordinary children inherit this process group/job at creation, while nested
/// Python operation owners inherit a drain writer which Rust joins on teardown.
pub fn spawn_process_tree(cmd: &mut Command) -> io::Result<ContainedChild> {
    // Python operation owners may open a nested session for local timeouts;
    // their inherited drain writer makes that handoff joinable from Rust.
    cmd.env("OMNIVOICE_DESKTOP_CONTAINED", "1");
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        let (nested_drain, nested_drain_write) = create_nested_drain()?;
        let nested_drain_fd = nested_drain_write.as_raw_fd();
        cmd.env("OMNIVOICE_DESKTOP_DRAIN_FD", nested_drain_fd.to_string());
        unsafe {
            cmd.pre_exec(move || {
                let flags = libc::fcntl(nested_drain_fd, libc::F_GETFD);
                if flags < 0
                    || libc::fcntl(
                        nested_drain_fd,
                        libc::F_SETFD,
                        flags & !libc::FD_CLOEXEC,
                    ) < 0
                {
                    return Err(io::Error::last_os_error());
                }
                Ok(())
            });
        }
        cmd.process_group(0);
        #[cfg(target_os = "macos")]
        let mut child = cmd.spawn()?;
        #[cfg(not(target_os = "macos"))]
        let child = cmd.spawn()?;
        drop(nested_drain_write);
        let root_pid = child.id();
        let process_group = i32::try_from(root_pid)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "pid exceeds i32"))?;
        #[cfg(target_os = "macos")]
        let exit_kqueue = match watch_process_exit(root_pid) {
            Ok(queue) => queue,
            Err(error) => {
                unsafe {
                    libc::kill(-process_group, libc::SIGKILL);
                }
                let _ = child.wait();
                return Err(error);
            }
        };
        return Ok(ContainedChild {
            child,
            tree: OwnedProcessTree {
                root_pid,
                terminated: false,
                process_group,
                nested_drain,
                #[cfg(target_os = "macos")]
                exit_kqueue,
                #[cfg(target_os = "macos")]
                root_exit_state: AtomicU8::new(0),
            },
        });
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        const CREATE_SUSPENDED: u32 = 0x0000_0004;

        let job = create_kill_on_close_job()?;
        cmd.creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED);
        let mut child = cmd.spawn()?;
        if let Err(error) = assign_job_and_resume(&job, &child) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
        let root_pid = child.id();
        return Ok(ContainedChild {
            child,
            tree: OwnedProcessTree {
                root_pid,
                terminated: false,
                job,
            },
        });
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = cmd;
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "process containment is unsupported on this platform",
        ))
    }
}

/// Observe an unexpected root death without a PID check/signal race. Unix
/// leaves the root unreaped while signalling its still-reserved process group
/// and waiting for every nested owner to drain; Windows terminates the stable
/// Job handle after the stable Child handle reports exit.
pub fn contained_child_exit(
    child: &mut Child,
    tree: &mut OwnedProcessTree,
) -> io::Result<Option<ExitStatus>> {
    #[cfg(unix)]
    {
        match tree.root_exited_unreaped() {
            Ok(false) => return Ok(None),
            Ok(true) => {
                // Do not reap the root until group cleanup succeeds: the
                // zombie is what keeps this process-group ID non-reusable.
                tree.force_terminate()?;
                tree.wait_nested_drain(nested_drain_timeout())?;
                let status = child.try_wait()?;
                return Ok(status);
            }
            Err(error) if error.raw_os_error() == Some(libc::ECHILD) => {
                // Another owner already reaped this root. Refuse to signal its
                // numeric group: it may since have been reused by a foreign
                // process. Production lifecycle paths never take this branch.
                return Err(io::Error::new(
                    io::ErrorKind::Other,
                    format!(
                        "process-tree root {} was already reaped; refusing a reusable group ID",
                        tree.root_pid
                    ),
                ));
            }
            Err(error) => return Err(error),
        }
    }
    #[cfg(windows)]
    {
        let status = child.try_wait()?;
        if status.is_some() {
            tree.force_terminate()?;
        }
        Ok(status)
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = tree;
        child.try_wait()
    }
}

/// Ask the contained tree to stop, then force the same stable containment
/// primitive before reaping the root. On Unix the unreaped root reserves the
/// process-group ID and keeps drain-timeout retries safe; a foreign group can
/// never be substituted between an identity check and signal. Windows never
/// signals by PID at all.
pub fn terminate_process_tree(
    child: &mut Child,
    tree: &mut OwnedProcessTree,
    graceful_timeout: Duration,
) -> io::Result<ExitStatus> {
    let pid = child.id();
    #[cfg(unix)]
    match tree.root_exited_unreaped() {
        Ok(true) => {
            tree.force_terminate()?;
            tree.wait_nested_drain(nested_drain_timeout())?;
            return child.wait();
        }
        Ok(false) => tree.signal_group(libc::SIGTERM)?,
        Err(error) if error.raw_os_error() == Some(libc::ECHILD) => {
            return Err(io::Error::new(
                io::ErrorKind::Other,
                format!(
                    "process-tree root {pid} was already reaped; refusing a reusable group ID"
                ),
            ));
        }
        Err(error) => return Err(error),
    }
    #[cfg(windows)]
    {
        // A console-less GUI child has no reliable graceful control event.
        // Terminating the stable job is the existing forced fallback, now
        // guaranteed to include every descendant.
        tree.force_terminate()?;
    }

    let deadline = std::time::Instant::now() + graceful_timeout;
    while std::time::Instant::now() < deadline {
        #[cfg(unix)]
        if tree.root_exited_unreaped()? {
            tree.force_terminate()?;
            tree.wait_nested_drain(nested_drain_timeout())?;
            return child.wait();
        }
        #[cfg(windows)]
        if let Some(status) = child.try_wait()? {
            return Ok(status);
        }
        std::thread::sleep(Duration::from_millis(50));
    }

    log::warn!(
        "Process tree rooted at pid {pid} did not stop within {graceful_timeout:?}; forcing it"
    );
    tree.force_terminate()?;
    let _ = child.kill();
    tree.wait_nested_drain(nested_drain_timeout())?;
    child.wait()
}

#[cfg(windows)]
fn windows_error(error: windows_core::Error) -> io::Error {
    io::Error::new(io::ErrorKind::Other, error.to_string())
}

#[cfg(windows)]
fn create_kill_on_close_job() -> io::Result<std::os::windows::io::OwnedHandle> {
    use std::os::windows::io::{AsRawHandle, FromRawHandle};
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::System::JobObjects::{
        CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
        SetInformationJobObject,
    };

    let job = unsafe { CreateJobObjectW(None, None).map_err(windows_error)? };
    let job = unsafe { std::os::windows::io::OwnedHandle::from_raw_handle(job.0) };
    let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    unsafe {
        SetInformationJobObject(
            HANDLE(job.as_raw_handle()),
            JobObjectExtendedLimitInformation,
            std::ptr::addr_of!(info).cast(),
            std::mem::size_of_val(&info) as u32,
        )
        .map_err(windows_error)?;
        Ok(job)
    }
}

#[cfg(windows)]
fn assign_job_and_resume(
    job: &std::os::windows::io::OwnedHandle,
    child: &Child,
) -> io::Result<()> {
    use std::os::windows::io::{AsRawHandle, FromRawHandle};
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, TH32CS_SNAPTHREAD, THREADENTRY32, Thread32First, Thread32Next,
    };
    use windows::Win32::System::JobObjects::AssignProcessToJobObject;
    use windows::Win32::System::Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME};

    unsafe {
        AssignProcessToJobObject(
            HANDLE(job.as_raw_handle()),
            HANDLE(child.as_raw_handle()),
        )
        .map_err(windows_error)?;
    }

    let snapshot = unsafe {
        CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0).map_err(windows_error)?
    };
    let snapshot = unsafe { std::os::windows::io::OwnedHandle::from_raw_handle(snapshot.0) };
    let mut entry = THREADENTRY32 {
        dwSize: std::mem::size_of::<THREADENTRY32>() as u32,
        ..Default::default()
    };
    unsafe {
        Thread32First(HANDLE(snapshot.as_raw_handle()), &mut entry).map_err(windows_error)?;
    }
    loop {
        if entry.th32OwnerProcessID == child.id() {
            let thread = unsafe {
                OpenThread(THREAD_SUSPEND_RESUME, false, entry.th32ThreadID)
                    .map_err(windows_error)?
            };
            let thread = unsafe { std::os::windows::io::OwnedHandle::from_raw_handle(thread.0) };
            if unsafe { ResumeThread(HANDLE(thread.as_raw_handle())) } == u32::MAX {
                return Err(io::Error::last_os_error());
            }
            return Ok(());
        }
        if unsafe { Thread32Next(HANDLE(snapshot.as_raw_handle()), &mut entry) }.is_err() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "suspended child primary thread was not found",
            ));
        }
    }
}

// Version of the Astral `uv` binary we download at first run when no system
// uv is on PATH. Pinned for reproducibility — bump alongside the uv.lock
// when the toolchain needs a newer uv.
pub const UV_VERSION: &str = "0.12.13";

// Version of BtbN/FFmpeg-Builds we download for Linux/Windows ffmpeg first-
// run setup. The string appears *twice* in each URL (once as the release tag,
// once inside the archive filename) — BtbN tags their autobuilds
// `autobuild-YYYY-MM-DD-HH-MM` and the inner filenames use the same datestamp.
// Driving both from one constant means pinning to a specific autobuild is a
// one-line edit: change `"latest"` to e.g. `"autobuild-2026-04-15-12-50"` and
// match the same constant in `.github/workflows/release.yml`
// (FFMPEG_BTBN_VERSION env var). Reproducible installer builds without
// surprise upstream regressions, AV reputation drift, or 2am pages when BtbN
// retags `latest` to a build that fails Windows SmartScreen.
//
// Browse releases: https://github.com/BtbN/FFmpeg-Builds/releases
pub const FFMPEG_BTBN_VERSION: &str = "latest";

// ── Sidecar detection ─────────────────────────────────────────────────────

/// Look for a sidecar binary bundled alongside the app via Tauri's
/// `bundle.externalBin`. Tauri places the per-target sidecar at the same
/// path as the main app executable on Linux/Windows, and inside
/// `Contents/MacOS/` on macOS .app bundles. The bundled file keeps its
/// `<name>-<target-triple>{.exe}` name.
///
/// Returns `None` in dev (`cargo run`) builds where the sidecar wasn't
/// bundled — the caller then falls back to PATH lookup or other strategies.
pub fn find_bundled_sidecar(name: &str) -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?;
    let triple = match (std::env::consts::OS, std::env::consts::ARCH) {
        ("macos", "aarch64") => "aarch64-apple-darwin",
        ("macos", "x86_64") => "x86_64-apple-darwin",
        ("linux", "x86_64") => "x86_64-unknown-linux-gnu",
        ("windows", "x86_64") => "x86_64-pc-windows-msvc",
        _ => return None,
    };
    let ext = if cfg!(windows) { ".exe" } else { "" };
    let candidate = dir.join(format!("{}-{}{}", name, triple, ext));
    if !candidate.is_file() {
        return None;
    }
    // build.rs writes a zero-byte placeholder so tauri-build's externalBin
    // existence check passes during dev / `cargo check`. Reject it here so
    // we don't try to exec an empty file — callers fall back to PATH lookup
    // or pip-bundled binaries instead.
    let len = std::fs::metadata(&candidate).ok().map(|m| m.len()).unwrap_or(0);
    if len < 1024 {
        return None;
    }
    Some(candidate)
}

pub fn find_bundled_uv() -> Option<PathBuf> { find_bundled_sidecar("uv") }
pub fn find_bundled_ffmpeg() -> Option<PathBuf> { find_bundled_sidecar("ffmpeg") }
pub fn find_bundled_ffprobe() -> Option<PathBuf> { find_bundled_sidecar("ffprobe") }

// ── On-demand ffmpeg / ffprobe download ───────────────────────────────────
//
// Sources:
//   macOS:   evermeet.cx — individual .zip per binary (x86_64, runs via Rosetta on arm64)
//   Linux:   BtbN/FFmpeg-Builds — single .tar.xz with both binaries
//   Windows: BtbN/FFmpeg-Builds — single .zip with both binaries

/// Download and cache static ffmpeg + ffprobe binaries into `dest`.
/// Idempotent: skips the download when both binaries already exist.
#[allow(unused_variables)] // `region` only used in linux/windows cfg blocks
pub fn install_ffmpeg_standalone(dest: &Path, region: &str) -> io::Result<()> {
    let ffmpeg_bin = dest.join(if cfg!(windows) { "ffmpeg.exe" } else { "ffmpeg" });
    let ffprobe_bin = dest.join(if cfg!(windows) { "ffprobe.exe" } else { "ffprobe" });
    if ffmpeg_bin.is_file() && ffprobe_bin.is_file() {
        return Ok(());
    }
    fs::create_dir_all(dest)?;

    #[cfg(target_os = "macos")]
    {
        // Prefer native arm64 ffmpeg via Homebrew — always latest, includes
        // ffprobe, zero Rosetta overhead on Apple Silicon.
        let brew_candidates = ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"];
        let brew_path = brew_candidates.iter().find(|p| PathBuf::from(p).is_file());
        if let Some(brew) = brew_path {
            log::info!("Installing ffmpeg via Homebrew (native arm64)");
            let status = Command::new(brew)
                .args(["install", "ffmpeg"])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            if matches!(status, Ok(ref s) if s.success()) {
                // brew install succeeded — ffmpeg/ffprobe are now on PATH
                // at /opt/homebrew/bin/ or /usr/local/bin/. No need to
                // cache in tools/ — resolve_ffmpeg will find them via PATH.
                return Ok(());
            }
            log::warn!("brew install ffmpeg failed — falling back to evermeet.cx");
        }
        // Fallback: evermeet.cx static binaries (x86_64, runs via Rosetta).
        for (tool, url) in [
            ("ffmpeg", "https://evermeet.cx/ffmpeg/getrelease/zip"),
            ("ffprobe", "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"),
        ] {
            let bin_path = dest.join(tool);
            if bin_path.is_file() {
                continue;
            }
            log::info!("Downloading {} from evermeet.cx", tool);
            let zip_path = dest.join(format!("{}.zip", tool));
            let resp = ureq::get(url)
                .timeout(Duration::from_secs(120))
                .call()
                .map_err(|e| io::Error::new(io::ErrorKind::Other, format!("{} download: {}", tool, e)))?;
            if resp.status() != 200 {
                return Err(io::Error::new(
                    io::ErrorKind::Other,
                    format!("{} download HTTP {}", tool, resp.status()),
                ));
            }
            let mut zip_file = fs::File::create(&zip_path)?;
            io::copy(&mut resp.into_reader(), &mut zip_file)?;
            drop(zip_file);
            let status = Command::new("unzip")
                .args(["-o", "-j"])
                .arg(&zip_path)
                .arg("-d")
                .arg(dest)
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()?;
            let _ = fs::remove_file(&zip_path);
            if !status.success() {
                return Err(io::Error::new(io::ErrorKind::Other, format!("unzip {} failed", tool)));
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                if let Ok(meta) = fs::metadata(&bin_path) {
                    let mut perms = meta.permissions();
                    perms.set_mode(0o755);
                    let _ = fs::set_permissions(&bin_path, perms);
                }
            }
        }
        return Ok(());
    }

    #[cfg(target_os = "linux")]
    {
        let url = resolve_github_url(
            &format!(
                "https://github.com/BtbN/FFmpeg-Builds/releases/download/{ver}/ffmpeg-master-{ver}-linux64-gpl.tar.xz",
                ver = FFMPEG_BTBN_VERSION,
            ),
            region,
        );
        log::info!("Downloading ffmpeg from BtbN (linux64) — version={}", FFMPEG_BTBN_VERSION);
        let archive_path = dest.join("ffmpeg.tar.xz");
        let resp = ureq::get(&url)
            .timeout(Duration::from_secs(300))
            .call()
            .map_err(|e| io::Error::new(io::ErrorKind::Other, format!("ffmpeg download: {}", e)))?;
        if resp.status() != 200 {
            return Err(io::Error::new(
                io::ErrorKind::Other,
                format!("ffmpeg download HTTP {}", resp.status()),
            ));
        }
        let mut archive_file = fs::File::create(&archive_path)?;
        io::copy(&mut resp.into_reader(), &mut archive_file)?;
        drop(archive_file);
        let status = Command::new("tar")
            .args(["-xJf"])
            .arg(&archive_path)
            .arg("-C")
            .arg(dest)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()?;
        let _ = fs::remove_file(&archive_path);
        if !status.success() {
            return Err(io::Error::new(io::ErrorKind::Other, "tar -xJf ffmpeg failed"));
        }
        for entry in fs::read_dir(dest)? {
            let entry = entry?;
            let p = entry.path();
            if p.is_dir() {
                let bin_dir = p.join("bin");
                if bin_dir.is_dir() {
                    for tool in ["ffmpeg", "ffprobe"] {
                        let src = bin_dir.join(tool);
                        if src.is_file() {
                            let dst = dest.join(tool);
                            let _ = fs::rename(&src, &dst).or_else(|_| {
                                fs::copy(&src, &dst).map(|_| ())
                            });
                        }
                    }
                    let _ = fs::remove_dir_all(&p);
                    break;
                }
            }
        }
        for tool in ["ffmpeg", "ffprobe"] {
            let bin = dest.join(tool);
            if bin.is_file() {
                use std::os::unix::fs::PermissionsExt;
                if let Ok(meta) = fs::metadata(&bin) {
                    let mut perms = meta.permissions();
                    perms.set_mode(0o755);
                    let _ = fs::set_permissions(&bin, perms);
                }
            }
        }
        return Ok(());
    }

    #[cfg(target_os = "windows")]
    {
        use std::io::Read;
        let url = resolve_github_url(
            &format!(
                "https://github.com/BtbN/FFmpeg-Builds/releases/download/{ver}/ffmpeg-master-{ver}-win64-gpl.zip",
                ver = FFMPEG_BTBN_VERSION,
            ),
            region,
        );
        log::info!("Downloading ffmpeg from BtbN (win64) — version={}", FFMPEG_BTBN_VERSION);
        let resp = ureq::get(&url)
            .timeout(Duration::from_secs(300))
            .call()
            .map_err(|e| io::Error::new(io::ErrorKind::Other, format!("ffmpeg download: {}", e)))?;
        if resp.status() != 200 {
            return Err(io::Error::new(
                io::ErrorKind::Other,
                format!("ffmpeg download HTTP {}", resp.status()),
            ));
        }
        let mut buf = Vec::new();
        resp.into_reader().read_to_end(&mut buf)?;
        let mut archive = zip::ZipArchive::new(std::io::Cursor::new(buf))
            .map_err(|e| io::Error::new(io::ErrorKind::Other, format!("zip: {}", e)))?;
        for i in 0..archive.len() {
            let mut file = archive.by_index(i)
                .map_err(|e| io::Error::new(io::ErrorKind::Other, format!("zip entry: {}", e)))?;
            let name = file.name().to_string();
            let basename = name.rsplit('/').next().unwrap_or(&name);
            if basename == "ffmpeg.exe" || basename == "ffprobe.exe" {
                let out_path = dest.join(basename);
                let mut out_file = fs::File::create(&out_path)?;
                io::copy(&mut file, &mut out_file)?;
            }
        }
        return Ok(());
    }

    // Unsupported platform — not an error, caller falls back to PATH / imageio-ffmpeg.
    #[allow(unreachable_code)]
    Ok(())
}

/// Resolve a usable ffmpeg binary. Order: bundled sidecar → cached download
/// in app_data/tools → system PATH → on-demand download from the internet.
pub fn resolve_ffmpeg<R: tauri::Runtime>(app: &tauri::AppHandle<R>, app_data: &Path) -> Option<PathBuf> {
    if let Some(p) = find_bundled_ffmpeg() {
        log::info!("Using bundled ffmpeg at {}", p.display());
        return Some(p);
    }
    let tools_dir = app_data.join("tools");
    let cached = tools_dir.join(if cfg!(windows) { "ffmpeg.exe" } else { "ffmpeg" });
    if cached.is_file() {
        log::info!("Using cached ffmpeg at {}", cached.display());
        return Some(cached);
    }
    if no_window(Command::new("ffmpeg").arg("-version").stdout(Stdio::null()).stderr(Stdio::null())).status().map(|s| s.success()).unwrap_or(false) {
        log::info!("Using system ffmpeg from PATH");
        return Some(PathBuf::from("ffmpeg"));
    }
    log::info!("No ffmpeg found — auto-installing");
    match install_ffmpeg_standalone(&tools_dir, &get_effective_region(app)) {
        Ok(()) => {
            if cached.is_file() {
                log::info!("Installed ffmpeg to {}", cached.display());
                return Some(cached);
            }
            for p in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"] {
                if PathBuf::from(p).is_file() {
                    log::info!("Installed ffmpeg at {}", p);
                    return Some(PathBuf::from(p));
                }
            }
            if no_window(Command::new("ffmpeg").arg("-version").stdout(Stdio::null()).stderr(Stdio::null())).status().map(|s| s.success()).unwrap_or(false) {
                return Some(PathBuf::from("ffmpeg"));
            }
            log::warn!("ffmpeg install completed but binary not found");
            None
        }
        Err(e) => {
            log::warn!("ffmpeg install failed: {} — backend will rely on imageio-ffmpeg", e);
            None
        }
    }
}

/// Resolve a usable ffprobe binary. Same cascade as ffmpeg, with one extra
/// step on Linux: probe the relocated .deb path at
/// `/usr/lib/omnivoice-studio/bin/ffprobe` (issue #76, see
/// `frontend/src-tauri/debian/postinst`). The bundled sidecar lookup via
/// `current_exe()` does not find this path because it lives outside the
/// binary's own directory, so we probe it explicitly here.
pub fn resolve_ffprobe<R: tauri::Runtime>(app: &tauri::AppHandle<R>, app_data: &Path) -> Option<PathBuf> {
    if let Some(p) = find_bundled_ffprobe() {
        log::info!("Using bundled ffprobe at {}", p.display());
        return Some(p);
    }
    // Linux .deb install path (#76 — ffprobe relocated out of /usr/bin to
    // avoid overwriting the system binary).
    #[cfg(target_os = "linux")]
    {
        let deb_path = PathBuf::from("/usr/lib/omnivoice-studio/bin/ffprobe");
        if deb_path.is_file() {
            log::info!("Using .deb-bundled ffprobe at {}", deb_path.display());
            return Some(deb_path);
        }
    }
    let tools_dir = app_data.join("tools");
    let cached = tools_dir.join(if cfg!(windows) { "ffprobe.exe" } else { "ffprobe" });
    if cached.is_file() {
        log::info!("Using cached ffprobe at {}", cached.display());
        return Some(cached);
    }
    if no_window(Command::new("ffprobe").arg("-version").stdout(Stdio::null()).stderr(Stdio::null())).status().map(|s| s.success()).unwrap_or(false) {
        log::info!("Using system ffprobe from PATH");
        return Some(PathBuf::from("ffprobe"));
    }
    if let Ok(()) = install_ffmpeg_standalone(&tools_dir, &get_effective_region(app)) {
        if cached.is_file() {
            log::info!("Installed ffprobe to {}", cached.display());
            return Some(cached);
        }
        for p in ["/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"] {
            if PathBuf::from(p).is_file() {
                log::info!("Installed ffprobe at {}", p);
                return Some(PathBuf::from(p));
            }
        }
        if no_window(Command::new("ffprobe").arg("-version").stdout(Stdio::null()).stderr(Stdio::null())).status().map(|s| s.success()).unwrap_or(false) {
            return Some(PathBuf::from("ffprobe"));
        }
    }
    None
}

// ── uv resolution ─────────────────────────────────────────────────────────

/// Resolve a usable `uv` binary. Order: bundled sidecar (shipped with the
/// release installer via `bundle.externalBin`), system PATH (dev / power
/// users), or — last resort — download via the official Astral installer.
pub fn resolve_uv<R: tauri::Runtime>(
    _app: &tauri::AppHandle<R>,
    app_data: &Path,
    progress: Option<&Arc<Mutex<BootstrapStage>>>,
) -> Result<PathBuf, String> {
    if let Some(p) = find_bundled_uv() {
        log::info!("Using bundled uv at {}", p.display());
        return Ok(p);
    }
    if uv_is_usable(Path::new("uv")) {
        log::info!("Using system uv from PATH");
        return Ok(PathBuf::from("uv"));
    }
    if let Some(p) = progress {
        set_stage(p, BootstrapStage::DownloadingUv { percent: None });
    }
    install_uv_standalone(&app_data.join("tools"), &get_effective_region(_app))
        .map_err(|e| format!("uv install failed: {}", e))
}

/// Install `uv` using the **official Astral installer scripts**.
///
/// Unix:    `curl -LsSf https://astral.sh/uv/{version}/install.sh | sh`
/// Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/{version}/install.ps1 | iex"`
///
/// The installer handles platform detection, checksums, and extraction
/// automatically. `UV_UNMANAGED_INSTALL` keeps this app-private tool out of
/// the user's PATH and shell profiles on every platform.
fn install_uv_standalone(dest: &Path, _region: &str) -> io::Result<PathBuf> {
    let uv_bin = dest.join(if cfg!(windows) { "uv.exe" } else { "uv" });
    if uv_is_usable(&uv_bin) {
        return Ok(uv_bin);
    }
    fs::create_dir_all(dest)?;
    log::info!("Installing uv {} via official installer into {}", UV_VERSION, dest.display());

    #[cfg(unix)]
    {
        let output = configure_uv_installer(
            Command::new("sh").args([
                "-c",
                &format!(
                    "curl -LsSf https://astral.sh/uv/{}/install.sh | sh",
                    UV_VERSION
                ),
            ]),
            dest,
        )
        .output()
        .map_err(|e| {
            io::Error::new(
                io::ErrorKind::Other,
                format!("uv installer launch failed (is curl installed?): {}", e),
            )
        })?;
        return finish_uv_install(dest, &uv_bin, output);
    }

    #[cfg(windows)]
    {
        let script = format!(
            "irm https://astral.sh/uv/{}/install.ps1 | iex",
            UV_VERSION
        );
        // Windows: `CREATE_NO_WINDOW` so the uv installer's PowerShell doesn't
        // flash a console window during first-run bootstrap. stdout/stderr are
        // piped, so nothing is lost.
        let mut command = Command::new("powershell");
        command.args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "ByPass",
            "-c",
            &script,
        ]);
        configure_uv_installer(&mut command, dest);
        let output = no_window(&mut command).output().map_err(|e| {
            io::Error::new(
                io::ErrorKind::Other,
                format!("uv PowerShell installer failed: {}", e),
            )
        })?;
        return finish_uv_install(dest, &uv_bin, output);
    }

    #[allow(unreachable_code)]
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "unsupported uv install platform",
    ))
}

fn configure_uv_installer<'a>(command: &'a mut Command, dest: &Path) -> &'a mut Command {
    // The official unmanaged mode is designed for app-private/CI installs: it
    // selects the destination and disables PATH, profile, and self-update
    // mutations. Explicitly remove the legacy variable so a parent shell
    // cannot leave the installer in two conflicting modes.
    command
        .env_remove("UV_INSTALL_DIR")
        .env("UV_UNMANAGED_INSTALL", dest)
        .env("UV_NO_MODIFY_PATH", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
}

fn uv_is_usable(path: &Path) -> bool {
    no_window(
        Command::new(path)
            .arg("--version")
            .stdout(Stdio::piped())
            .stderr(Stdio::null()),
    )
    .output()
    .map(|output| output.status.success() && uv_version_matches(&output.stdout))
    .unwrap_or(false)
}

fn uv_version_matches(output: &[u8]) -> bool {
    let Ok(text) = std::str::from_utf8(output) else {
        return false;
    };
    let mut fields = text.split_whitespace();
    fields.next() == Some("uv") && fields.next() == Some(UV_VERSION)
}

fn finish_uv_install(dest: &Path, uv_bin: &Path, output: Output) -> io::Result<PathBuf> {
    finish_uv_install_with_probe(dest, uv_bin, output, uv_is_usable)
}

fn finish_uv_install_with_probe<F>(
    dest: &Path,
    uv_bin: &Path,
    output: Output,
    is_usable: F,
) -> io::Result<PathBuf>
where
    F: Fn(&Path) -> bool,
{
    let alt = dest.join("bin").join(if cfg!(windows) { "uv.exe" } else { "uv" });
    if !is_usable(uv_bin) && is_usable(&alt) {
        fs::rename(&alt, uv_bin).or_else(|_| fs::copy(&alt, uv_bin).map(|_| ()))?;
    }

    // Some installer failures happen after extraction (for example while
    // editing a Windows shell profile). The installed executable is the real
    // postcondition: accepting a verified binary makes first run self-heal in
    // this process instead of requiring a restart. Never accept a partial or
    // corrupt file merely because it exists.
    if is_usable(uv_bin) {
        if output.status.success() {
            log::info!("uv installed successfully at {}", uv_bin.display());
        } else {
            log::warn!(
                "uv installer exited with {:?}, but the installed binary passed validation at {}",
                output.status.code(),
                uv_bin.display()
            );
        }
        return Ok(uv_bin.to_path_buf());
    }

    let detail = installer_output_detail(&output);
    Err(io::Error::new(
        io::ErrorKind::Other,
        if output.status.success() {
            format!(
                "uv installer completed but no usable binary was found at {}{}",
                uv_bin.display(),
                detail
            )
        } else {
            format!("uv installer exited with code {:?}{}", output.status.code(), detail)
        },
    ))
}

fn installer_output_detail(output: &Output) -> String {
    let bytes = if output.stderr.is_empty() {
        &output.stdout
    } else {
        &output.stderr
    };
    let text = String::from_utf8_lossy(bytes);
    let mut text = text.trim().to_string();
    if text.is_empty() {
        return String::new();
    }
    for key in ["USERPROFILE", "HOME"] {
        if let Some(home) = std::env::var_os(key).and_then(|value| value.into_string().ok()) {
            text = redact_home_prefix(&text, &home);
        }
    }
    let start = text
        .char_indices()
        .rev()
        .nth(1999)
        .map(|(index, _)| index)
        .unwrap_or(0);
    format!(": {}", &text[start..])
}

fn redact_home_prefix(text: &str, home: &str) -> String {
    if home.len() < 3 {
        return text.to_string();
    }
    let mut redacted = text.replace(home, "~");
    let forward = home.replace('\\', "/");
    let backward = home.replace('/', "\\");
    if forward != home {
        redacted = redacted.replace(&forward, "~");
    }
    if backward != home {
        redacted = redacted.replace(&backward, "~");
    }
    redacted
}

#[cfg(test)]
mod uv_tests {
    use super::*;
    use std::ffi::OsStr;

    #[cfg(unix)]
    #[test]
    fn darwin_group_signal_recovers_exit_during_term_or_kill_delivery() {
        for signal in [libc::SIGTERM, libc::SIGKILL] {
            let exited = std::cell::Cell::new(false);
            signal_process_group_with(
                signal,
                true,
                Duration::ZERO,
                |delivered| {
                    assert_eq!(delivered, signal);
                    assert!(!exited.replace(true));
                    Err(io::Error::from_raw_os_error(libc::EPERM))
                },
                || {
                    assert!(exited.get(), "ownership must be checked after failed delivery");
                    Ok(true)
                },
            )
            .expect("an exited unreaped root keeps its group reserved until drain completes");
        }
    }

    #[cfg(unix)]
    #[test]
    fn darwin_group_signal_waits_for_an_exit_that_registers_after_eperm() {
        // The flake behind contained_exit_probe_preserves_a_live_child: TERM
        // started the exit, KILL got EPERM, and NOTE_EXIT had not arrived yet.
        let probes = std::cell::Cell::new(0);
        signal_process_group_with(
            libc::SIGKILL,
            true,
            Duration::from_secs(5),
            |_| Err(io::Error::from_raw_os_error(libc::EPERM)),
            || {
                probes.set(probes.get() + 1);
                Ok(probes.get() >= 3)
            },
        )
        .expect("an exit that registers within the settle window is accepted");
        assert_eq!(probes.get(), 3);
    }

    #[cfg(unix)]
    #[test]
    fn darwin_group_signal_rejects_live_reaped_or_unverifiable_roots() {
        for signal in [libc::SIGTERM, libc::SIGKILL] {
            for (probe, expected) in [
                (Ok(false), libc::EPERM),
                (Err(libc::ECHILD), libc::ECHILD),
                (Err(libc::EIO), libc::EIO),
            ] {
                let error = signal_process_group_with(
                    signal,
                    true,
                    Duration::from_millis(20),
                    |_| Err(io::Error::from_raw_os_error(libc::EPERM)),
                    || probe.map_err(io::Error::from_raw_os_error),
                )
                .expect_err("only a confirmed unreaped exit can explain Darwin EPERM");
                assert_eq!(error.raw_os_error(), Some(expected));
            }
        }
    }

    #[cfg(unix)]
    #[test]
    fn group_signal_preserves_other_permission_errors_and_platforms() {
        for (darwin, errno) in [(false, libc::EPERM), (true, libc::EACCES)] {
            let error = signal_process_group_with(
                libc::SIGKILL,
                darwin,
                Duration::ZERO,
                |_| Err(io::Error::from_raw_os_error(errno)),
                || panic!("unrelated errors must not use the Darwin exit exception"),
            )
            .unwrap_err();
            assert_eq!(error.raw_os_error(), Some(errno));
        }
    }

    #[cfg(unix)]
    #[test]
    fn contained_exit_probe_preserves_a_live_child() {
        let mut command = Command::new("sleep");
        command.arg("30");
        let ContainedChild {
            mut child,
            mut tree,
        } = spawn_process_tree(&mut command).expect("spawn contained test child");

        assert!(contained_child_exit(&mut child, &mut tree).unwrap().is_none());
        terminate_process_tree(&mut child, &mut tree, Duration::ZERO)
            .expect("terminate the still-owned process tree");
    }

    #[cfg(unix)]
    #[test]
    fn unexpected_root_exit_without_descendants_is_reaped_cleanly() {
        let mut command = Command::new("sh");
        command.args(["-c", "exit 7"]);
        let ContainedChild {
            mut child,
            mut tree,
        } = spawn_process_tree(&mut command).expect("spawn contained test child");
        let deadline = std::time::Instant::now() + Duration::from_secs(5);

        loop {
            if let Some(status) = contained_child_exit(&mut child, &mut tree)
                .expect("clean an already-exited root")
            {
                assert_eq!(status.code(), Some(7));
                break;
            }
            assert!(std::time::Instant::now() < deadline, "child never exited");
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    #[cfg(unix)]
    #[test]
    fn reaped_root_refuses_to_signal_a_reusable_process_group() {
        let mut command = Command::new("sleep");
        command.arg("30");
        let ContainedChild {
            mut child,
            mut tree,
        } = spawn_process_tree(&mut command).expect("spawn contained test child");
        child.kill().unwrap();
        child.wait().unwrap(); // deliberately discard the stable root identity

        let error = terminate_process_tree(&mut child, &mut tree, Duration::ZERO)
            .expect_err("a reusable numeric process-group id must never be signalled");
        assert!(error.to_string().contains("already reaped"));
        assert!(error.to_string().contains("refusing"));
    }

    #[test]
    fn installer_uses_app_private_unmanaged_mode() {
        let mut command = Command::new("installer");
        configure_uv_installer(&mut command, Path::new("private-tools"));
        let envs: std::collections::HashMap<_, _> = command.get_envs().collect();

        assert_eq!(envs.get(OsStr::new("UV_INSTALL_DIR")), Some(&None));
        assert_eq!(
            envs.get(OsStr::new("UV_UNMANAGED_INSTALL")).and_then(|value| *value),
            Some(OsStr::new("private-tools"))
        );
        assert_eq!(
            envs.get(OsStr::new("UV_NO_MODIFY_PATH")).and_then(|value| *value),
            Some(OsStr::new("1"))
        );
    }

    #[test]
    fn uv_version_probe_requires_the_pinned_version() {
        assert!(uv_version_matches(
            format!("uv {} (build-id)\n", UV_VERSION).as_bytes()
        ));
        assert!(!uv_version_matches(b"uv 0.10.0 (older)\n"));
        assert!(!uv_version_matches(b"not-uv 0.11.7\n"));
        assert!(!uv_version_matches(b"uv\n"));
        assert!(!uv_version_matches(&[0xff, 0xfe]));
    }

    #[test]
    fn installer_error_includes_captured_stderr() {
        let output = Output {
            status: failure_status(),
            stdout: Vec::new(),
            stderr: b"profile update denied".to_vec(),
        };
        assert_eq!(installer_output_detail(&output), ": profile update denied");
    }

    #[test]
    fn installer_error_redacts_unix_and_windows_home_paths() {
        assert_eq!(
            redact_home_prefix(
                "installed into /Users/alice/.local/bin",
                "/Users/alice"
            ),
            "installed into ~/.local/bin"
        );
        assert_eq!(
            redact_home_prefix(
                r"installed into C:\Users\alice\.local\bin",
                r"C:\Users\alice"
            ),
            r"installed into ~\.local\bin"
        );
        assert_eq!(
            redact_home_prefix(
                "installed into C:/Users/alice/.local/bin",
                r"C:\Users\alice"
            ),
            "installed into ~/.local/bin"
        );
    }

    #[test]
    fn installer_exit_one_is_accepted_when_downloaded_uv_is_usable() {
        let dest = Path::new("private-tools");
        let uv_bin = dest.join(if cfg!(windows) { "uv.exe" } else { "uv" });
        let output = Output {
            status: failure_status(),
            stdout: Vec::new(),
            stderr: b"later installer step failed".to_vec(),
        };

        let result = finish_uv_install_with_probe(dest, &uv_bin, output, |candidate| {
            candidate == uv_bin
        });

        assert_eq!(result.unwrap(), uv_bin);
    }

    #[test]
    fn successful_installer_without_usable_uv_is_rejected() {
        let dest = Path::new("private-tools");
        let uv_bin = dest.join(if cfg!(windows) { "uv.exe" } else { "uv" });
        let output = Output {
            status: success_status(),
            stdout: Vec::new(),
            stderr: Vec::new(),
        };

        let error = finish_uv_install_with_probe(dest, &uv_bin, output, |_| false)
            .expect_err("installer success is insufficient without a usable binary");

        assert!(error.to_string().contains("no usable binary was found"));
    }

    #[test]
    fn failed_installer_with_unusable_uv_reports_captured_error() {
        let dest = Path::new("private-tools");
        let uv_bin = dest.join(if cfg!(windows) { "uv.exe" } else { "uv" });
        let output = Output {
            status: failure_status(),
            stdout: Vec::new(),
            stderr: b"downloaded executable was corrupt".to_vec(),
        };

        let error = finish_uv_install_with_probe(dest, &uv_bin, output, |_| false)
            .expect_err("an unusable download must not be accepted");

        assert!(error.to_string().contains("downloaded executable was corrupt"));
    }

    #[test]
    fn usable_legacy_bin_location_is_relocated() {
        let unique = format!(
            "voicestudio-uv-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let dest = std::env::temp_dir().join(unique);
        let uv_bin = dest.join(if cfg!(windows) { "uv.exe" } else { "uv" });
        let legacy = dest
            .join("bin")
            .join(if cfg!(windows) { "uv.exe" } else { "uv" });
        fs::create_dir_all(legacy.parent().unwrap()).unwrap();
        fs::write(&legacy, b"verified test executable").unwrap();
        let output = Output {
            status: success_status(),
            stdout: Vec::new(),
            stderr: Vec::new(),
        };

        let result = finish_uv_install_with_probe(&dest, &uv_bin, output, |candidate| {
            candidate.is_file()
        });

        assert_eq!(result.unwrap(), uv_bin);
        assert!(uv_bin.is_file());
        assert!(!legacy.exists());
        fs::remove_dir_all(dest).unwrap();
    }

    #[cfg(unix)]
    fn success_status() -> std::process::ExitStatus {
        use std::os::unix::process::ExitStatusExt;
        std::process::ExitStatus::from_raw(0)
    }

    #[cfg(windows)]
    fn success_status() -> std::process::ExitStatus {
        use std::os::windows::process::ExitStatusExt;
        std::process::ExitStatus::from_raw(0)
    }

    #[cfg(unix)]
    fn failure_status() -> std::process::ExitStatus {
        use std::os::unix::process::ExitStatusExt;
        std::process::ExitStatus::from_raw(1 << 8)
    }

    #[cfg(windows)]
    fn failure_status() -> std::process::ExitStatus {
        use std::os::windows::process::ExitStatusExt;
        std::process::ExitStatus::from_raw(1)
    }
}
