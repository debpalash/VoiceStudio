//! Session-bound transcript delivery across desktop platforms.
//!
//! The public surface deliberately talks in sessions and outcomes. Focus
//! discovery, clipboard ownership, and input synthesis stay private so a late
//! transcript can never accidentally target a newer dictation session.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use arboard::{Clipboard, ImageData};
use enigo::{Direction, Enigo, Key, Keyboard, Settings as EnigoSettings};
use serde::Serialize;

const TARGET_SETTLE_DELAY: Duration = Duration::from_millis(60);
const CLIPBOARD_CONSUME_DELAY: Duration = Duration::from_millis(300);
const START_EVENT_DEDUPE_WINDOW: Duration = Duration::from_millis(150);

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum DeliveryOutcome {
    Inserted,
    Copied,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CaptureOrigin {
    Shortcut,
    Tray,
}

#[derive(Clone, Default)]
pub struct DictationOutput {
    inner: Arc<Inner>,
}

#[derive(Default)]
struct Inner {
    owner_pid: Option<u32>,
    next_session_id: AtomicU64,
    operation: Mutex<()>,
    state: Mutex<OutputState>,
}

#[derive(Default)]
struct OutputState {
    active: Option<OutputSession>,
    pending: Option<OutputSession>,
    clipboard_generation: u64,
    tray_target: Option<(PlatformTarget, Instant)>,
}

struct OutputSession {
    id: u64,
    started_at: Instant,
    target: Option<PlatformTarget>,
    clipboard: Option<ClipboardLease>,
    clipboard_only: bool,
}

struct ClipboardLease {
    original: ClipboardSnapshot,
    generation: u64,
    staged: String,
    staged_at: Instant,
}

#[derive(Clone)]
enum ClipboardSnapshot {
    Text(String),
    Html {
        html: String,
        alt_text: Option<String>,
    },
    Files(Vec<PathBuf>),
    Image(ImageData<'static>),
    // Rich clipboard formats that arboard cannot round-trip must not be
    // replaced a second time with a guessed value.
    Unsupported,
}

impl DictationOutput {
    /// Native-helper hosts identify the UI process so tray capture excludes its windows.
    /// In-process callers retain the existing default (the current process).
    pub fn for_owner(owner_pid: u32) -> Self {
        Self {
            inner: Arc::new(Inner {
                owner_pid: Some(owner_pid),
                ..Inner::default()
            }),
        }
    }

    fn owner_pid(&self) -> u32 {
        self.inner.owner_pid.unwrap_or_else(std::process::id)
    }

    /// Remember focus on tray mouse-down, before the menu itself can become
    /// foreground. Tauri does not emit tray pointer events on Linux; X11 can
    /// still capture `_NET_ACTIVE_WINDOW` at the menu action, while Wayland
    /// tray starts intentionally fall back to the clipboard.
    pub fn prime_tray_target(&self) {
        self.prime_tray_target_with(capture_target);
    }

    fn prime_tray_target_with<F>(&self, capture: F)
    where
        F: FnOnce() -> Option<PlatformTarget>,
    {
        // Focus discovery must happen at callback entry. Clipboard delivery
        // can hold the operation lock while another application becomes
        // foreground, so acquiring it first would capture the wrong target.
        let captured_at = Instant::now();
        let target = capture().filter(|target| !target.belongs_to_process(self.owner_pid()));
        if let Ok(mut state) = self.inner.state.lock() {
            state.tray_target = target.map(|target| (target, captured_at));
        }
    }

    /// Capture the destination before the capture event can show the pill.
    pub fn begin_session(&self, origin: CaptureOrigin) -> u64 {
        self.begin_session_with(origin, capture_target, tray_action_capture_supported())
    }

    fn begin_session_with<F>(
        &self,
        origin: CaptureOrigin,
        capture: F,
        capture_tray_on_action: bool,
    ) -> u64
    where
        F: FnOnce() -> Option<PlatformTarget>,
    {
        // Capture before waiting for another delivery operation. This is the
        // shortcut-down target, even when a clipboard/helper call is busy.
        let started_at = Instant::now();
        let captured_target = (origin == CaptureOrigin::Shortcut || capture_tray_on_action)
            .then(capture)
            .flatten()
            .filter(|target| {
                origin == CaptureOrigin::Shortcut || !target.belongs_to_process(self.owner_pid())
            });
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        // Collapse only duplicate delivery of the same physical press. A
        // later press becomes a candidate until the frontend accepts it, so
        // a press ignored during transcription cannot invalidate that result.
        if let Some(pending) = state.pending.as_ref() {
            if start_events_duplicate(pending.started_at, started_at) {
                return pending.id;
            }
        }
        state.pending = None;
        if let Some(active) = state.active.as_ref() {
            if start_events_duplicate(active.started_at, started_at) {
                return active.id;
            }
        }
        let primed_tray_target = state
            .tray_target
            .take()
            .filter(|(_, captured_at)| captured_at.elapsed() <= Duration::from_secs(10))
            .map(|(target, _)| target);
        let target = choose_session_target(origin, captured_target, primed_tray_target);
        let id = self
            .inner
            .next_session_id
            .fetch_add(1, Ordering::Relaxed)
            .wrapping_add(1);
        let clipboard_only = should_start_clipboard_only(
            origin,
            target.is_some(),
            untargeted_wayland_insert_enabled(),
        );
        let session = OutputSession {
            id,
            started_at,
            clipboard_only,
            target,
            clipboard: None,
        };
        if state.active.is_some() {
            state.pending = Some(session);
        } else {
            state.active = Some(session);
        }
        id
    }

    pub fn current_session_id(&self) -> Option<u64> {
        self.inner.state.lock().ok().and_then(|state| {
            state
                .pending
                .as_ref()
                .or(state.active.as_ref())
                .map(|session| session.id)
        })
    }

    /// Promote a captured candidate only after the frontend accepts its start
    /// event. This keeps an older transcribing session valid when a new press
    /// is ignored, and makes late teardown of that older ID harmless.
    pub fn activate_session(&self, session_id: u64) -> Result<(), String> {
        let _operation = self
            .inner
            .operation
            .lock()
            .map_err(|_| kind_err("paste", "output operation lock poisoned"))?;
        let replaced_lease = {
            let mut state = self
                .inner
                .state
                .lock()
                .map_err(|_| kind_err("paste", "output state lock poisoned"))?;
            if state.active.as_ref().map(|session| session.id) == Some(session_id) {
                return Ok(());
            }
            if state.pending.as_ref().map(|session| session.id) != Some(session_id) {
                return Err(kind_err("paste", "stale dictation session candidate"));
            }
            let candidate = state.pending.take().expect("candidate validated above");
            let replaced = state.active.replace(candidate);
            replaced.and_then(|session| session.clipboard)
        };
        if let Some(lease) = replaced_lease {
            self.schedule_finished_restore(lease);
        }
        Ok(())
    }

    /// Drop an unaccepted candidate without touching the active session. A
    /// key repeat can legitimately carry the active ID, so rejection must be
    /// a no-op for anything other than the matching pending candidate.
    pub fn reject_session_candidate(&self, session_id: u64) {
        let _operation = self
            .inner
            .operation
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        let Ok(mut state) = self.inner.state.lock() else {
            return;
        };
        if state.pending.as_ref().map(|session| session.id) == Some(session_id) {
            state.pending = None;
        }
    }

    pub fn deliver(&self, session_id: u64, text: &str) -> Result<DeliveryOutcome, String> {
        let _operation = self
            .inner
            .operation
            .lock()
            .map_err(|_| kind_err("paste", "output operation lock poisoned"))?;
        self.require_session(session_id)?;
        if text.is_empty() {
            return Ok(DeliveryOutcome::Inserted);
        }

        if self.session_is_clipboard_only(session_id)? {
            self.copy_only(session_id, text)?;
            return Ok(DeliveryOutcome::Copied);
        }

        #[cfg(target_os = "linux")]
        if is_wayland() {
            return self.deliver_wayland(session_id, text);
        }

        let target = self.session_target(session_id)?;
        let Some(target) = target else {
            self.copy_only(session_id, text)?;
            return Ok(DeliveryOutcome::Copied);
        };
        if !activate_target(&target) {
            self.copy_only(session_id, text)?;
            return Ok(DeliveryOutcome::Copied);
        }

        let generation = self.stage_clipboard(session_id, text)?;
        if !target_is_active(&target) {
            self.latch_staged_copy_only(session_id, generation, text)?;
            return Ok(DeliveryOutcome::Copied);
        }
        if synthesize_paste().is_err() {
            // The key sequence may have reached the application. Keep the
            // transcript staged, latch clipboard-only, and never attempt a
            // second insertion.
            self.latch_staged_copy_only(session_id, generation, text)?;
            return Ok(DeliveryOutcome::Copied);
        }
        self.schedule_restore(session_id, generation, text.to_owned());
        // SendInput/CGEventPost/XTest enqueue keystrokes; success does not mean
        // the target has consumed its paste. Keep the operation lock through
        // the same consumption window used by clipboard restoration, so a
        // second utterance cannot replace the clipboard or reset modifier state
        // (Windows AttachThreadInput) while the first Ctrl/Cmd+V is queued.
        thread::sleep(CLIPBOARD_CONSUME_DELAY);
        Ok(DeliveryOutcome::Inserted)
    }

    /// Keep the complete transcript on the clipboard without attempting input
    /// synthesis. Used when a platform permission gate fails before emission.
    pub fn copy_for_session(&self, session_id: u64, text: &str) -> Result<DeliveryOutcome, String> {
        let _operation = self
            .inner
            .operation
            .lock()
            .map_err(|_| kind_err("clipboard", "output operation lock poisoned"))?;
        self.require_session(session_id)?;
        self.copy_only(session_id, text)?;
        Ok(DeliveryOutcome::Copied)
    }

    pub fn type_delta(
        &self,
        session_id: u64,
        text: &str,
        backspaces: u32,
    ) -> Result<DeliveryOutcome, String> {
        let _operation = self
            .inner
            .operation
            .lock()
            .map_err(|_| kind_err("paste", "output operation lock poisoned"))?;
        self.require_session(session_id)
            .map_err(|_| kind_err("preflight", "stale dictation session before live typing"))?;
        if self.session_is_clipboard_only(session_id)? {
            return Err(kind_err("preflight", "dictation session is clipboard-only"));
        }

        #[cfg(target_os = "linux")]
        if is_wayland() {
            return type_delta_wayland(text, backspaces);
        }

        let target = self.session_target(session_id)?;
        let Some(target) = target else {
            return Err(kind_err("preflight", "the original target is unavailable"));
        };
        if !activate_target(&target) {
            return Err(kind_err(
                "preflight",
                "the original target could not be activated",
            ));
        }
        if !target_is_active(&target) {
            return Err(kind_err(
                "preflight",
                "the original target lost focus before live typing",
            ));
        }
        synthesize_text(text, backspaces)?;
        Ok(DeliveryOutcome::Inserted)
    }

    pub fn finish_session(&self, session_id: u64) {
        let _operation = self
            .inner
            .operation
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        let finished = {
            let Ok(mut state) = self.inner.state.lock() else {
                return;
            };
            if state.pending.as_ref().map(|session| session.id) == Some(session_id) {
                state.pending.take()
            } else if state.active.as_ref().map(|session| session.id) == Some(session_id) {
                state.active.take()
            } else {
                None
            }
        };
        if let Some(lease) = finished.and_then(|session| session.clipboard) {
            self.schedule_finished_restore(lease);
        }
    }

    fn require_session(&self, session_id: u64) -> Result<(), String> {
        let state = self
            .inner
            .state
            .lock()
            .map_err(|_| kind_err("paste", "output state lock poisoned"))?;
        if state.active.as_ref().map(|session| session.id) == Some(session_id) {
            Ok(())
        } else {
            Err(kind_err("paste", "stale dictation session"))
        }
    }

    fn session_target(&self, session_id: u64) -> Result<Option<PlatformTarget>, String> {
        let state = self
            .inner
            .state
            .lock()
            .map_err(|_| kind_err("paste", "output state lock poisoned"))?;
        let session = state
            .active
            .as_ref()
            .filter(|session| session.id == session_id)
            .ok_or_else(|| kind_err("paste", "stale dictation session"))?;
        Ok(session.target.clone())
    }

    fn session_is_clipboard_only(&self, session_id: u64) -> Result<bool, String> {
        let state = self
            .inner
            .state
            .lock()
            .map_err(|_| kind_err("paste", "output state lock poisoned"))?;
        state
            .active
            .as_ref()
            .filter(|session| session.id == session_id)
            .map(|session| session.clipboard_only)
            .ok_or_else(|| kind_err("paste", "stale dictation session"))
    }

    fn stage_clipboard(&self, session_id: u64, text: &str) -> Result<u64, String> {
        let mut clipboard = open_clipboard()?;
        let mut state = self
            .inner
            .state
            .lock()
            .map_err(|_| kind_err("clipboard", "output state lock poisoned"))?;
        // Overlapping streaming segments share one original snapshot. If the
        // user copied something after our prior stage, that becomes the new
        // protected value instead of being overwritten by a stale restore.
        let current = clipboard.get_text().ok();
        let keep_original = state
            .active
            .as_ref()
            .filter(|session| session.id == session_id)
            .ok_or_else(|| kind_err("paste", "stale dictation session"))?
            .clipboard
            .as_ref()
            .is_some_and(|lease| clipboard_still_staged(&lease.staged, current.as_deref()));
        let replacement = (!keep_original).then(|| snapshot_clipboard(&mut clipboard));

        set_clipboard_text(&mut clipboard, text)?;
        state.clipboard_generation = state.clipboard_generation.wrapping_add(1);
        let generation = state.clipboard_generation;
        let session = state
            .active
            .as_mut()
            .filter(|session| session.id == session_id)
            .ok_or_else(|| kind_err("paste", "stale dictation session"))?;
        if let Some(original) = replacement {
            session.clipboard = Some(ClipboardLease {
                original,
                generation,
                staged: String::new(),
                staged_at: Instant::now(),
            });
        }
        let lease = session.clipboard.as_mut().expect("clipboard lease created");
        lease.generation = generation;
        lease.staged.clear();
        lease.staged.push_str(text);
        lease.staged_at = Instant::now();
        Ok(generation)
    }

    fn copy_only(&self, session_id: u64, text: &str) -> Result<(), String> {
        let mut state = self
            .inner
            .state
            .lock()
            .map_err(|_| kind_err("clipboard", "output state lock poisoned"))?;
        state
            .active
            .as_ref()
            .filter(|session| session.id == session_id)
            .ok_or_else(|| kind_err("paste", "stale dictation session"))?;
        let mut clipboard = open_clipboard()?;
        set_clipboard_text(&mut clipboard, text)?;
        state.clipboard_generation = state.clipboard_generation.wrapping_add(1);
        let session = state.active.as_mut().expect("session validated");
        session.clipboard = None;
        session.clipboard_only = true;
        Ok(())
    }

    fn latch_staged_copy_only(
        &self,
        session_id: u64,
        generation: u64,
        staged: &str,
    ) -> Result<(), String> {
        let mut state = self
            .inner
            .state
            .lock()
            .map_err(|_| kind_err("clipboard", "output state lock poisoned"))?;
        let is_current_stage = state.clipboard_generation == generation
            && state
                .active
                .as_ref()
                .filter(|session| session.id == session_id)
                .and_then(|session| session.clipboard.as_ref())
                .is_some_and(|lease| lease.generation == generation && lease.staged == staged);
        if !is_current_stage {
            return Err(kind_err("paste", "stale staged clipboard"));
        }

        // The transcript is already on the clipboard. Invalidate every
        // scheduled restore without reopening a clipboard that may be
        // transiently unavailable, then keep this session copy-only.
        state.clipboard_generation = state.clipboard_generation.wrapping_add(1);
        let session = state.active.as_mut().expect("session validated above");
        session.clipboard = None;
        session.clipboard_only = true;
        Ok(())
    }

    fn schedule_restore(&self, session_id: u64, generation: u64, staged: String) {
        let output = self.clone();
        thread::spawn(move || {
            thread::sleep(CLIPBOARD_CONSUME_DELAY);
            output.restore_if_current(session_id, generation, &staged);
        });
    }

    fn restore_if_current(&self, session_id: u64, generation: u64, staged: &str) {
        let _operation = self
            .inner
            .operation
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        let mut state = match self.inner.state.lock() {
            Ok(state) => state,
            Err(_) => return,
        };
        if state.clipboard_generation != generation {
            return;
        }
        let Some(session) = state
            .active
            .as_mut()
            .filter(|session| session.id == session_id)
        else {
            return;
        };
        let Some(lease) = session.clipboard.as_ref() else {
            return;
        };
        if lease.generation != generation || lease.staged != staged {
            return;
        }
        let original = lease.original.clone();
        let Ok(mut clipboard) = open_clipboard() else {
            return;
        };
        let current = clipboard.get_text().ok();
        if !clipboard_still_staged(staged, current.as_deref()) {
            session.clipboard = None;
            return;
        }
        if restore_clipboard(&mut clipboard, &original).is_ok() {
            session.clipboard = None;
        }
    }

    fn schedule_finished_restore(&self, lease: ClipboardLease) {
        let output = self.clone();
        let delay = CLIPBOARD_CONSUME_DELAY.saturating_sub(lease.staged_at.elapsed());
        thread::spawn(move || {
            thread::sleep(delay);
            output.restore_finished_lease(lease);
        });
    }

    fn restore_finished_lease(&self, lease: ClipboardLease) {
        let _operation = self
            .inner
            .operation
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        let Ok(state) = self.inner.state.lock() else {
            return;
        };
        if state.clipboard_generation != lease.generation {
            return;
        }
        let Ok(mut clipboard) = open_clipboard() else {
            return;
        };
        let current = clipboard.get_text().ok();
        if clipboard_still_staged(&lease.staged, current.as_deref()) {
            let _ = restore_clipboard(&mut clipboard, &lease.original);
        }
    }
}

fn open_clipboard() -> Result<Clipboard, String> {
    let attempts = if cfg!(target_os = "windows") { 4 } else { 1 };
    let mut last_error = None;
    for attempt in 0..attempts {
        match Clipboard::new() {
            Ok(clipboard) => return Ok(clipboard),
            Err(error) => last_error = Some(error),
        }
        if attempt + 1 < attempts {
            thread::sleep(Duration::from_millis(25));
        }
    }
    Err(kind_err(
        "clipboard",
        format!("init failed: {}", last_error.expect("at least one attempt")),
    ))
}

fn set_clipboard_text(clipboard: &mut Clipboard, text: &str) -> Result<(), String> {
    let attempts = if cfg!(target_os = "windows") { 4 } else { 1 };
    let mut last_error = None;
    for attempt in 0..attempts {
        match clipboard.set_text(text.to_owned()) {
            Ok(()) => return Ok(()),
            Err(error) => last_error = Some(error),
        }
        if attempt + 1 < attempts {
            thread::sleep(Duration::from_millis(25));
        }
    }
    Err(kind_err(
        "clipboard",
        format!(
            "write failed: {}",
            last_error.expect("at least one attempt")
        ),
    ))
}

fn snapshot_clipboard(clipboard: &mut Clipboard) -> ClipboardSnapshot {
    // Rich formats commonly expose a plain-text fallback. Probe them first so
    // restoring the clipboard does not silently flatten HTML, images, or file
    // selections into that fallback.
    let files = clipboard.get().file_list().ok();
    let html = clipboard.get().html().ok();
    let image = clipboard.get_image().ok();
    let text = clipboard.get_text().ok();
    snapshot_from_formats(files, html, image, text)
}

fn snapshot_from_formats(
    files: Option<Vec<PathBuf>>,
    html: Option<String>,
    image: Option<ImageData<'static>>,
    text: Option<String>,
) -> ClipboardSnapshot {
    if let Some(files) = files.filter(|paths| !paths.is_empty()) {
        ClipboardSnapshot::Files(files)
    } else if let Some(html) = html {
        ClipboardSnapshot::Html {
            html,
            alt_text: text,
        }
    } else if let Some(image) = image {
        ClipboardSnapshot::Image(image)
    } else if let Some(text) = text {
        ClipboardSnapshot::Text(text)
    } else {
        ClipboardSnapshot::Unsupported
    }
}

fn restore_clipboard(
    clipboard: &mut Clipboard,
    snapshot: &ClipboardSnapshot,
) -> Result<(), String> {
    match snapshot {
        ClipboardSnapshot::Text(text) => set_clipboard_text(clipboard, text),
        ClipboardSnapshot::Html { html, alt_text } => clipboard
            .set()
            .html(html.clone(), alt_text.clone())
            .map_err(|error| kind_err("clipboard", format!("HTML restore failed: {error}"))),
        ClipboardSnapshot::Files(paths) => clipboard
            .set()
            .file_list(paths)
            .map_err(|error| kind_err("clipboard", format!("file-list restore failed: {error}"))),
        ClipboardSnapshot::Image(image) => clipboard
            .set_image(image.clone())
            .map_err(|error| kind_err("clipboard", format!("image restore failed: {error}"))),
        ClipboardSnapshot::Unsupported => Ok(()),
    }
}

fn clipboard_still_staged(staged: &str, current: Option<&str>) -> bool {
    current == Some(staged)
}

fn choose_session_target<T>(
    origin: CaptureOrigin,
    captured_at_action: Option<T>,
    primed_tray_target: Option<T>,
) -> Option<T> {
    match origin {
        CaptureOrigin::Shortcut => captured_at_action,
        CaptureOrigin::Tray => primed_tray_target.or(captured_at_action),
    }
}

fn start_events_duplicate(existing: Instant, incoming: Instant) -> bool {
    let distance = if incoming >= existing {
        incoming.duration_since(existing)
    } else {
        existing.duration_since(incoming)
    };
    distance <= START_EVENT_DEDUPE_WINDOW
}

#[cfg(target_os = "linux")]
fn tray_action_capture_supported() -> bool {
    !is_wayland()
}

#[cfg(not(target_os = "linux"))]
fn tray_action_capture_supported() -> bool {
    false
}

fn should_start_clipboard_only(
    origin: CaptureOrigin,
    target_available: bool,
    untargeted_wayland_opt_in: bool,
) -> bool {
    !target_available && !(origin == CaptureOrigin::Shortcut && untargeted_wayland_opt_in)
}

#[cfg(target_os = "linux")]
fn untargeted_wayland_insert_enabled() -> bool {
    is_wayland()
        && std::env::var("VOICESTUDIO_WAYLAND_UNTARGETED_INSERT").is_ok_and(|value| {
            matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes"
            )
        })
}

#[cfg(not(target_os = "linux"))]
fn untargeted_wayland_insert_enabled() -> bool {
    false
}

fn synthesize_paste() -> Result<(), String> {
    let mut enigo = Enigo::new(&EnigoSettings::default())
        .map_err(|error| kind_err("paste", format!("keyboard init failed: {error}")))?;
    #[cfg(target_os = "macos")]
    let modifier = Key::Meta;
    #[cfg(not(target_os = "macos"))]
    let modifier = Key::Control;

    enigo
        .key(modifier, Direction::Press)
        .map_err(|error| kind_err("paste", format!("modifier press failed: {error}")))?;
    let click = enigo.key(Key::Unicode('v'), Direction::Click);
    let release = enigo.key(modifier, Direction::Release);
    if let Err(error) = click {
        return Err(kind_err("paste", format!("paste key failed: {error}")));
    }
    release.map_err(|error| kind_err("paste", format!("modifier release failed: {error}")))
}

fn synthesize_text(text: &str, backspaces: u32) -> Result<(), String> {
    let mut enigo = Enigo::new(&EnigoSettings::default())
        .map_err(|error| kind_err("paste", format!("keyboard init failed: {error}")))?;
    for _ in 0..backspaces {
        enigo
            .key(Key::Backspace, Direction::Click)
            .map_err(|error| kind_err("paste", format!("backspace failed: {error}")))?;
    }
    if !text.is_empty() {
        enigo
            .text(text)
            .map_err(|error| kind_err("paste", format!("type failed: {error}")))?;
    }
    Ok(())
}

fn kind_err(kind: &str, detail: impl std::fmt::Display) -> String {
    format!("{kind}:{detail}")
}

// ── Destination capture and reactivation ────────────────────────────────

#[cfg(target_os = "macos")]
#[derive(Clone)]
struct PlatformTarget {
    app: objc2::rc::Retained<objc2_app_kit::NSRunningApplication>,
    pid: i32,
}

#[cfg(target_os = "macos")]
impl PlatformTarget {
    fn belongs_to_process(&self, owner_pid: u32) -> bool {
        self.pid == owner_pid as i32
    }
}

#[cfg(target_os = "macos")]
fn capture_target() -> Option<PlatformTarget> {
    let app = objc2_app_kit::NSWorkspace::sharedWorkspace().frontmostApplication()?;
    let pid = app.processIdentifier();
    Some(PlatformTarget { app, pid })
}

#[cfg(target_os = "macos")]
fn activate_target(target: &PlatformTarget) -> bool {
    use objc2_app_kit::NSApplicationActivationOptions;

    if target.app.processIdentifier() != target.pid
        || !target
            .app
            .activateWithOptions(NSApplicationActivationOptions::empty())
    {
        return false;
    }
    thread::sleep(TARGET_SETTLE_DELAY);
    target.app.isActive() && target.app.processIdentifier() == target.pid
}

#[cfg(target_os = "macos")]
fn target_is_active(target: &PlatformTarget) -> bool {
    target.app.processIdentifier() == target.pid && target.app.isActive()
}

#[cfg(target_os = "windows")]
#[derive(Clone)]
struct PlatformTarget {
    hwnd: isize,
    pid: u32,
}

#[cfg(target_os = "windows")]
impl PlatformTarget {
    fn belongs_to_process(&self, owner_pid: u32) -> bool {
        self.pid == owner_pid
    }
}

#[cfg(target_os = "windows")]
fn capture_target() -> Option<PlatformTarget> {
    use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

    unsafe {
        let hwnd = GetForegroundWindow();
        if hwnd.0.is_null() {
            return None;
        }
        let mut pid = 0;
        GetWindowThreadProcessId(hwnd, Some(&mut pid));
        if pid == 0 {
            return None;
        }
        Some(PlatformTarget {
            hwnd: hwnd.0 as isize,
            pid,
        })
    }
}

#[cfg(target_os = "windows")]
fn activate_target(target: &PlatformTarget) -> bool {
    use windows::Win32::Foundation::HWND;
    use windows::Win32::System::Threading::{AttachThreadInput, GetCurrentThreadId};
    use windows::Win32::UI::WindowsAndMessaging::{
        BringWindowToTop, GetForegroundWindow, GetWindowThreadProcessId, IsWindow,
        SetForegroundWindow,
    };

    unsafe {
        let hwnd = HWND(target.hwnd as *mut _);
        if !IsWindow(Some(hwnd)).as_bool() {
            return false;
        }
        let mut pid = 0;
        let target_thread = GetWindowThreadProcessId(hwnd, Some(&mut pid));
        if pid != target.pid || target_thread == 0 {
            return false;
        }
        let current_thread = GetCurrentThreadId();
        let foreground = GetForegroundWindow();
        let foreground_thread = if foreground.0.is_null() {
            0
        } else {
            GetWindowThreadProcessId(foreground, None)
        };
        // Windows grants foreground activation through the thread that owns the
        // current foreground window. The recorder can become foreground when its
        // Stop button is clicked, so attaching to the destination thread does not
        // transfer that right and SetForegroundWindow can silently fail.
        let attached = foreground_thread != 0
            && foreground_thread != current_thread
            && AttachThreadInput(current_thread, foreground_thread, true).as_bool();
        let _ = BringWindowToTop(hwnd);
        let requested = SetForegroundWindow(hwnd).as_bool();
        if attached {
            let _ = AttachThreadInput(current_thread, foreground_thread, false);
        }
        if !requested {
            return false;
        }
        thread::sleep(TARGET_SETTLE_DELAY);
        GetForegroundWindow() == hwnd
    }
}

#[cfg(target_os = "windows")]
fn target_is_active(target: &PlatformTarget) -> bool {
    use windows::Win32::Foundation::HWND;
    use windows::Win32::UI::WindowsAndMessaging::{
        GetForegroundWindow, GetWindowThreadProcessId, IsWindow,
    };

    unsafe {
        let hwnd = HWND(target.hwnd as *mut _);
        if !IsWindow(Some(hwnd)).as_bool() || GetForegroundWindow() != hwnd {
            return false;
        }
        let mut pid = 0;
        GetWindowThreadProcessId(hwnd, Some(&mut pid));
        pid == target.pid
    }
}

#[cfg(target_os = "linux")]
#[derive(Clone)]
struct PlatformTarget {
    window: u32,
    pid: Option<u32>,
}

#[cfg(target_os = "linux")]
impl PlatformTarget {
    fn belongs_to_process(&self, owner_pid: u32) -> bool {
        self.pid == Some(owner_pid)
    }
}

#[cfg(target_os = "linux")]
fn capture_target() -> Option<PlatformTarget> {
    use x11rb::connection::Connection;

    if is_wayland() {
        return None;
    }
    let (connection, screen_num) = x11rb::connect(None).ok()?;
    let root = connection.setup().roots.get(screen_num)?.root;
    let window = x11_active_window(&connection, root)?;
    // A window id alone can be reused after its client exits. Without the
    // standard EWMH PID we cannot prove this is still the shortcut-down
    // target at delivery time, so degrade to clipboard-only instead.
    let pid = x11_window_pid(&connection, window)?;
    Some(PlatformTarget {
        window,
        pid: Some(pid),
    })
}

#[cfg(target_os = "linux")]
fn activate_target(target: &PlatformTarget) -> bool {
    use x11rb::connection::Connection;
    use x11rb::protocol::xproto::{
        ClientMessageData, ClientMessageEvent, ConnectionExt, EventMask,
    };

    let Ok((connection, screen_num)) = x11rb::connect(None) else {
        return false;
    };
    let Some(root) = connection
        .setup()
        .roots
        .get(screen_num)
        .map(|screen| screen.root)
    else {
        return false;
    };
    let Ok(attributes) = connection.get_window_attributes(target.window) else {
        return false;
    };
    if attributes.reply().is_err() {
        return false;
    }
    if target.pid.is_some() && x11_window_pid(&connection, target.window) != target.pid {
        return false;
    }
    let Ok(cookie) = connection.intern_atom(false, b"_NET_ACTIVE_WINDOW") else {
        return false;
    };
    let Ok(reply) = cookie.reply() else {
        return false;
    };
    let event = ClientMessageEvent::new(
        32,
        target.window,
        reply.atom,
        ClientMessageData::from([1, 0, 0, 0, 0]),
    );
    if connection
        .send_event(
            false,
            root,
            EventMask::SUBSTRUCTURE_REDIRECT | EventMask::SUBSTRUCTURE_NOTIFY,
            event,
        )
        .is_err()
        || connection.flush().is_err()
    {
        return false;
    }
    thread::sleep(TARGET_SETTLE_DELAY);
    x11_active_window(&connection, root) == Some(target.window)
}

#[cfg(target_os = "linux")]
fn target_is_active(target: &PlatformTarget) -> bool {
    use x11rb::connection::Connection;

    let Ok((connection, screen_num)) = x11rb::connect(None) else {
        return false;
    };
    let Some(root) = connection
        .setup()
        .roots
        .get(screen_num)
        .map(|screen| screen.root)
    else {
        return false;
    };
    x11_active_window(&connection, root) == Some(target.window)
        && (target.pid.is_none() || x11_window_pid(&connection, target.window) == target.pid)
}

#[cfg(target_os = "linux")]
fn x11_active_window<C: x11rb::connection::Connection>(connection: &C, root: u32) -> Option<u32> {
    use x11rb::protocol::xproto::{AtomEnum, ConnectionExt};

    let atom = connection
        .intern_atom(false, b"_NET_ACTIVE_WINDOW")
        .ok()?
        .reply()
        .ok()?
        .atom;
    connection
        .get_property(false, root, atom, AtomEnum::WINDOW, 0, 1)
        .ok()?
        .reply()
        .ok()?
        .value32()?
        .next()
}

#[cfg(target_os = "linux")]
fn x11_window_pid<C: x11rb::connection::Connection>(connection: &C, window: u32) -> Option<u32> {
    use x11rb::protocol::xproto::{AtomEnum, ConnectionExt};

    let atom = connection
        .intern_atom(false, b"_NET_WM_PID")
        .ok()?
        .reply()
        .ok()?
        .atom;
    connection
        .get_property(false, window, atom, AtomEnum::CARDINAL, 0, 1)
        .ok()?
        .reply()
        .ok()?
        .value32()?
        .next()
}

#[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
#[derive(Clone)]
struct PlatformTarget;

#[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
impl PlatformTarget {
    fn belongs_to_process(&self, _owner_pid: u32) -> bool {
        false
    }
}

#[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
fn capture_target() -> Option<PlatformTarget> {
    None
}

#[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
fn activate_target(_: &PlatformTarget) -> bool {
    false
}

#[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
fn target_is_active(_: &PlatformTarget) -> bool {
    false
}

// ── Wayland insertion adapters ──────────────────────────────────────────

#[cfg(any(target_os = "linux", test))]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LinuxTool {
    Wtype,
    Dotool,
    Ydotool,
}

#[cfg(any(target_os = "linux", test))]
fn linux_tool_plan(wayland: bool, desktop: &str) -> Vec<LinuxTool> {
    if !wayland {
        return Vec::new();
    }
    let tokens: Vec<String> = desktop
        .split([':', ';', ',', ' '])
        .filter(|token| !token.is_empty())
        .map(str::to_ascii_lowercase)
        .collect();
    let kde = tokens
        .iter()
        .any(|token| token.contains("kde") || token.contains("plasma"));
    let gnome = tokens.iter().any(|token| token.contains("gnome"));
    if kde {
        vec![LinuxTool::Dotool, LinuxTool::Ydotool]
    } else if gnome {
        vec![LinuxTool::Dotool, LinuxTool::Ydotool]
    } else {
        vec![LinuxTool::Wtype, LinuxTool::Dotool, LinuxTool::Ydotool]
    }
}

#[cfg(any(target_os = "linux", test))]
fn wayland_live_type_tool(desktop: &str) -> Option<LinuxTool> {
    linux_tool_plan(true, desktop)
        .into_iter()
        // Live revisions need both arbitrary Unicode and backspace support.
        .find(|tool| *tool == LinuxTool::Wtype)
}

#[cfg(target_os = "linux")]
fn is_wayland() -> bool {
    std::env::var("XDG_SESSION_TYPE").is_ok_and(|value| value.eq_ignore_ascii_case("wayland"))
        || std::env::var_os("WAYLAND_DISPLAY").is_some()
}

#[cfg(target_os = "linux")]
impl DictationOutput {
    fn deliver_wayland(&self, session_id: u64, text: &str) -> Result<DeliveryOutcome, String> {
        let desktop = std::env::var("XDG_CURRENT_DESKTOP").unwrap_or_default();
        for tool in linux_tool_plan(true, &desktop) {
            if !linux_helper_ready(tool) {
                continue;
            }
            match tool {
                // This accepts arbitrary Unicode. Only one started helper
                // is ever tried, because a failing process may have inserted a
                // prefix and retrying would duplicate it.
                LinuxTool::Wtype => {
                    if run_linux_helper("wtype", &["-"], Some(text.as_bytes())).is_err() {
                        self.copy_only(session_id, text)?;
                        return Ok(DeliveryOutcome::Copied);
                    }
                    return Ok(DeliveryOutcome::Inserted);
                }
                // dotool/ydotool text modes are layout-limited. Paste a
                // clipboard payload with fixed key commands instead.
                LinuxTool::Dotool => {
                    let generation = self.stage_clipboard(session_id, text)?;
                    if run_linux_helper("dotool", &[], Some(b"key ctrl+v\n")).is_err() {
                        self.latch_staged_copy_only(session_id, generation, text)?;
                        return Ok(DeliveryOutcome::Copied);
                    }
                    self.schedule_restore(session_id, generation, text.to_owned());
                    return Ok(DeliveryOutcome::Inserted);
                }
                LinuxTool::Ydotool => {
                    let generation = self.stage_clipboard(session_id, text)?;
                    if run_linux_helper("ydotool", &["key", "29:1", "47:1", "47:0", "29:0"], None)
                        .is_err()
                    {
                        self.latch_staged_copy_only(session_id, generation, text)?;
                        return Ok(DeliveryOutcome::Copied);
                    }
                    self.schedule_restore(session_id, generation, text.to_owned());
                    return Ok(DeliveryOutcome::Inserted);
                }
            }
        }
        self.copy_only(session_id, text)?;
        Ok(DeliveryOutcome::Copied)
    }
}

#[cfg(target_os = "linux")]
impl LinuxTool {
    fn command(self) -> &'static str {
        match self {
            Self::Wtype => "wtype",
            Self::Dotool => "dotool",
            Self::Ydotool => "ydotool",
        }
    }
}

#[cfg(any(target_os = "linux", test))]
fn linux_helper_ready_with<E, P, U>(
    tool: LinuxTool,
    exists: E,
    probe: P,
    uinput_writable: U,
) -> bool
where
    E: FnOnce(&str) -> bool,
    P: FnOnce(&str, &[&str]) -> bool,
    U: FnOnce() -> bool,
{
    let command = match tool {
        LinuxTool::Wtype => "wtype",
        LinuxTool::Dotool => "dotool",
        LinuxTool::Ydotool => "ydotool",
    };
    if !exists(command) {
        return false;
    }
    match tool {
        // dotool opens uinput itself. Opening and immediately closing the
        // device verifies permissions without creating a virtual keyboard or
        // emitting input.
        LinuxTool::Dotool => uinput_writable(),
        // Since 1.0, ydotool is only a client for ydotoold. `debug` verifies
        // the socket and daemon without emitting input.
        LinuxTool::Ydotool => probe(command, &["debug"]),
        LinuxTool::Wtype => true,
    }
}

#[cfg(target_os = "linux")]
fn linux_helper_ready(tool: LinuxTool) -> bool {
    linux_helper_ready_with(
        tool,
        linux_helper_exists,
        |command, args| run_linux_helper(command, args, None).is_ok(),
        linux_uinput_writable,
    )
}

#[cfg(target_os = "linux")]
fn linux_uinput_writable() -> bool {
    ["/dev/uinput", "/dev/input/uinput"]
        .iter()
        .any(|path| std::fs::OpenOptions::new().write(true).open(path).is_ok())
}

#[cfg(target_os = "linux")]
fn type_delta_wayland(text: &str, backspaces: u32) -> Result<DeliveryOutcome, String> {
    let desktop = std::env::var("XDG_CURRENT_DESKTOP").unwrap_or_default();
    if wayland_live_type_tool(&desktop) == Some(LinuxTool::Wtype) && linux_helper_exists("wtype") {
        let mut owned = Vec::with_capacity(backspaces as usize * 4 + 1);
        for _ in 0..backspaces {
            owned.extend(["-P".to_owned(), "BackSpace".to_owned()]);
            owned.extend(["-p".to_owned(), "BackSpace".to_owned()]);
        }
        owned.push("-".to_owned());
        let args: Vec<&str> = owned.iter().map(String::as_str).collect();
        run_linux_helper("wtype", &args, Some(text.as_bytes()))?;
        return Ok(DeliveryOutcome::Inserted);
    }
    Err(kind_err(
        "preflight",
        "live typing is unavailable on this Wayland compositor",
    ))
}

#[cfg(target_os = "linux")]
fn linux_helper_exists(name: &str) -> bool {
    use std::os::unix::fs::PermissionsExt;

    std::env::var_os("PATH").is_some_and(|path| {
        std::env::split_paths(&path).any(|dir| {
            let path = dir.join(name);
            path.metadata().is_ok_and(|metadata| {
                metadata.is_file() && metadata.permissions().mode() & 0o111 != 0
            })
        })
    })
}

#[cfg(target_os = "linux")]
fn run_linux_helper(name: &str, args: &[&str], stdin: Option<&[u8]>) -> Result<(), String> {
    use std::io::{Read, Write};
    use std::process::{Command, Stdio};
    use std::time::Instant;

    let mut command = Command::new(name);
    command
        .args(args)
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .stdin(if stdin.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        });
    // AppImages bundle ABI-sensitive libraries. Host desktop helpers must use
    // the host loader while retaining DISPLAY/WAYLAND_DISPLAY and the runtime
    // directory from the user session.
    if std::env::var_os("APPIMAGE").is_some() || std::env::var_os("APPDIR").is_some() {
        command
            .env_remove("LD_LIBRARY_PATH")
            .env_remove("LD_PRELOAD");
    }
    let started = Instant::now();
    let mut child = command
        .spawn()
        .map_err(|error| kind_err("paste", format!("{name} could not start: {error}")))?;
    let writer = if let Some(bytes) = stdin {
        let Some(mut pipe) = child.stdin.take() else {
            let _ = child.kill();
            let _ = child.wait();
            return Err(kind_err("paste", format!("{name} stdin unavailable")));
        };
        let bytes = bytes.to_owned();
        Some(thread::spawn(move || pipe.write_all(&bytes)))
    } else {
        None
    };
    let stderr_drain = child.stderr.take().map(|mut stderr| {
        thread::spawn(move || {
            let mut buffer = [0_u8; 1024];
            while let Ok(read) = stderr.read(&mut buffer) {
                if read == 0 {
                    break;
                }
            }
        })
    });

    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) if started.elapsed() < Duration::from_secs(2) => {
                thread::sleep(Duration::from_millis(10));
            }
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                break Err(kind_err("paste", format!("{name} timed out")));
            }
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                break Err(kind_err("paste", format!("{name} wait failed: {error}")));
            }
        }
    };
    let write_result = writer.map(|writer| writer.join());
    if let Some(stderr_drain) = stderr_drain {
        let _ = stderr_drain.join();
    }
    let status = status?;
    if let Some(write_result) = write_result {
        write_result
            .map_err(|_| kind_err("paste", format!("{name} input worker failed")))?
            .map_err(|error| kind_err("paste", format!("{name} input failed: {error}")))?;
    }
    if status.success() {
        return Ok(());
    }
    Err(kind_err(
        "paste",
        format!("{name} exited with status {status}"),
    ))
}

#[cfg(test)]
mod tests {
    use super::{
        clipboard_still_staged, linux_helper_ready_with, linux_tool_plan,
        should_start_clipboard_only, wayland_live_type_tool, CaptureOrigin, ClipboardLease,
        ClipboardSnapshot, DeliveryOutcome, DictationOutput, LinuxTool, OutputSession,
        START_EVENT_DEDUPE_WINDOW,
    };
    use std::sync::mpsc;
    use std::thread;
    use std::time::{Duration, Instant};

    #[cfg(target_os = "windows")]
    #[test]
    fn helper_tray_capture_excludes_the_owner_window() {
        let owner = std::process::id().wrapping_add(1);
        let output = DictationOutput::for_owner(owner);
        let session = output.begin_session_with(
            CaptureOrigin::Tray,
            || {
                Some(super::PlatformTarget {
                    hwnd: 1,
                    pid: owner,
                })
            },
            true,
        );
        let state = output.inner.state.lock().unwrap();
        let active = state.active.as_ref().unwrap();
        assert_eq!(active.id, session);
        assert!(active.target.is_none());
        assert!(active.clipboard_only);
        assert_eq!(DictationOutput::default().owner_pid(), std::process::id());
    }

    #[test]
    fn clipboard_restore_never_overwrites_a_new_user_copy() {
        assert!(clipboard_still_staged(
            "dictated text",
            Some("dictated text")
        ));
        assert!(!clipboard_still_staged(
            "dictated text",
            Some("new user copy")
        ));
        assert!(!clipboard_still_staged("dictated text", None));
    }

    #[test]
    fn rich_clipboard_formats_win_over_plain_text_fallbacks() {
        let html = super::snapshot_from_formats(
            None,
            Some("<b>formatted</b>".to_owned()),
            None,
            Some("formatted".to_owned()),
        );
        assert!(matches!(
            html,
            ClipboardSnapshot::Html { alt_text: Some(ref text), .. } if text == "formatted"
        ));

        let files = super::snapshot_from_formats(
            Some(vec![std::path::PathBuf::from("/tmp/example.wav")]),
            None,
            None,
            Some("file:///tmp/example.wav".to_owned()),
        );
        assert!(matches!(files, ClipboardSnapshot::Files(ref paths) if paths.len() == 1));
    }

    #[test]
    fn post_stage_failure_keeps_the_transcript_without_another_clipboard_write() {
        let output = DictationOutput::default();
        {
            let mut state = output.inner.state.lock().unwrap();
            state.clipboard_generation = 11;
            state.active = Some(OutputSession {
                id: 7,
                started_at: Instant::now(),
                target: None,
                clipboard_only: false,
                clipboard: Some(ClipboardLease {
                    original: ClipboardSnapshot::Text("original".to_owned()),
                    generation: 11,
                    staged: "transcript".to_owned(),
                    staged_at: Instant::now(),
                }),
            });
        }

        output.latch_staged_copy_only(7, 11, "transcript").unwrap();

        let state = output.inner.state.lock().unwrap();
        let session = state.active.as_ref().unwrap();
        assert!(session.clipboard_only);
        assert!(session.clipboard.is_none());
        assert_eq!(state.clipboard_generation, 12);
    }

    #[test]
    fn clipboard_only_live_type_failure_is_known_to_precede_emission() {
        let output = DictationOutput::default();
        output.inner.state.lock().unwrap().active = Some(OutputSession {
            id: 9,
            started_at: Instant::now(),
            target: None,
            clipboard_only: true,
            clipboard: None,
        });

        let error = output.type_delta(9, "not emitted", 0).unwrap_err();

        assert!(error.starts_with("preflight:"), "{error}");
    }

    #[test]
    fn wayland_tool_plan_matches_compositor_capabilities() {
        assert_eq!(
            linux_tool_plan(true, "KDE"),
            vec![LinuxTool::Dotool, LinuxTool::Ydotool]
        );
        assert_eq!(
            linux_tool_plan(true, "ubuntu:GNOME"),
            vec![LinuxTool::Dotool, LinuxTool::Ydotool]
        );
        assert_eq!(
            linux_tool_plan(true, "sway"),
            vec![LinuxTool::Wtype, LinuxTool::Dotool, LinuxTool::Ydotool]
        );
        assert_eq!(
            linux_tool_plan(true, "plasma;KDE"),
            vec![LinuxTool::Dotool, LinuxTool::Ydotool]
        );
        assert!(linux_tool_plan(false, "KDE").is_empty());
    }

    #[test]
    fn wayland_live_typing_requires_a_revision_capable_backend() {
        assert_eq!(wayland_live_type_tool("KDE"), None);
        assert_eq!(wayland_live_type_tool("ubuntu:GNOME"), None);
        assert_eq!(wayland_live_type_tool("sway"), Some(LinuxTool::Wtype));
    }

    #[test]
    fn ydotool_requires_a_reachable_daemon() {
        assert!(!linux_helper_ready_with(
            LinuxTool::Ydotool,
            |command| command == "ydotool",
            |command, args| command == "ydotool" && args == ["debug"] && false,
            || true,
        ));
        assert!(linux_helper_ready_with(
            LinuxTool::Ydotool,
            |_| true,
            |command, args| command == "ydotool" && args == ["debug"],
            || true,
        ));
        assert!(linux_helper_ready_with(
            LinuxTool::Wtype,
            |_| true,
            |_, _| panic!("non-daemon helper should not be probed"),
            || panic!("compositor helper should not probe uinput"),
        ));
    }

    #[test]
    fn dotool_requires_writable_uinput_before_selection() {
        assert!(!linux_helper_ready_with(
            LinuxTool::Dotool,
            |_| true,
            |_, _| panic!("dotool should not run a command probe"),
            || false,
        ));
        assert!(linux_helper_ready_with(
            LinuxTool::Dotool,
            |_| true,
            |_, _| panic!("dotool should not run a command probe"),
            || true,
        ));
    }

    #[test]
    fn delivery_outcome_has_a_stable_wire_shape() {
        assert_eq!(
            serde_json::to_string(&DeliveryOutcome::Inserted).unwrap(),
            "\"inserted\""
        );
        assert_eq!(
            serde_json::to_string(&DeliveryOutcome::Copied).unwrap(),
            "\"copied\""
        );
    }

    #[test]
    fn missing_target_is_copy_only_unless_wayland_insertion_is_explicitly_enabled() {
        assert!(should_start_clipboard_only(
            CaptureOrigin::Shortcut,
            false,
            false
        ));
        assert!(!should_start_clipboard_only(
            CaptureOrigin::Shortcut,
            false,
            true
        ));
        assert!(should_start_clipboard_only(
            CaptureOrigin::Tray,
            false,
            true
        ));
        assert!(!should_start_clipboard_only(
            CaptureOrigin::Shortcut,
            true,
            false
        ));
    }

    #[test]
    fn accepted_restart_survives_late_finish_of_the_previous_session() {
        let output = DictationOutput::default();
        let first = output.begin_session(CaptureOrigin::Shortcut);
        let duplicate = output.begin_session(CaptureOrigin::Shortcut);
        assert_eq!(duplicate, first);

        output
            .inner
            .state
            .lock()
            .unwrap()
            .active
            .as_mut()
            .unwrap()
            .started_at = Instant::now() - START_EVENT_DEDUPE_WINDOW - Duration::from_millis(1);
        let restarted = output.begin_session(CaptureOrigin::Shortcut);
        assert_ne!(restarted, first);
        output.activate_session(restarted).unwrap();
        output.finish_session(first);
        assert_eq!(output.current_session_id(), Some(restarted));
    }

    #[test]
    fn rejected_restart_candidate_does_not_invalidate_the_active_session() {
        let output = DictationOutput::default();
        let active = output.begin_session(CaptureOrigin::Shortcut);
        output
            .inner
            .state
            .lock()
            .unwrap()
            .active
            .as_mut()
            .unwrap()
            .started_at = Instant::now() - START_EVENT_DEDUPE_WINDOW - Duration::from_millis(1);

        let rejected = output.begin_session(CaptureOrigin::Shortcut);
        output.reject_session_candidate(rejected);

        assert_eq!(output.current_session_id(), Some(active));
        assert!(output.require_session(active).is_ok());
        output.reject_session_candidate(active);
        assert!(output.require_session(active).is_ok());
    }

    #[test]
    fn busy_output_does_not_delay_capture_or_split_duplicate_start_events() {
        let output = DictationOutput::default();
        let operation = output.inner.operation.lock().unwrap();
        let (captured_tx, captured_rx) = mpsc::channel();
        let (finished_tx, finished_rx) = mpsc::channel();
        let workers: Vec<_> = (0..2)
            .map(|_| {
                let worker_output = output.clone();
                let captured_tx = captured_tx.clone();
                let finished_tx = finished_tx.clone();
                thread::spawn(move || {
                    let id = worker_output.begin_session_with(
                        CaptureOrigin::Shortcut,
                        || {
                            captured_tx.send(()).unwrap();
                            None
                        },
                        false,
                    );
                    finished_tx.send(id).unwrap();
                    id
                })
            })
            .collect();

        for _ in 0..2 {
            captured_rx
                .recv_timeout(Duration::from_millis(100))
                .expect("focus capture waited behind the output operation lock");
        }
        let ids: Vec<_> = (0..2)
            .map(|_| {
                finished_rx
                    .recv_timeout(Duration::from_millis(100))
                    .expect("session reservation waited behind the output operation lock")
            })
            .collect();
        assert_eq!(ids[0], ids[1]);
        drop(operation);

        for worker in workers {
            assert!(worker.join().is_ok());
        }
    }

    #[test]
    fn duplicate_start_uses_event_time_not_lock_admission_time() {
        let first = Instant::now() - START_EVENT_DEDUPE_WINDOW - Duration::from_millis(20);
        let duplicate = first + Duration::from_millis(5);
        assert!(super::start_events_duplicate(first, duplicate));
    }

    #[test]
    fn tray_target_prefers_pointer_prime_then_x11_action_capture() {
        assert_eq!(
            super::choose_session_target(CaptureOrigin::Tray, Some("action"), Some("primed")),
            Some("primed"),
        );
        assert_eq!(
            super::choose_session_target(CaptureOrigin::Tray, Some("x11-action"), None),
            Some("x11-action"),
        );
        assert_eq!(
            super::choose_session_target(CaptureOrigin::Tray, None::<&str>, None),
            None,
        );
    }
}
