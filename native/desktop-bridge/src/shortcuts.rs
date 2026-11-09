//! Native press/release service. Registration and platform event pumping share
//! the main thread (required by macOS Carbon and Windows message-only windows).
//!
//! The thread sleeps in the OS's own wait (macOS run loop, Windows message
//! queue, Linux channel) until a hotkey, portal signal or command arrives;
//! commands and off-thread hotkey events wake it explicitly. It never ticks
//! while no shortcut is set, and only a slow backstop runs while one is.
use global_hotkey::{hotkey::HotKey, GlobalHotKeyEvent, GlobalHotKeyManager, HotKeyState};
use std::{
    str::FromStr,
    sync::{mpsc, Mutex},
    time::Duration,
};

/// Backstop wake while a shortcut is registered. Every delivery path wakes the
/// thread itself, so this only bounds the damage of a wake an OS drops.
pub(crate) const REGISTERED_BACKSTOP: Duration = Duration::from_millis(500);

enum Message {
    Replace(Option<String>, mpsc::SyncSender<Result<(), String>>),
    Stop,
    Hotkey {
        id: u32,
        pressed: bool,
    },
    #[cfg(target_os = "linux")]
    Portal {
        generation: u64,
        pressed: bool,
    },
}

#[derive(Clone)]
pub struct Control(mpsc::Sender<Message>);
pub struct Events {
    tx: mpsc::Sender<Message>,
    rx: mpsc::Receiver<Message>,
}
pub fn channel() -> (Control, Events) {
    let (tx, rx) = mpsc::channel();
    (Control(tx.clone()), Events { tx, rx })
}
impl Control {
    fn send(&self, message: Message) -> Result<(), String> {
        self.0
            .send(message)
            .map_err(|_| "Shortcut service stopped")?;
        waker::wake();
        Ok(())
    }
    pub fn replace(&self, accelerator: Option<String>) -> Result<(), String> {
        let (tx, rx) = mpsc::sync_channel(1);
        self.send(Message::Replace(accelerator, tx))?;
        rx.recv_timeout(Duration::from_secs(300))
            .map_err(|_| "Shortcut registration timed out")?
    }
    pub fn stop(&self) {
        let _ = self.send(Message::Stop);
    }
}

/// Where global-hotkey delivers events. Its handler can only be set once per
/// process and may run off the main thread (Windows release detection, the
/// Linux X11 thread), so it forwards into whichever service is running.
static HOTKEY_SINK: Mutex<Option<mpsc::Sender<Message>>> = Mutex::new(None);

fn forward_hotkey(event: GlobalHotKeyEvent) {
    let sink = HOTKEY_SINK
        .lock()
        .unwrap_or_else(|poison| poison.into_inner());
    if let Some(sink) = sink.as_ref() {
        let pressed = event.state == HotKeyState::Pressed;
        if sink
            .send(Message::Hotkey {
                id: event.id,
                pressed,
            })
            .is_ok()
        {
            waker::wake();
        }
    }
}

fn parse(raw: &str) -> Result<HotKey, String> {
    if raw.len() > 100 {
        return Err("Shortcut is too long".into());
    }
    let hotkey = HotKey::from_str(raw).map_err(|error| format!("Invalid shortcut: {error}"))?;
    if hotkey.mods.is_empty() {
        return Err("Shortcut needs a modifier".into());
    }
    Ok(hotkey)
}

/// How long the service may sleep before looking again. `None` means until
/// woken: commands and every hotkey path wake the thread explicitly.
pub(crate) fn wait_budget(backlog: bool, registered: bool) -> Option<Duration> {
    if backlog {
        Some(Duration::ZERO)
    } else if registered {
        Some(REGISTERED_BACKSTOP)
    } else {
        None
    }
}

/// Collapses repeated press/release reports into edges.
#[derive(Default)]
pub(crate) struct Latch {
    down: bool,
}
impl Latch {
    /// The edge to emit, if `pressed` changes the held state.
    pub(crate) fn apply(&mut self, pressed: bool) -> Option<bool> {
        (pressed != self.down).then(|| {
            self.down = pressed;
            pressed
        })
    }
    /// A synthetic release when the shortcut goes away mid-hold.
    pub(crate) fn reset(&mut self) -> Option<bool> {
        self.apply(false)
    }
}

pub fn run(events: Events, mut emit: impl FnMut(bool)) {
    waker::bind_current_thread();
    *HOTKEY_SINK
        .lock()
        .unwrap_or_else(|poison| poison.into_inner()) = Some(events.tx.clone());
    GlobalHotKeyEvent::set_event_handler(Some(forward_hotkey));
    let mut manager: Option<GlobalHotKeyManager> = None;
    let mut current: Option<HotKey> = None;
    let mut latch = Latch::default();
    #[cfg(target_os = "linux")]
    let portal = crate::wayland_shortcut_core::PortalShortcutState::for_desktop(
        "com.voicestudio.desktop",
        std::env::var_os("VOICESTUDIO_DESKTOP_EXE")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| std::env::current_exe().unwrap_or_default()),
    );
    #[cfg(target_os = "linux")]
    let mut portal_generation = 0u64;
    let mut backlog = true;
    'service: loop {
        let mut next = match waker::wait(&events.rx, wait_budget(backlog, current.is_some())) {
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
            Err(mpsc::RecvTimeoutError::Timeout) => None,
            Ok(message) => Some(message),
        };
        backlog = pump();
        loop {
            let message = match next.take() {
                Some(message) => message,
                None => match events.rx.try_recv() {
                    Ok(message) => message,
                    Err(mpsc::TryRecvError::Empty) => break,
                    Err(mpsc::TryRecvError::Disconnected) => break 'service,
                },
            };
            match message {
                Message::Stop => break 'service,
                Message::Hotkey { id, pressed } => {
                    if current.as_ref().map(|key| key.id()) == Some(id) {
                        if let Some(edge) = latch.apply(pressed) {
                            emit(edge);
                        }
                    }
                }
                #[cfg(target_os = "linux")]
                Message::Portal {
                    generation,
                    pressed,
                } => {
                    if generation == portal_generation {
                        if let Some(edge) = latch.apply(pressed) {
                            emit(edge);
                        }
                    }
                }
                Message::Replace(raw, reply) => {
                    let result = (|| {
                        let next = raw.as_deref().map(parse).transpose()?;
                        if next == current {
                            return Ok(());
                        }
                        #[cfg(target_os = "linux")]
                        if crate::wayland_shortcut_core::is_wayland_session() {
                            let next_generation = portal_generation.wrapping_add(1);
                            if let Some(raw) = raw {
                                let sender = events.tx.clone();
                                portal.replace(
                                    std::sync::Arc::new(move |pressed| {
                                        let _ = sender.send(Message::Portal {
                                            generation: next_generation,
                                            pressed,
                                        });
                                    }),
                                    raw,
                                )?;
                            } else {
                                portal.close();
                            }
                            if let Some(edge) = latch.reset() {
                                emit(edge);
                            }
                            portal_generation = next_generation;
                            current = next;
                            return Ok(());
                        }
                        if manager.is_none() && next.is_some() {
                            manager = Some(
                                GlobalHotKeyManager::new().map_err(|error| error.to_string())?,
                            );
                        }
                        if let Some(manager) = &manager {
                            // Bind replacement first so an invalid/conflicting shortcut
                            // cannot destroy a previously working registration.
                            if let Some(next) = next {
                                manager.register(next).map_err(|error| error.to_string())?;
                            }
                            if let Some(previous) = current {
                                if let Err(error) = manager.unregister(previous) {
                                    if let Some(next) = next {
                                        let _ = manager.unregister(next);
                                    }
                                    return Err(error.to_string());
                                }
                            }
                        }
                        if let Some(edge) = latch.reset() {
                            emit(edge);
                        }
                        current = next;
                        Ok(())
                    })();
                    let _ = reply.send(result);
                }
            }
        }
    }
    *HOTKEY_SINK
        .lock()
        .unwrap_or_else(|poison| poison.into_inner()) = None;
    #[cfg(target_os = "linux")]
    portal.close();
    if let Some(edge) = latch.reset() {
        emit(edge);
    }
    if let (Some(manager), Some(current)) = (manager, current) {
        let _ = manager.unregister(current);
    }
}

/// Dispatch queued OS events without waiting. `true` means more were left.
#[cfg(target_os = "windows")]
fn pump() -> bool {
    use windows::Win32::UI::WindowsAndMessaging::{
        DispatchMessageW, PeekMessageW, TranslateMessage, MSG, PM_REMOVE,
    };
    unsafe {
        let mut message = MSG::default();
        while PeekMessageW(&mut message, None, 0, 0, PM_REMOVE).as_bool() {
            let _ = TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }
    false
}
#[cfg(target_os = "macos")]
fn pump() -> bool {
    crate::macos_event_pump::pump()
}
#[cfg(not(any(target_os = "windows", target_os = "macos")))]
fn pump() -> bool {
    false // global-hotkey owns the X11 connection's event thread.
}

/// Sleeping and waking the service thread with each OS's native primitive.
mod waker {
    use std::{sync::mpsc, time::Duration};

    /// Windows: block in the thread's message queue (WM_HOTKEY lands there);
    /// other threads post a null message to it.
    #[cfg(target_os = "windows")]
    mod os {
        use std::sync::atomic::{AtomicU32, Ordering};
        use std::time::Duration;
        use windows::Win32::Foundation::{LPARAM, WPARAM};
        use windows::Win32::System::Threading::{GetCurrentThreadId, INFINITE};
        use windows::Win32::UI::WindowsAndMessaging::{
            MsgWaitForMultipleObjectsEx, PostThreadMessageW, MWMO_INPUTAVAILABLE, QS_ALLINPUT,
            WM_NULL,
        };
        static THREAD: AtomicU32 = AtomicU32::new(0);
        pub fn bind_current_thread() {
            THREAD.store(unsafe { GetCurrentThreadId() }, Ordering::SeqCst);
        }
        pub fn wake() {
            // Before the service's first pump there is no queue to post to,
            // but the loop drains the channel before it ever sleeps.
            let thread = THREAD.load(Ordering::SeqCst);
            if thread != 0 {
                let _ = unsafe { PostThreadMessageW(thread, WM_NULL, WPARAM(0), LPARAM(0)) };
            }
        }
        pub fn sleep(timeout: Option<Duration>) {
            let millis = timeout.map_or(INFINITE, |timeout| {
                u32::try_from(timeout.as_millis()).unwrap_or(INFINITE - 1)
            });
            unsafe {
                let _ = MsgWaitForMultipleObjectsEx(None, millis, QS_ALLINPUT, MWMO_INPUTAVAILABLE);
            }
        }
    }

    /// macOS: block in the main run loop, which also serves the main dispatch
    /// queue (keyboard output) and the window-server port carrying hotkeys.
    /// Other threads wake it by queueing an empty main-queue block.
    #[cfg(target_os = "macos")]
    mod os {
        use std::time::Duration;
        pub fn bind_current_thread() {}
        pub fn wake() {
            dispatch2::DispatchQueue::main().exec_async(|| {});
        }
        pub fn sleep(timeout: Option<Duration>) {
            crate::macos_event_pump::wait(timeout);
        }
    }

    pub fn bind_current_thread() {
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        os::bind_current_thread();
    }

    pub fn wake() {
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        os::wake();
    }

    /// Sleep until woken or `timeout`. macOS/Windows sleep in the OS queue and
    /// leave the channel to the caller.
    #[cfg(any(target_os = "windows", target_os = "macos"))]
    pub fn wait<T>(
        _rx: &mpsc::Receiver<T>,
        timeout: Option<Duration>,
    ) -> Result<T, mpsc::RecvTimeoutError> {
        if timeout != Some(Duration::ZERO) {
            os::sleep(timeout);
        }
        Err(mpsc::RecvTimeoutError::Timeout)
    }

    /// Linux has no main-thread event source of its own (X11 and the portal
    /// run on their own threads), so it blocks on the channel itself.
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    pub fn wait<T>(
        rx: &mpsc::Receiver<T>,
        timeout: Option<Duration>,
    ) -> Result<T, mpsc::RecvTimeoutError> {
        match timeout {
            Some(timeout) => rx.recv_timeout(timeout),
            None => rx.recv().map_err(|_| mpsc::RecvTimeoutError::Disconnected),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn requires_a_modified_valid_shortcut() {
        assert!(parse("CmdOrCtrl+Shift+Space").is_ok());
        assert!(parse("Ctrl+Alt+F24").is_ok());
        assert!(parse("Space").is_err());
        assert!(parse("Ctrl+NotAKey").is_err());
        assert!(parse(&"x".repeat(101)).is_err());
    }

    #[test]
    fn never_ticks_without_a_shortcut_and_only_slowly_with_one() {
        // #3924: the service woke every 8 ms whenever a shortcut was set.
        assert_eq!(wait_budget(false, false), None);
        assert_eq!(wait_budget(false, true), Some(REGISTERED_BACKSTOP));
        assert!(REGISTERED_BACKSTOP >= Duration::from_millis(250));
    }

    #[test]
    fn a_backlog_is_drained_without_sleeping() {
        assert_eq!(wait_budget(true, false), Some(Duration::ZERO));
        assert_eq!(wait_budget(true, true), Some(Duration::ZERO));
    }

    #[test]
    fn latch_reports_only_edges() {
        let mut latch = Latch::default();
        assert_eq!(latch.reset(), None);
        assert_eq!(latch.apply(true), Some(true));
        assert_eq!(latch.apply(true), None, "auto-repeat is not a new press");
        assert_eq!(latch.apply(false), Some(false));
        assert_eq!(latch.apply(false), None);
        latch.apply(true);
        assert_eq!(latch.reset(), Some(false), "replacing mid-hold releases");
    }

    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    #[test]
    fn commands_wake_a_service_sleeping_without_a_shortcut() {
        let (control, events) = channel();
        let sender = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(50));
            control.stop();
        });
        assert!(matches!(waker::wait(&events.rx, None), Ok(Message::Stop)));
        sender.join().unwrap();
    }

    #[test]
    fn hotkeys_reach_the_running_service_only() {
        let (_control, events) = channel();
        let event = |id| GlobalHotKeyEvent {
            id,
            state: HotKeyState::Pressed,
        };
        *HOTKEY_SINK.lock().unwrap() = Some(events.tx.clone());
        forward_hotkey(event(7));
        assert!(matches!(
            events.rx.try_recv(),
            Ok(Message::Hotkey {
                id: 7,
                pressed: true
            })
        ));
        *HOTKEY_SINK.lock().unwrap() = None;
        forward_hotkey(event(8));
        assert!(events.rx.try_recv().is_err());
    }
}
