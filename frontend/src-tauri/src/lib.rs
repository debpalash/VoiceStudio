//! VoiceStudio — Tauri desktop shell.
//!
//! Module layout:
//!   config    – persistent app config, region helpers
//!   bootstrap – first-run venv creation, progress stages, retry commands
//!   tools     – sidecar detection, FFmpeg/ffprobe/uv resolution & install
//!   backend   – spawn backend process, port probing, log paths
//!   commands  – Tauri IPC commands (sysinfo, logs, HF cache, paste, tray, dictation)

pub mod backend;
pub mod blank_guard;
pub mod bootstrap;
pub mod commands;
pub mod config;
pub mod crash;
pub mod dictation_output;
pub mod dictation_shortcut;
pub mod persistence_exit;
pub mod reset;
pub mod setup;
pub mod speech_sidecar;
pub mod tools;
pub mod uninstall;
pub mod updater_channel;
pub mod watch_folder;
mod watch_folder_core;
#[cfg(target_os = "linux")]
pub mod wayland_shortcut;
#[cfg(target_os = "linux")]
pub mod wayland_shortcut_core;

use std::collections::{HashMap, VecDeque};
use std::process::Child;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::TrayIconBuilder;
use tauri::{Emitter, Manager};
use tauri_plugin_positioner::{Position, WindowExt};

use crate::bootstrap::{set_stage, BootstrapStage, BootstrapState};
use crate::config::load_config;
use crate::dictation_output::CaptureOrigin;
use crate::dictation_shortcut::DictationShortcutManager;

// ── Port ──────────────────────────────────────────────────────────────────

pub fn backend_port() -> u16 {
    std::env::var("OMNIVOICE_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(3900)
}

// ── Shared state types ────────────────────────────────────────────────────

pub struct BackendState {
    /// Serializes every desktop lifecycle owner from its first port/process
    /// probe through child tracking and readiness. Bootstrap, Retry, reset,
    /// setup/uninstall, shutdown, and the crash supervisor never overlap.
    pub lifecycle: Mutex<()>,
    pub process: Mutex<Option<Child>>,
    /// Stable OS containment for a desktop-spawned backend. Unix keeps the
    /// root unreaped until its inherited process group is drained; Windows
    /// owns a kill-on-close Job handle. Neither path signals a reusable PID.
    pub owned_tree: Mutex<Option<tools::OwnedProcessTree>>,
    /// A healthy same-version backend which predates this desktop launch.
    /// It is health-supervised but deliberately never killed by PID: there is
    /// no safe way to adopt ownership of an arbitrary external process tree.
    pub attached: AtomicBool,
    /// Consecutive deep-health failures for an unowned attachment. A single
    /// busy/slow response is not process death; the supervisor confirms a
    /// sustained outage before considering a safe replacement.
    pub attached_health: Mutex<AttachedHealthState>,
    /// When the tracked child was spawned — feeds the crash marker's
    /// `uptime_s` (#941). Set alongside `process` in bootstrap.rs.
    pub spawned_at: Mutex<Option<std::time::Instant>>,
}

#[derive(Default)]
pub struct AttachedHealthState {
    pub failures: u32,
    pub unhealthy_since: Option<std::time::Instant>,
}

pub struct AppFlags {
    pub quitting: AtomicBool,
    /// A destructive uninstall is stopping the backend but is not itself a
    /// terminal app exit until the purge succeeds. Keeping this separate from
    /// `quitting` lets CloseRequested keep the main window alive and lets a
    /// failed purge recover without overwriting a concurrent real exit.
    pub uninstalling: AtomicBool,
    /// Generation which owns `uninstalling`. A stale join/panic finalizer may
    /// only release its own claim, never a newer uninstall attempt.
    pub uninstall_owner: std::sync::atomic::AtomicU64,
    /// Whether dictation is currently recording. The tray's Start/Stop item
    /// used to infer this from `widget.is_visible()`, which stopped meaning
    /// anything once the widget became a permanently hidden host. The frontend
    /// already reports every start and stop via `set_tray_recording` (it drives
    /// the tray icon), so that same call keeps this in step.
    pub dictating: AtomicBool,
    pub capture: Mutex<CaptureDispatchState>,
    pub output: dictation_output::DictationOutput,
}

pub struct CaptureDispatchState {
    pub(crate) ready: bool,
    pub(crate) pending: VecDeque<CaptureEvent>,
    registration_counter: u64,
    delivery_counter: u64,
    active_registration: Option<u64>,
    in_flight: HashMap<u64, CaptureInFlight>,
}

struct CaptureInFlight {
    event: CaptureEvent,
    outcome: Option<Result<(), String>>,
}

pub(crate) enum CaptureReceiptCancellation {
    Received,
    Cancelled(CaptureEvent),
    Missing,
}

pub(crate) enum CaptureAcceptanceTimeout {
    Completed(Result<(), String>),
    Cancelled(CaptureEvent),
    Missing,
}

struct CaptureEnqueue {
    delivery_id: u64,
    event: Option<CaptureEvent>,
}

impl Default for CaptureDispatchState {
    fn default() -> Self {
        Self {
            ready: false,
            pending: VecDeque::new(),
            registration_counter: 0,
            delivery_counter: 0,
            active_registration: None,
            in_flight: HashMap::new(),
        }
    }
}

impl CaptureDispatchState {
    pub(crate) fn begin_registration(&mut self) -> u64 {
        self.registration_counter = self.registration_counter.wrapping_add(1).max(1);
        self.active_registration = Some(self.registration_counter);
        self.ready = false;
        self.registration_counter
    }

    pub(crate) fn mark_registration_ready(
        &mut self,
        registration_id: u64,
    ) -> VecDeque<CaptureEvent> {
        if self.active_registration != Some(registration_id) {
            return VecDeque::new();
        }
        self.ready = true;
        self.pending
            .iter()
            .cloned()
            .map(|mut event| {
                event.payload.registration_id = registration_id;
                event
            })
            .collect()
    }

    fn enqueue(&mut self, mut event: CaptureEvent) -> CaptureEnqueue {
        self.delivery_counter = self.delivery_counter.wrapping_add(1).max(1);
        event.payload.delivery_id = self.delivery_counter;
        self.pending.push_back(event.clone());
        let ready_event = self
            .active_registration
            .filter(|_| self.ready)
            .map(|registration_id| {
                event.payload.registration_id = registration_id;
                event
            });
        CaptureEnqueue {
            delivery_id: self.delivery_counter,
            event: ready_event,
        }
    }

    pub(crate) fn acknowledge(&mut self, registration_id: u64, delivery_id: u64) -> bool {
        if self.active_registration != Some(registration_id) {
            return false;
        }
        if let Some(index) = self
            .pending
            .iter()
            .position(|event| event.payload.delivery_id == delivery_id)
        {
            if let Some(event) = self.pending.remove(index) {
                if event.await_result {
                    self.in_flight.insert(
                        delivery_id,
                        CaptureInFlight {
                            event,
                            outcome: None,
                        },
                    );
                }
                return true;
            }
        }
        false
    }

    pub(crate) fn complete(
        &mut self,
        registration_id: u64,
        delivery_id: u64,
        error: Option<String>,
    ) {
        if self.active_registration != Some(registration_id) {
            return;
        }
        if let Some(delivery) = self.in_flight.get_mut(&delivery_id) {
            delivery.outcome = Some(error.map_or_else(|| Ok(()), Err));
        }
    }

    pub(crate) fn completion_ready(&self, delivery_id: u64) -> bool {
        self.in_flight
            .get(&delivery_id)
            .is_some_and(|delivery| delivery.outcome.is_some())
    }

    pub(crate) fn take_completion(&mut self, delivery_id: u64) -> Option<Result<(), String>> {
        let ready = self.completion_ready(delivery_id);
        ready
            .then(|| self.in_flight.remove(&delivery_id))
            .flatten()
            .and_then(|delivery| delivery.outcome)
    }

    pub(crate) fn delivery_pending(&self, delivery_id: u64) -> bool {
        self.pending
            .iter()
            .any(|event| event.payload.delivery_id == delivery_id)
    }

    pub(crate) fn cancel_unreceived_delivery(
        &mut self,
        delivery_id: u64,
    ) -> CaptureReceiptCancellation {
        if self.in_flight.contains_key(&delivery_id) {
            return CaptureReceiptCancellation::Received;
        }
        let Some(index) = self
            .pending
            .iter()
            .position(|event| event.payload.delivery_id == delivery_id)
        else {
            return CaptureReceiptCancellation::Missing;
        };
        self.pending
            .remove(index)
            .map_or(CaptureReceiptCancellation::Missing, CaptureReceiptCancellation::Cancelled)
    }

    pub(crate) fn cancel_delivery(&mut self, delivery_id: u64) -> Option<CaptureEvent> {
        if let Some(index) = self
            .pending
            .iter()
            .position(|event| event.payload.delivery_id == delivery_id)
        {
            return self.pending.remove(index);
        }
        self.in_flight
            .remove(&delivery_id)
            .map(|delivery| delivery.event)
    }

    pub(crate) fn take_completion_or_cancel(
        &mut self,
        delivery_id: u64,
    ) -> CaptureAcceptanceTimeout {
        if let Some(completion) = self.take_completion(delivery_id) {
            CaptureAcceptanceTimeout::Completed(completion)
        } else {
            self.cancel_delivery(delivery_id).map_or(
                CaptureAcceptanceTimeout::Missing,
                CaptureAcceptanceTimeout::Cancelled,
            )
        }
    }

    pub(crate) fn end_registration(&mut self, registration_id: u64) {
        if self.active_registration == Some(registration_id) {
            self.active_registration = None;
            self.ready = false;
        }
    }
}

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DictationCapturePayload {
    pub(crate) session_id: u64,
    pub(crate) delivery_id: u64,
    pub(crate) registration_id: u64,
}

#[derive(Clone)]
pub(crate) struct CaptureEvent {
    pub(crate) name: &'static str,
    pub(crate) payload: DictationCapturePayload,
    await_result: bool,
}

pub struct TrayHandle {
    pub tray: Mutex<Option<tauri::tray::TrayIcon>>,
    pub dictate: Mutex<Option<tauri::menu::MenuItem<tauri::Wry>>>,
}

fn dictation_capture_event(action: &str, dictating: bool) -> &'static str {
    match action {
        "stop" => "tray-dictate-stop",
        "toggle" if dictating => "tray-dictate-stop",
        _ => "tray-dictate",
    }
}

pub fn dispatch_dictation_capture(app: &tauri::AppHandle, action: &str) {
    let _ = dispatch_dictation_capture_from(app, action, CaptureOrigin::Shortcut, false);
}

pub(crate) fn request_dictation_capture_delivery(
    app: &tauri::AppHandle,
    action: &str,
) -> Option<u64> {
    dispatch_dictation_capture_from(app, action, CaptureOrigin::Shortcut, true)
}

fn dispatch_dictation_capture_from(
    app: &tauri::AppHandle,
    action: &str,
    origin: CaptureOrigin,
    await_result: bool,
) -> Option<u64> {
    let flags = app.state::<AppFlags>();
    let event = dictation_capture_event(action, flags.dictating.load(Ordering::SeqCst));
    let session_id = if event == "tray-dictate" {
        flags.output.begin_session(origin)
    } else if let Some(session_id) = flags.output.current_session_id() {
        session_id
    } else {
        log::warn!("Dictation capture '{action}' ignored — no active output session");
        return None;
    };

    // The recorder lives in the widget WebView. WebKit can suspend that
    // document while its window is hidden, so an event cannot be relied on to
    // wake the very listener that must receive it. Preserve the output target
    // first, then show the non-activating pill before enqueueing/emitting the
    // start event. The widget's idle reconcile hides it again if capture is
    // disabled or startup exits early.
    if event == "tray-dictate" {
        if let Err(error) = commands::show_dictation_pill(app.clone()) {
            log::warn!("Dictation capture '{action}' could not wake the capture window: {error}");
        }
    }

    let capture_event = CaptureEvent {
        name: event,
        payload: DictationCapturePayload {
            session_id,
            delivery_id: 0,
            registration_id: 0,
        },
        await_result,
    };
    let Ok(mut capture) = flags.capture.lock() else {
        log::warn!("Dictation capture state lock poisoned");
        return None;
    };
    let enqueued = capture.enqueue(capture_event);
    let delivery_id = enqueued.delivery_id;
    if let Some(capture_event) = enqueued.event {
        drop(capture);
        // A press that reaches Rust but produces no recording is otherwise
        // indistinguishable from one the compositor never delivered, so say
        // which side of the handshake the press left on.
        if let Err(error) = app.emit(event, capture_event.payload) {
            log::warn!("Dictation capture '{action}' could not emit {event}: {error}");
        } else {
            log::info!("Dictation capture '{action}' emitted as {event}");
        }
    } else {
        log::warn!(
            "Dictation capture '{action}' queued — the capture window has not registered yet"
        );
    }
    Some(delivery_id)
}

#[cfg(test)]
mod dictation_capture_tests {
    use super::{
        dictation_capture_event, CaptureAcceptanceTimeout, CaptureDispatchState, CaptureEvent,
        CaptureReceiptCancellation, DictationCapturePayload,
    };

    fn capture_event(name: &'static str) -> CaptureEvent {
        CaptureEvent {
            name,
            payload: DictationCapturePayload {
                session_id: 7,
                delivery_id: 0,
                registration_id: 0,
            },
            await_result: false,
        }
    }

    #[test]
    fn toggle_starts_when_idle_and_stops_when_recording() {
        assert_eq!(dictation_capture_event("toggle", false), "tray-dictate");
        assert_eq!(dictation_capture_event("toggle", true), "tray-dictate-stop");
    }

    #[test]
    fn readiness_queue_preserves_press_then_release() {
        let mut state = CaptureDispatchState::default();
        state.enqueue(capture_event("tray-dictate"));
        state.enqueue(capture_event("tray-dictate-stop"));
        let names: Vec<_> = state.pending.into_iter().map(|event| event.name).collect();
        assert_eq!(names, ["tray-dictate", "tray-dictate-stop"]);
    }

    #[test]
    fn unacknowledged_delivery_survives_listener_replacement() {
        let mut state = CaptureDispatchState::default();
        state.enqueue(capture_event("tray-dictate"));
        let stale = state.begin_registration();
        let first_delivery = state.mark_registration_ready(stale);
        let delivery_id = first_delivery[0].payload.delivery_id;

        state.end_registration(stale);
        let current = state.begin_registration();
        let retried = state.mark_registration_ready(current);
        assert_eq!(retried.len(), 1);
        assert_eq!(retried[0].payload.delivery_id, delivery_id);
        assert_eq!(retried[0].payload.registration_id, current);

        assert!(!state.acknowledge(stale, delivery_id));
        assert_eq!(state.pending.len(), 1);
        assert!(state.delivery_pending(delivery_id));
        assert!(state.acknowledge(current, delivery_id));
        assert!(state.pending.is_empty());
        assert!(!state.delivery_pending(delivery_id));
    }

    #[test]
    fn timed_out_delivery_can_be_cancelled_without_touching_others() {
        let mut state = CaptureDispatchState::default();
        state.enqueue(capture_event("tray-dictate"));
        state.enqueue(capture_event("tray-dictate-stop"));
        let first_id = state.pending[0].payload.delivery_id;
        let second_id = state.pending[1].payload.delivery_id;

        let cancelled = state.cancel_delivery(first_id).expect("delivery exists");
        assert_eq!(cancelled.payload.session_id, 7);
        assert!(!state.delivery_pending(first_id));
        assert!(state.delivery_pending(second_id));
    }

    #[test]
    fn awaited_delivery_preserves_frontend_rejection_for_the_requester() {
        let mut state = CaptureDispatchState::default();
        let registration_id = state.begin_registration();
        state.mark_registration_ready(registration_id);
        let mut event = capture_event("tray-dictate");
        event.await_result = true;
        let delivery_id = state.enqueue(event).delivery_id;

        assert!(state.acknowledge(registration_id, delivery_id));
        assert!(!state.completion_ready(delivery_id));
        state.complete(
            registration_id,
            delivery_id,
            Some("Dictation is disabled".into()),
        );
        assert!(state.completion_ready(delivery_id));

        assert_eq!(
            state.take_completion(delivery_id),
            Some(Err("Dictation is disabled".into()))
        );
        assert_eq!(state.take_completion(delivery_id), None);
    }

    #[test]
    fn cancellation_suppresses_an_event_cloned_for_ready_emission() {
        let mut state = CaptureDispatchState::default();
        let registration_id = state.begin_registration();
        state.mark_registration_ready(registration_id);
        let mut event = capture_event("tray-dictate");
        event.await_result = true;
        let enqueued = state.enqueue(event);
        let emitted = enqueued.event.expect("ready event was cloned");

        assert!(matches!(
            state.cancel_unreceived_delivery(enqueued.delivery_id),
            CaptureReceiptCancellation::Cancelled(_)
        ));
        assert!(!state.acknowledge(
            emitted.payload.registration_id,
            emitted.payload.delivery_id
        ));
    }

    #[test]
    fn listener_receipt_wins_atomically_over_timeout_cancellation() {
        let mut state = CaptureDispatchState::default();
        let registration_id = state.begin_registration();
        state.mark_registration_ready(registration_id);
        let mut event = capture_event("tray-dictate");
        event.await_result = true;
        let delivery_id = state.enqueue(event).delivery_id;

        assert!(state.acknowledge(registration_id, delivery_id));
        assert!(matches!(
            state.cancel_unreceived_delivery(delivery_id),
            CaptureReceiptCancellation::Received
        ));
    }

    #[test]
    fn completion_at_the_timeout_boundary_wins_over_cancellation() {
        let mut state = CaptureDispatchState::default();
        let registration_id = state.begin_registration();
        state.mark_registration_ready(registration_id);
        let mut event = capture_event("tray-dictate");
        event.await_result = true;
        let delivery_id = state.enqueue(event).delivery_id;
        state.acknowledge(registration_id, delivery_id);
        state.complete(registration_id, delivery_id, None);

        assert!(matches!(
            state.take_completion_or_cancel(delivery_id),
            CaptureAcceptanceTimeout::Completed(Ok(()))
        ));
        assert!(matches!(
            state.take_completion_or_cancel(delivery_id),
            CaptureAcceptanceTimeout::Missing
        ));
    }

    #[test]
    fn stale_listener_cannot_claim_or_clear_a_newer_registration() {
        let mut state = CaptureDispatchState::default();
        let stale = state.begin_registration();
        let current = state.begin_registration();

        assert!(state.mark_registration_ready(stale).is_empty());
        assert!(!state.ready);
        state.mark_registration_ready(current);
        assert!(state.ready);
        state.end_registration(stale);
        assert!(state.ready);
        state.end_registration(current);
        assert!(!state.ready);
    }
}

pub const TRAY_ICON_DEFAULT: &[u8] = include_bytes!("../icons/32x32.png");
pub const TRAY_ICON_RECORDING: &[u8] = include_bytes!("../icons/tray-recording.png");

// ── WebView media-capture permissions ─────────────────────────────────────
//
// `getUserMedia()` needs the WebView-engine permission answered, separately
// from the OS-level microphone permission:
//
// - Windows (WebView2): with no `PermissionRequested` handler registered,
//   WebView2 falls back to its own permission UI. The dictation pill is a
//   300×64 transparent, undecorated, always-on-top window that can't host
//   that UI (and is deliberately unfocused so the auto-paste lands in the
//   target app) — the request dies and getUserMedia() rejects with
//   NotAllowedError even though Windows already lets the app record (#323:
//   backend transcribes fine, the pill still says access denied). We answer
//   media-capture requests in code, for the app's own origin only. The OS
//   privacy toggle (Settings → Privacy & security → Microphone) still
//   applies on top.
// - Linux (WebKitGTK): media-stream must be enabled per-WebView and the
//   permission request answered programmatically.
// - macOS (WKWebView): nothing to do here in code — wry's own WKUIDelegate
//   (WryWebViewUIDelegate::request_media_capture_permission) already grants
//   every media-capture request unconditionally at the WebKit/JS layer. But
//   that alone isn't sufficient (#1013): Tauri's macOS bundle defaults
//   `hardenedRuntime` to true, and Hardened Runtime blocks camera/microphone
//   hardware access unless the matching entitlement is present — without it,
//   TCC never even registers a request, so the app never appears in System
//   Settings → Privacy & Security → Microphone for the user to enable. See
//   src-tauri/entitlements.plist (wired in via tauri.conf.json's
//   bundle.macOS.entitlements) for the actual grant; NSMicrophoneUsageDescription
//   in Info.plist only supplies the *prompt text* TCC shows, it doesn't
//   substitute for the entitlement.

/// True for origins the app itself serves: the Tauri custom-protocol origin
/// in production and the Vite dev server / loopback in `tauri dev`.
#[cfg_attr(not(windows), allow(dead_code))]
fn is_app_origin(uri: &str) -> bool {
    let rest = match uri
        .strip_prefix("https://")
        .or_else(|| uri.strip_prefix("http://"))
    {
        Some(rest) => rest,
        None => return false,
    };
    let host = rest
        .split(['/', '?', '#'])
        .next()
        .unwrap_or("")
        .split(':')
        .next()
        .unwrap_or("");
    host == "tauri.localhost" || host == "localhost" || host == "127.0.0.1"
}

#[allow(unused_variables)]
fn grant_webview_media_permissions(win: &tauri::WebviewWindow) {
    #[cfg(target_os = "linux")]
    {
        let label = win.label().to_string();
        let _ = win.with_webview(move |webview| {
            use webkit2gtk::{PermissionRequestExt, SettingsExt, WebViewExt};
            let wk = webview.inner();
            if let Some(settings) = WebViewExt::settings(&wk) {
                settings.set_enable_media_stream(true);
                settings.set_enable_mediasource(true);
                settings.set_media_playback_requires_user_gesture(false);
                log::info!("WebKitGTK: media-stream enabled on '{label}'");
            }
            wk.connect_permission_request(|_, request| {
                request.allow();
                true
            });
        });
    }

    #[cfg(windows)]
    {
        let label = win.label().to_string();
        let _ = win.with_webview(move |webview| {
            use webview2_com::Microsoft::Web::WebView2::Win32::{
                ICoreWebView2, ICoreWebView2PermissionRequestedEventArgs,
                COREWEBVIEW2_PERMISSION_KIND_CAMERA, COREWEBVIEW2_PERMISSION_KIND_MICROPHONE,
                COREWEBVIEW2_PERMISSION_KIND_UNKNOWN_PERMISSION,
                COREWEBVIEW2_PERMISSION_STATE_ALLOW,
            };
            use webview2_com::{take_pwstr, PermissionRequestedEventHandler};

            let core = match unsafe { webview.controller().CoreWebView2() } {
                Ok(core) => core,
                Err(e) => {
                    log::warn!("WebView2: CoreWebView2 unavailable on '{label}': {e}");
                    return;
                }
            };
            let handler = PermissionRequestedEventHandler::create(Box::new(
                move |_core: Option<ICoreWebView2>,
                      args: Option<ICoreWebView2PermissionRequestedEventArgs>|
                      -> windows_core::Result<()> {
                    let args = match args {
                        Some(args) => args,
                        None => return Ok(()),
                    };
                    unsafe {
                        let mut kind = COREWEBVIEW2_PERMISSION_KIND_UNKNOWN_PERMISSION;
                        args.PermissionKind(&mut kind)?;
                        if kind != COREWEBVIEW2_PERMISSION_KIND_MICROPHONE
                            && kind != COREWEBVIEW2_PERMISSION_KIND_CAMERA
                        {
                            // Leave non-media permissions to default handling.
                            return Ok(());
                        }
                        let mut uri = windows_core::PWSTR::null();
                        args.Uri(&mut uri)?;
                        if is_app_origin(&take_pwstr(uri)) {
                            args.SetState(COREWEBVIEW2_PERMISSION_STATE_ALLOW)?;
                        }
                    }
                    Ok(())
                },
            ));
            let mut token = 0i64;
            match unsafe { core.add_PermissionRequested(&handler, &mut token) } {
                Ok(()) => log::info!("WebView2: media-capture auto-grant active on '{label}'"),
                Err(e) => log::warn!(
                    "WebView2: PermissionRequested handler registration failed on '{label}': {e}"
                ),
            }
        });
    }

    // macOS: intentionally empty — see module comment above.
}

#[cfg(test)]
mod media_permission_tests {
    use super::is_app_origin;

    #[test]
    fn allows_app_and_dev_origins() {
        assert!(is_app_origin("http://tauri.localhost/index.html"));
        assert!(is_app_origin("https://tauri.localhost"));
        assert!(is_app_origin("http://localhost:3901/"));
        assert!(is_app_origin("http://127.0.0.1:3901/index.html"));
    }

    #[test]
    fn rejects_foreign_origins() {
        assert!(!is_app_origin("http://tauri.localhost.evil.com/"));
        assert!(!is_app_origin("https://example.com/"));
        assert!(!is_app_origin("file:///C:/index.html"));
        assert!(!is_app_origin("http://localhost.evil.com:3901/"));
        assert!(!is_app_origin(""));
    }
}

// ── Windows: dictation pill must never take foreground focus (#982) ────────
//
// Windows counterpart of #287 (macOS auto-paste — don't steal focus). The
// pill is `.always_on_top(true).skip_taskbar(true)` and is documented above
// (see `grant_webview_media_permissions`) as "deliberately unfocused so the
// auto-paste lands in the target app" — true on macOS, but on Windows,
// showing an always-on-top top-level window gives it Win32 foreground
// activation by default (ordinary Windows window-manager behavior; macOS
// doesn't force-activate a shown window the same way). Nothing marked the
// pill non-activating, so on Windows it stole foreground on every show —
// the synthesized Ctrl+V from `simulate_paste` landed back in the pill
// instead of the app the user was dictating into, and because the pill
// wrongly held focus for the whole session the target app never got it back
// until the pill's auto-dismiss timer eventually hid it.
//
// Two pieces, both required (verified by reading how `.show()` is used at
// the call sites below — several are followed by an explicit `set_focus()`
// that would fight the style bit on its own):
//   1. WS_EX_NOACTIVATE on the HWND, applied once right after creation, so
//      the OS never grants this window foreground activation implicitly.
//   2. `ShowWindow(SW_SHOWNOACTIVATE)` in place of `WebviewWindow::show()` at
//      the pill's dictation-trigger call sites, and the explicit
//      `set_focus()` calls at those same sites are skipped on Windows (the
//      same way they already are on macOS below).
//
// The flag math (`with_noactivate_style`) is a plain function so it's
// unit-testable on every platform — the actual Win32 syscalls that use it
// are Windows-only and can't run under `cargo test` on a non-Windows runner.

/// `WS_EX_NOACTIVATE` (winuser.h: `#define WS_EX_NOACTIVATE 0x08000000L`).
/// Hardcoded rather than imported from the `windows` crate so `with_noactivate_style`
/// below stays free of the Windows-only dependency and is testable everywhere.
/// Only consumed by Windows-only code (or the platform-agnostic test module
/// below) — `#[allow(dead_code)]` elsewhere, same as `is_app_origin` above.
#[cfg_attr(not(windows), allow(dead_code))]
const WS_EX_NOACTIVATE_BIT: isize = 0x0800_0000;

/// OR `WS_EX_NOACTIVATE` into an existing extended window style, preserving
/// every other bit already set (topmost, layered, etc. — the pill's
/// `always_on_top(true)` sets one of these). Pure so it's unit-testable
/// without a real HWND. See module comment above for why this exists.
#[cfg_attr(not(windows), allow(dead_code))]
fn with_noactivate_style(current_ex_style: isize) -> isize {
    current_ex_style | WS_EX_NOACTIVATE_BIT
}

/// Mark the pill's HWND `WS_EX_NOACTIVATE`, once, right after creation — this
/// holds for every later `.show()` regardless of call site (belt-and-braces
/// alongside `show_pill_noactivate` below, which some call sites also need
/// because they pair `.show()` with an explicit `set_focus()`).
#[cfg(target_os = "windows")]
fn mark_pill_noactivate(win: &tauri::WebviewWindow) {
    use windows::Win32::UI::WindowsAndMessaging::{
        GetWindowLongPtrW, SetWindowLongPtrW, GWL_EXSTYLE,
    };
    let Ok(hwnd) = win.hwnd() else {
        log::warn!("pill: could not resolve HWND to apply WS_EX_NOACTIVATE (#982)");
        return;
    };
    unsafe {
        let current = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, with_noactivate_style(current));
    }
}

/// Show the pill without granting it foreground activation.
///
/// Two steps, and both are load-bearing.
///
/// `win.show()` is what tells TAURI the window is visible. Raw `ShowWindow`
/// alone puts it on screen behind Tauri's back, and Tauri goes on believing it
/// is hidden — so `isVisible()` answers `false` while the user is looking at
/// the thing, `hide()` becomes a no-op on a window it thinks is already
/// hidden, and the capture widget's idle reconcile (which asks `isVisible()`
/// before deciding to clean up) concludes there is nothing to clean up. The
/// result is an empty dark rectangle stranded on the desktop after the pill is
/// dismissed, with no way to remove it short of quitting the app.
///
/// `SW_SHOWNOACTIVATE` is what keeps the foreground where it belongs (#982): a
/// pill that steals focus makes the paste land in the pill instead of the
/// user's document. `WS_EX_NOACTIVATE` is already on the window from
/// `mark_pill_noactivate` at creation, which is what makes the `show()` above
/// safe — the style bit, not the show flag, is what actually refuses
/// activation. The flag stays anyway: it costs nothing and holds even if the
/// style bit could not be applied (`hwnd()` can fail).
#[cfg(target_os = "windows")]
pub(crate) fn show_pill_noactivate(win: &tauri::WebviewWindow) {
    use windows::Win32::UI::WindowsAndMessaging::{ShowWindow, SW_SHOWNOACTIVATE};
    show_pill_noactivate_with(
        || win.show().map_err(|error| error.to_string()),
        || {
            let hwnd = win.hwnd().map_err(|_| "no HWND".to_string())?;
            unsafe {
                let _ = ShowWindow(hwnd, SW_SHOWNOACTIVATE);
            }
            Ok(())
        },
    )
}

/// The ordering itself, with both shows as parameters.
///
/// Split out so a test can pin the contract that the bug broke: Tauri's own
/// `show` must run, and it must run FIRST. A native-only show is what left an
/// empty pill window stranded on the desktop.
///
/// And when Tauri's show FAILS, the native show must not run at all (Greptile).
/// Showing it natively anyway puts an always-on-top window on screen that
/// Tauri believes is hidden — the exact stranded-window bug, reached by a
/// different door. A pill that does not appear is the lesser failure: the
/// tray's red dot still says the user is being recorded, and nothing is left
/// behind that cannot be removed.
pub(crate) fn show_pill_noactivate_with<T, N>(show_tauri: T, show_native: N)
where
    T: FnOnce() -> Result<(), String>,
    N: FnOnce() -> Result<(), String>,
{
    if let Err(error) = show_tauri() {
        log::warn!("pill: Tauri show failed; not showing it natively either, or it could never be hidden: {error}");
        return;
    }
    if let Err(error) = show_native() {
        log::warn!("pill: non-activating show failed ({error}) (#982)");
    }
}

#[cfg(test)]
mod pill_noactivate_tests {
    use super::{show_pill_noactivate_with, with_noactivate_style, WS_EX_NOACTIVATE_BIT};

    #[test]
    fn showing_the_pill_tells_tauri_before_it_tells_windows() {
        // The bug: only the raw Win32 show ran, so the window went on screen
        // behind Tauri's back. Tauri then answered `isVisible()` with false
        // while the user was looking at it, `hide()` did nothing on a window
        // it believed was already hidden, and the widget's idle reconcile —
        // which asks `isVisible()` before cleaning up — concluded there was
        // nothing to clean up. An empty rectangle stayed on the desktop until
        // the app was quit.
        use std::cell::RefCell;
        let order = RefCell::new(Vec::new());
        show_pill_noactivate_with(
            || {
                order.borrow_mut().push("tauri");
                Ok(())
            },
            || {
                order.borrow_mut().push("native");
                Ok(())
            },
        );
        assert_eq!(
            order.into_inner(),
            ["tauri", "native"],
            "Tauri's own show must run, and run first"
        );
    }

    #[test]
    fn a_failing_tauri_show_does_not_fall_back_to_a_native_one() {
        // Greptile: a native-only show after Tauri's show failed puts an
        // always-on-top window on screen that Tauri believes is hidden, so
        // neither dismiss() nor the idle reconcile can ever remove it — the
        // stranded-window bug again. A pill that does not appear is the lesser
        // failure; the tray's red dot still signals recording.
        let mut native_ran = false;
        show_pill_noactivate_with(
            || Err("no window".to_string()),
            || {
                native_ran = true;
                Ok(())
            },
        );
        assert!(!native_ran, "a native show after a failed Tauri show strands an unhidable window");
    }

    #[test]
    fn adds_noactivate_bit_without_clobbering_existing_style() {
        // Stand-in for whatever bits the pill's always_on_top/skip_taskbar
        // window already carries (e.g. WS_EX_TOPMOST = 0x00000008) —
        // NOACTIVATE must be added on top, never replace them.
        let topmost = 0x0000_0008isize;
        let updated = with_noactivate_style(topmost);
        assert_eq!(
            updated & WS_EX_NOACTIVATE_BIT,
            WS_EX_NOACTIVATE_BIT,
            "NOACTIVATE bit must be set"
        );
        assert_eq!(
            updated & topmost,
            topmost,
            "pre-existing style bits must survive"
        );
    }

    #[test]
    fn idempotent_if_already_noactivate() {
        assert_eq!(
            with_noactivate_style(WS_EX_NOACTIVATE_BIT),
            WS_EX_NOACTIVATE_BIT
        );
    }

    #[test]
    fn matches_documented_win32_value() {
        // winuser.h: #define WS_EX_NOACTIVATE 0x08000000L
        assert_eq!(WS_EX_NOACTIVATE_BIT, 0x0800_0000);
    }
}

// ── Tauri entry ───────────────────────────────────────────────────────────

/// Production `ExitRequested` teardown, exposed so the real-child lifecycle
/// harness exercises the exact shutdown path used by the desktop event loop.
#[doc(hidden)]
pub fn shutdown_backend_for_exit<R: tauri::Runtime>(app_handle: &tauri::AppHandle<R>) {
    // Raise the quitting flag FIRST: exits that don't pass through the tray
    // Quit item (macOS ⌘Q, OS session end) would otherwise let a death watcher
    // observe our own termination and record a false crash marker (#941).
    app_handle
        .state::<AppFlags>()
        .quitting
        .store(true, Ordering::SeqCst);
    if let Err(error) = bootstrap::with_backend_stopped(app_handle, || {}) {
        log::warn!("Could not fully stop the backend during app exit: {error}");
    }
}

/// Show, unminimize and focus the main window. Shared by the tray's "Show
/// VoiceStudio" menu item and the macOS `RunEvent::Reopen` handler below (Dock
/// icon clicked while the main window is hidden), so the two recovery paths
/// behave identically instead of drifting apart over time.
fn show_and_focus_main_window<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        #[cfg(not(target_os = "macos"))]
        let _ = win.set_skip_taskbar(false);
        let _ = win.unminimize();
        let _ = win.set_focus();
        // Self-recovery: if the webview failed to load the dev/prod URL
        // earlier (Vite restarted, backend not up yet at first show, etc.)
        // the window shows a blank `<body></body>` with a "Could not connect
        // to the server" console error. Reload only when the body is empty
        // so a healthy window doesn't blink on every show.
        let _ = win.eval(
            "if (document.body && document.body.childElementCount === 0) { location.reload(); }",
        );
    }
}

/// Whether a macOS `RunEvent::Reopen` (Dock icon clicked — Cocoa's
/// `applicationShouldHandleReopen:hasVisibleWindows:`) should restore the
/// main window. Pure so it's unit-testable — the actual event only fires
/// inside the real Cocoa event loop and can't be synthesized under
/// `cargo test` (see the `with_noactivate_style` comment above for the same
/// rationale). `CloseRequested` (see `on_window_event` below) hides the main
/// window rather than destroying it, so it is merely invisible once the user
/// has closed it — exactly when the Dock icon should bring it back.
///
/// Keyed on the MAIN window specifically, not on Cocoa's `has_visible_windows`
/// flag. This app owns a second window: the always-on-top dictation pill
/// (`widget`, built below), which is shown and hidden independently and can
/// sit on screen for a long time on its own — the Accessibility-setup state
/// persists until the permission is granted. Keying on "any window visible"
/// would report `true` from the pill alone and leave the Dock icon dead in
/// precisely the case this handler exists to fix.
///
/// Only called from the macOS-gated `RunEvent::Reopen` arm below outside of
/// tests — `#[allow(dead_code)]` elsewhere, same treatment as `is_app_origin`
/// and `with_noactivate_style` above.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn should_restore_on_reopen(main_window_visible: bool, _cocoa_has_visible_windows: bool) -> bool {
    // Cocoa's aggregate flag is accepted and deliberately ignored. Taking it
    // as a parameter rather than dropping it at the call site is what lets
    // the tests below pin the contract: `(main: false, cocoa: true)` — the
    // pill up, the main window closed — must still restore. An earlier
    // revision decided on the aggregate alone and left the Dock icon dead in
    // exactly that state.
    !main_window_visible
}

#[cfg(test)]
mod reopen_tests {
    use super::should_restore_on_reopen;

    #[test]
    fn restores_when_the_main_window_is_hidden() {
        assert!(should_restore_on_reopen(false, false));
    }

    #[test]
    fn does_nothing_when_the_main_window_is_already_visible() {
        assert!(!should_restore_on_reopen(true, true));
    }

    /// Regression guard: the dictation pill is a separate always-on-top
    /// window that can be visible while the main window is closed — the
    /// Accessibility-setup state stays up until the permission is granted.
    /// An earlier revision keyed this decision on Cocoa's
    /// `has_visible_windows`, which the pill alone sets to `true`, leaving
    /// the Dock icon dead in exactly the situation this handler is for.
    /// The decision must depend only on the main window.
    #[test]
    fn restores_even_when_another_window_such_as_the_pill_is_visible() {
        // Cocoa reports a visible window (the pill) while the main window is
        // hidden. Passing both values separately is the point: this case is
        // what distinguishes the main-window rule from the aggregate one, and
        // it fails if the body ever goes back to `!cocoa_has_visible_windows`.
        assert!(should_restore_on_reopen(false, true));
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // #879: if the previous run requested a WebView cache repair (splash
    // recovery panel → clear_webview_cache_and_relaunch), perform it now —
    // before any webview exists, so WebView2 holds no locks on the profile.
    commands::clear_webview_cache_if_marked();

    // ── Detect pill mode from CLI args OR persisted config ────────────────
    // CLI flag takes precedence. If not passed, fall back to the
    // `launch_as_widget` config field (set via tray "Switch to Pill Mode" or
    // Settings → Launch options checkbox). This means a user can configure
    // "launch as widget by default" once and never need to remember the flag.
    let cli_pill = std::env::args().any(|a| a == "--pill");
    let pill_mode = cli_pill || crate::config::load_config_pre_app().launch_as_widget;
    if pill_mode {
        log::info!(
            "Starting in pill (dictation-only) mode (source: {})",
            if cli_pill {
                "--pill flag"
            } else {
                "config.launch_as_widget"
            }
        );
        // On macOS, hide the Dock icon in pill mode so only the tray shows.
        // This is handled after the app builds via set_activation_policy.
    }

    let pill_mode_setup = pill_mode;
    let pill_mode_tray = pill_mode;

    let app = tauri::Builder::default()
        // Single-instance MUST be registered first.
        .plugin(tauri_plugin_single_instance::init(move |app, argv, _cwd| {
            if let Some(action) = speech_sidecar::cli_dictation_action(&argv) {
                log::info!("Second-instance dictation control: {action:?}");
                speech_sidecar::dispatch_action(app, action);
                return;
            }
            log::info!("Second instance attempted — focusing existing window");
            // Always the studio window, never the widget. In pill mode this
            // used to target "widget" and show() it — which is precisely the
            // empty rectangle the hidden-host contract exists to prevent, and
            // relaunching the app is a plausible thing to do when one is stuck
            // on your desktop. Pill mode hides the studio window rather than
            // closing it, and a second launch is the user asking for the app,
            // so show that instead — the same thing the tray's "Open
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.show();
                #[cfg(not(target_os = "macos"))]
                let _ = win.set_skip_taskbar(false);
                let _ = win.unminimize();
                let _ = win.set_focus();
            }
            // #1156: when the backend died at startup, relaunching the app
            // used to just refocus the dead window — the user was stuck
            // unless they found the Retry button (or Task Manager). A
            // second-instance attempt IS the user asking for a restart, so
            // in the Failed stage run the same recovery as the Retry button.
            // (respawn_backend attaches to an already-healthy backend rather
            // than double-spawning, so a stray double-click stays harmless.)
            let state = app.state::<bootstrap::BootstrapState>();
            if bootstrap::already_diagnosed(&state.stage) {
                log::info!(
                    "Second instance while bootstrap is Failed — retrying backend spawn (#1156)"
                );
                bootstrap::respawn_backend(
                    app.clone(),
                    state.stage.clone(),
                    state.logs.clone(),
                );
            }
        }))
        .plugin(tauri_plugin_positioner::init())
        .invoke_handler(tauri::generate_handler![
            bootstrap::bootstrap_status,
            bootstrap::last_bootstrap_failure,
            bootstrap::get_bootstrap_logs,
            bootstrap::retry_bootstrap,
            bootstrap::clean_and_retry_bootstrap,
            setup::get_setup_state,
            setup::check_install_target,
            setup::complete_setup,
            config::get_region,
            config::set_region,
            config::get_update_channel,
            config::set_update_channel,
            updater_channel::check_update,
            updater_channel::install_update,
            updater_channel::list_releases,
            commands::get_sysinfo,
            commands::authorize_host_path,
            commands::read_log_tail,
            commands::hf_cache_scan,
            commands::simulate_paste,
            commands::copy_dictation_output_session,
            commands::simulate_type,
            commands::activate_dictation_output_session,
            commands::reject_dictation_output_session,
            commands::finish_dictation_output_session,
            commands::check_accessibility,
            commands::open_accessibility_settings,
            commands::check_microphone,
            commands::open_microphone_settings,
            commands::open_input_monitoring_settings,
            commands::set_tray_recording,
            commands::quit_app,
            persistence_exit::confirm_persistence_flush,
            commands::save_text_file,
            commands::reveal_host_path,
            commands::get_dictation_shortcut,
            commands::get_effective_dictation_shortcut,
            commands::set_dictation_shortcut,
            commands::request_dictation_capture,
            commands::begin_dictation_capture_registration,
            commands::mark_dictation_capture_ready,
            commands::acknowledge_dictation_capture_delivery,
            commands::complete_dictation_capture_delivery,
            commands::end_dictation_capture_registration,
            commands::show_dictation_pill,
            commands::get_launch_as_widget,
            commands::set_launch_as_widget,
            commands::clear_webview_cache_and_relaunch,
            crash::get_last_backend_crash,
            crash::acknowledge_backend_crash,
            uninstall::uninstall_scan,
            uninstall::uninstall_purge,
            reset::reset_scan,
            reset::reset_purge,
            blank_guard::report_render_state,
            blank_guard::recover_main_window,
            watch_folder::watch_folder_pick,
            watch_folder::watch_folder_scan,
            watch_folder::watch_folder_enqueue,
            watch_folder::watch_folder_forget,
        ])
        .setup(move |app| {
            // Blank-window guard: watch the main window and, if nothing ever
            // renders, reload and finally paint a built-in explanation rather
            // than leaving the user with a dark rectangle (#1178 class).
            blank_guard::arm(app.handle());

            app.handle().plugin(tauri_plugin_dialog::init())?;
            app.handle().plugin(tauri_plugin_updater::Builder::new().build())?;
            app.handle().plugin(tauri_plugin_process::init())?;
            app.handle().plugin(tauri_plugin_opener::init())?;
            // Exclude the dictation widget from state persistence — otherwise
            // `tauri-plugin-window-state` restores `visible: true` on next
            // launch if the user happened to be dictating when they quit,
            // overriding the WebviewWindowBuilder `.visible(false)` below.
            // Symptom: pill appears on app load with no shortcut press.
            // "main" is denylisted too (owner decision, 2026-07-02): the app
            // must ALWAYS open maximized — not fullscreen — per
            // tauri.conf.json (`maximized: true`, `fullscreen: false`).
            // Persisting geometry meant one manual resize made every later
            // launch reopen at that smaller size, overriding the config.
            app.handle().plugin(
                tauri_plugin_window_state::Builder::default()
                    .with_denylist(&["widget", "main"])
                    .build(),
            )?;
            app.handle().plugin(
                tauri_plugin_log::Builder::new()
                    .level(log::LevelFilter::Info)
                    .targets([
                        tauri_plugin_log::Target::new(tauri_plugin_log::TargetKind::LogDir {
                            file_name: Some("tauri".into()),
                        }),
                        tauri_plugin_log::Target::new(tauri_plugin_log::TargetKind::Stdout),
                    ])
                    .build(),
            )?;

            // ── Programmatic widget window creation ──────────────────────
            // Tauri 2's config-array creation silently dropped the widget
            // window (declared in tauri.conf.json with create:false to
            // make the handoff explicit). Some combination of transparent
            // + decorations:false + visible:false + always-on-top was being
            // rejected without an error. Building via WebviewWindowBuilder
            // works and surfaces real errors on failure.
            {
                use tauri::{WebviewWindowBuilder, WebviewUrl};
                let result = WebviewWindowBuilder::new(
                    app,
                    "widget",
                    WebviewUrl::App("index.html".into()),
                )
                .title("Capture")
                .inner_size(460.0, 164.0)
                .resizable(false)
                .transparent(true)
                .decorations(false)
                // No window shadow. On Windows, Tauri's default (`true`) gives
                // an undecorated window a 1px white border and, on Windows 11,
                // rounded corners — drawn around the WHOLE 460x164 window, not
                // the pill inside it, which is at most 284px wide. The result
                // is a visible card framing empty space around the capsule,
                // there whether the pill is showing or not. The capsule draws
                // its own edge and shadow in CSS; the window must draw nothing.
                // (Unsupported on Linux, where it was never the problem.)
                .shadow(false)
                .always_on_top(true)
                .visible(false)
                .focused(false)
                .skip_taskbar(true)
                .center()
                // Stamp the window's identity BEFORE any app script runs.
                // main-app.jsx used to learn it from `getCurrentWindow().label`,
                // which throws if `__TAURI_INTERNALS__` isn't injected yet; the
                // catch then fell back to a URL query Tauri 2 cannot set, so the
                // widget silently decided it was the main window. It then
                // rendered <App/> instead of <CaptureWidget/>: no
                // `data-window="widget"` (opaque chrome background), no pill,
                // and — because the idle-hide reconcile lives in CaptureWidget —
                // nothing left that could ever hide it. That is the dark
                // rectangle stuck on the desktop until the app was killed.
                // An init script cannot race: it is evaluated before page load.
                .initialization_script("window.__OV_WINDOW__ = 'widget';")
                .build();
                if let Err(e) = &result {
                    log::error!("Failed to create widget window: {e:?}");
                }
                // Windows: mark the pill non-activating right away so it holds
                // for every later `.show()` regardless of call site (#982).
                #[cfg(target_os = "windows")]
                if let Ok(win) = &result {
                    mark_pill_noactivate(win);
                }
                // Wayland cannot reactivate an arbitrary foreign client. The
                // GTK toplevel therefore must never accept focus when mapped.
                #[cfg(target_os = "linux")]
                if let Ok(win) = &result {
                    use gtk::prelude::GtkWindowExt;
                    if let Ok(gtk_window) = win.gtk_window() {
                        gtk_window.set_accept_focus(false);
                        gtk_window.set_focus_on_map(false);
                    }
                }
            }

            app.manage(AppFlags {
                quitting: AtomicBool::new(false),
                uninstalling: AtomicBool::new(false),
                uninstall_owner: std::sync::atomic::AtomicU64::new(0),
                dictating: AtomicBool::new(false),
                capture: Mutex::new(CaptureDispatchState::default()),
                output: dictation_output::DictationOutput::default(),
            });
            match speech_sidecar::start(app.handle().clone()) {
                Ok(sidecar) => {
                    log::info!("Speech control API ready on port {}", sidecar.port);
                    app.manage(sidecar);
                }
                Err(error) => {
                    log::warn!("Speech control API unavailable: {error}");
                }
            }
            let initial_args: Vec<String> = std::env::args().collect();
            if let Some(action) = speech_sidecar::cli_dictation_action(&initial_args) {
                speech_sidecar::dispatch_action(app.handle(), action);
            }
            app.manage(persistence_exit::PersistenceExitState::default());
            app.manage(TrayHandle {
                tray: Mutex::new(None),
                dictate: Mutex::new(None),
            });
            let startup_shortcut = load_config(app.handle()).dictation_shortcut;
            app.manage(DictationShortcutManager::new(&startup_shortcut));

            // ── Global dictation shortcut (hold-to-talk) ─────────────────
            {
                use tauri_plugin_global_shortcut::ShortcutState;

                app.handle().plugin(
                    tauri_plugin_global_shortcut::Builder::new()
                        .with_handler(move |app_handle, _shortcut, event| {
                            match event.state {
                                ShortcutState::Pressed => {
                                    log::info!("Global shortcut pressed: dictation start");
                                    // Dispatch preserves the focused target,
                                    // wakes the recorder WebView, then emits.
                                    dispatch_dictation_capture(app_handle, "start");
                                }
                                ShortcutState::Released => {
                                    log::info!("Global shortcut released: dictation stop");
                                    dispatch_dictation_capture(app_handle, "stop");
                                }
                            }
                        })
                        .build(),
                )?;
            }

            // ── System tray ──────────────────────────────────────────────
            let tray_menu = if pill_mode_tray {
                // Pill mode: minimal tray with Open Studio + Dictate + Quit
                let shortcut_hint = app.state::<DictationShortcutManager>().info().display;
                let dictate_i = MenuItemBuilder::new(format!("Start Dictation  {shortcut_hint}"))
                    .id("dictate")
                    .build(app)?;
                let open_studio_i = MenuItemBuilder::new("Open VoiceStudio")
                    .id("open_studio")
                    .build(app)?;
                let quit_i = MenuItemBuilder::new("Quit Dictation")
                    .id("quit")
                    .build(app)?;
                MenuBuilder::new(app)
                    .item(&dictate_i)
                    .separator()
                    .item(&open_studio_i)
                    .separator()
                    .item(&quit_i)
                    .build()?
            } else {
                // Studio mode: full tray
                let show_i = MenuItemBuilder::new("Show VoiceStudio")
                    .id("show")
                    .build(app)?;
                let shortcut_hint = app.state::<DictationShortcutManager>().info().display;
                let dictate_i = MenuItemBuilder::new(format!("Start Dictation  {shortcut_hint}"))
                    .id("dictate")
                    .build(app)?;
                let switch_to_pill_i = MenuItemBuilder::new("Switch to Dictation Widget")
                    .id("switch_to_pill")
                    .build(app)?;
                let settings_i = MenuItemBuilder::new("Settings")
                    .id("settings")
                    .build(app)?;
                let quit_i = MenuItemBuilder::new("Quit VoiceStudio")
                    .id("quit")
                    .build(app)?;
                MenuBuilder::new(app)
                    .item(&show_i)
                    .separator()
                    .item(&dictate_i)
                    .item(&switch_to_pill_i)
                    .item(&settings_i)
                    .separator()
                    .item(&quit_i)
                    .build()?
            };

            if let Some(item) = tray_menu.get("dictate") {
                if let Some(item) = item.as_menuitem() {
                    if let Ok(mut slot) = app.state::<TrayHandle>().dictate.lock() {
                        *slot = Some(item.clone());
                    }
                }
            }

            // Publish the effective shortcut only after the tray item exists;
            // Wayland portal registration completes asynchronously and may
            // otherwise race past the first tray-label update.
            DictationShortcutManager::register_initial(
                app.handle().clone(),
                startup_shortcut.clone(),
            );

            let tray = TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&tray_menu)
                .tooltip(if pill_mode_tray { "VoiceStudio Dictation" } else { "VoiceStudio" })
                .on_tray_icon_event(|tray, event| {
                    if matches!(
                        event,
                        tauri::tray::TrayIconEvent::Click {
                            button_state: tauri::tray::MouseButtonState::Down,
                            ..
                        }
                    ) {
                        tray.app_handle()
                            .state::<AppFlags>()
                            .output
                            .prime_tray_target();
                    }
                })
                .on_menu_event(move |app, event| {
                    match event.id().as_ref() {
                        "show" => {
                            show_and_focus_main_window(app);
                        }
                        "open_studio" => {
                            // Persist the preference (so next launch is studio, not pill)
                            // then spawn a new instance without --pill and exit this one.
                            let mut cfg = crate::config::load_config(app);
                            cfg.launch_as_widget = false;
                            crate::config::save_config(app, &cfg);
                            if let Err(error) = persistence_exit::request_spawned_relaunch(app, vec![])
                            {
                                log::error!("Could not switch to studio mode: {error}");
                            }
                        }
                        "switch_to_pill" => {
                            // Mirror of "open_studio" but the other direction:
                            // persist launch_as_widget=true, relaunch with --pill,
                            // and exit the current (studio) instance.
                            let mut cfg = crate::config::load_config(app);
                            cfg.launch_as_widget = true;
                            crate::config::save_config(app, &cfg);
                            if let Err(error) = persistence_exit::request_spawned_relaunch(
                                app,
                                vec!["--pill".into()],
                            ) {
                                log::error!("Could not switch to pill mode: {error}");
                            }
                        }
                        "dictate" => {
                            // Toggle start/stop. This used to ask the widget
                            // window whether it was visible; the widget is now a
                            // permanently hidden host, so visibility says nothing
                            // about whether we are recording. `dictating` is kept
                            // current by the frontend's existing
                            // `set_tray_recording` call on every start and stop.
                            if app.state::<AppFlags>().dictating.load(Ordering::SeqCst) {
                                let _ = dispatch_dictation_capture_from(
                                    app,
                                    "stop",
                                    CaptureOrigin::Tray,
                                    false,
                                );
                            } else {
                                let _ = dispatch_dictation_capture_from(
                                    app,
                                    "start",
                                    CaptureOrigin::Tray,
                                    false,
                                );
                            }
                        }
                        "settings" => {
                            if let Some(win) = app.get_webview_window("main") {
                                let _ = win.show();
                                #[cfg(not(target_os = "macos"))]
                                let _ = win.set_skip_taskbar(false);
                                let _ = win.set_focus();
                            }
                            let _ = app.emit("tray-navigate", "settings");
                        }
                        "quit" => {
                            app.state::<AppFlags>()
                                .quitting
                                .store(true, Ordering::SeqCst);
                            app.exit(0);
                        }
                        _ => {}
                    }
                })
                .build(app)?;
            if let Ok(mut slot) = app.state::<TrayHandle>().tray.lock() {
                *slot = Some(tray);
            }

            // ── Hide the unused window per mode ──────────────────────────
            if pill_mode_setup {
                // Pill mode: hide the main window
                if let Some(main_win) = app.get_webview_window("main") {
                    let _ = main_win.hide();
                    let _ = main_win.set_skip_taskbar(true);
                }
                // On macOS, set activation policy to Accessory (no Dock icon)
                #[cfg(target_os = "macos")]
                {
                    let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
                }
                // Pill mode: widget stays HIDDEN until activated by global
                // shortcut or tray 'Start Dictation'. Pre-position it now so
                // the first show appears at bottom-center without an
                // animation/frame flicker. Trade-off accepted vs the original
                // 'looks-launch-failed' concern: the tray icon + 'VoiceStudio
                // Dictation' tooltip provide the app-running signal.
                match app.get_webview_window("widget") {
                    Some(win) => {
                        // Defensive: make sure the widget is hidden on startup
                        // regardless of what window-state restored. The denylist
                        // above should handle it, but belt-and-braces.
                        let _ = win.hide();
                        if win.move_window(Position::BottomCenter).is_err() {
                            let _ = win.center();
                        }
                        log::info!("Pill mode: widget window pre-positioned at bottom-center (hidden until activated)");
                    }
                    None => log::error!(
                        "Pill mode: widget window NOT FOUND — get_webview_window(\"widget\") \
                         returned None. Check tauri.conf.json windows[label=\"widget\"]."
                    ),
                }
            } else {
                // Studio mode: widget window stays hidden but ready for the
                // global shortcut. Belt-and-braces hide() in case any plugin
                // or stale state would otherwise show it on startup.
                if let Some(win) = app.get_webview_window("widget") {
                    let _ = win.hide();
                }
                // Enforce the always-open-maximized contract (#881) at
                // runtime: macOS can ignore `maximized: true` from
                // tauri.conf.json at window creation when combined with the
                // Overlay title-bar style, so the config flag alone isn't
                // reliable. maximize() zooms the window — it never enters a
                // fullscreen Space. Guarded by tests/test_window_launch_state.py.
                if let Some(main_win) = app.get_webview_window("main") {
                    if !main_win.is_maximized().unwrap_or(false) {
                        let _ = main_win.maximize();
                    }
                }
            }

            // ── WebView media-capture permissions (mic for dictation) ────
            // BOTH the main window (voice-clone recording) and the dictation
            // widget need this: the widget is a separate WebView with its own
            // permission handling. Previously only "main" was covered on
            // Linux, and Windows had no handler at all — so getUserMedia() in
            // the dictation pill rejected with NotAllowedError even when the
            // OS-level mic permission was granted (#323).
            for label in ["main", "widget"] {
                if let Some(win) = app.get_webview_window(label) {
                    grant_webview_media_permissions(&win);
                }
            }

            // ── Bootstrap ────────────────────────────────────────────────
            let bootstrap_state = BootstrapState {
                stage: Arc::new(Mutex::new(BootstrapStage::Checking)),
                logs: Arc::new(Mutex::new(Vec::new())),
            };
            let stage_handle = bootstrap_state.stage.clone();
            app.manage(bootstrap_state);
            app.manage(BackendState {
                lifecycle: Mutex::new(()),
                process: Mutex::new(None),
                owned_tree: Mutex::new(None),
                attached: AtomicBool::new(false),
                attached_health: Mutex::new(AttachedHealthState::default()),
                spawned_at: Mutex::new(None),
            });

            let app_handle = app.handle().clone();
            std::thread::spawn(move || {
                let skip_spawn = std::env::var("TAURI_SKIP_BACKEND").is_ok();
                if skip_spawn {
                    log::info!("TAURI_SKIP_BACKEND set — not spawning");
                    set_stage(&stage_handle, BootstrapStage::Ready);
                    return;
                }
                // `--setup` re-opens the install-plan screen on demand — it
                // must win over the attach-to-healthy-backend shortcut, or a
                // running backend would skip straight past it.
                if std::env::args().any(|a| a == "--setup") {
                    log::info!("--setup flag — opening the setup screen");
                    set_stage(&stage_handle, BootstrapStage::AwaitingSetup);
                    return;
                }
                // Probe/attach, the first-run gate, spawn, child tracking, and
                // readiness are one serialized lifecycle operation. A Retry
                // arriving during launch waits and attaches instead of
                // creating a second backend on the same port (#1635).
                bootstrap::spawn_initial_backend_and_wait(&app_handle, &stage_handle);
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() != "main" {
                    return;
                }
                let quitting = window
                    .app_handle()
                    .state::<AppFlags>()
                    .quitting
                    .load(Ordering::SeqCst);
                if quitting {
                    return;
                }
                api.prevent_close();
                let _ = window.hide();
                #[cfg(not(target_os = "macos"))]
                {
                    let _ = window.set_skip_taskbar(true);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| match event {
        tauri::RunEvent::ExitRequested { code, api, .. } => {
            if !persistence_exit::handle_exit_requested(app_handle, code, &api) {
                return;
            }
            shutdown_backend_for_exit(app_handle);
        }
        // macOS: clicking the Dock icon while the app has no visible windows
        // fires this (instead of relaunching) via Cocoa's
        // `applicationShouldHandleReopen:hasVisibleWindows:`. CloseRequested
        // (see `on_window_event` above) hides the main window rather than
        // destroying it, so without this arm the click did nothing — the
        // process stayed alive with a live Dock icon and the only way back
        // was the tray's "Show VoiceStudio" item. `show_and_focus_main_window`
        // is the same sequence that item runs, so both paths behave
        // identically.
        #[cfg(target_os = "macos")]
        tauri::RunEvent::Reopen {
            has_visible_windows,
            ..
        } => {
            // Cocoa's `has_visible_windows` is deliberately NOT used: the
            // dictation pill is a separate always-on-top window that sets it
            // to `true` on its own. Ask the main window directly instead.
            // `is_visible()` errors only if the window has gone away, and a
            // redundant show is harmless next to a Dock icon that stays dead,
            // so treat an error as "not visible" and restore.
            let main_visible = app_handle
                .get_webview_window("main")
                .map(|win| win.is_visible().unwrap_or(false))
                .unwrap_or(false);
            if should_restore_on_reopen(main_visible, has_visible_windows) {
                show_and_focus_main_window(app_handle);
            }
        }
        _ => {}
    });
}
