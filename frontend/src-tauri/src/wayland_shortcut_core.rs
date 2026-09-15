//! Wayland global shortcut support through xdg-desktop-portal.
//!
//! `tauri-plugin-global-shortcut` uses `global-hotkey`, whose Linux backend is
//! X11-only. Under XWayland its registration can still return `Ok(())`, but a
//! native Wayland compositor never sends it key events. The portal is the
//! compositor-owned, permission-aware API for this job.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Mutex};
use std::time::Duration;

use std::sync::Arc;
type Callback = Arc<dyn Fn(bool) + Send + Sync>;
use zbus::{
    blocking::{Connection, Proxy},
    zvariant::{OwnedObjectPath, OwnedValue, Str},
};

const DESKTOP_DESTINATION: &str = "org.freedesktop.portal.Desktop";
const DESKTOP_PATH: &str = "/org/freedesktop/portal/desktop";
const GLOBAL_SHORTCUTS_INTERFACE: &str = "org.freedesktop.portal.GlobalShortcuts";
const REQUEST_INTERFACE: &str = "org.freedesktop.portal.Request";
const SESSION_INTERFACE: &str = "org.freedesktop.portal.Session";
const REGISTRY_INTERFACE: &str = "org.freedesktop.host.portal.Registry";
const SHORTCUT_ID: &str = "voice-dictation";
static REQUEST_SEQUENCE: AtomicU64 = AtomicU64::new(1);
const PORTAL_LISTENER_TIMEOUT: Duration = Duration::from_secs(5);
const PORTAL_RESPONSE_TIMEOUT: Duration = Duration::from_secs(60);

type VariantMap = HashMap<String, OwnedValue>;

#[derive(Clone)]
struct PortalRegistration {
    connection: Connection,
    session: OwnedObjectPath,
}

#[derive(Default)]
pub struct PortalShortcutState {
    active: Mutex<Option<PortalRegistration>>,
    revision: AtomicU64,
    identity: DesktopIdentity,
}

impl PortalShortcutState {
    pub fn for_desktop(desktop_id: &'static str, executable: std::path::PathBuf) -> Self {
        Self {
            identity: DesktopIdentity {
                desktop_id,
                executable: Some(executable),
            },
            ..Self::default()
        }
    }
    pub fn close(&self) {
        self.reserve();
        if let Ok(mut active) = self.active.lock() {
            if let Some(previous) = active.take() {
                let _ = close_session(&previous);
            }
        }
    }

    pub fn replace(&self, callback: Callback, accelerator: String) -> Result<String, String> {
        let revision = self.reserve();
        self.replace_reserved(callback, accelerator, revision)
    }

    pub fn reserve(&self) -> u64 {
        self.revision.fetch_add(1, Ordering::SeqCst) + 1
    }

    fn is_current(&self, revision: u64) -> bool {
        self.revision.load(Ordering::SeqCst) == revision
    }

    pub fn replace_reserved(
        &self,
        callback: Callback,
        accelerator: String,
        revision: u64,
    ) -> Result<String, String> {
        // Bind the replacement first. A declined consent dialog or unavailable
        // portal therefore leaves the working shortcut and saved preference
        // untouched.
        let (registration, display) = bind(&accelerator, &self.identity)?;
        if !self.is_current(revision) {
            let _ = close_session(&registration);
            return Err("shortcut registration was superseded by a newer request".into());
        }
        let mut active = match self.active.lock() {
            Ok(active) => active,
            Err(_) => {
                let _ = close_session(&registration);
                return Err("portal shortcut lock poisoned".into());
            }
        };
        if !self.is_current(revision) {
            drop(active);
            let _ = close_session(&registration);
            return Err("shortcut registration was superseded by a newer request".into());
        }
        if let Err(error) = start_listener(callback, registration.clone()) {
            let _ = close_session(&registration);
            return Err(error);
        }
        let previous = active.replace(registration);
        drop(active);
        if let Some(previous) = previous {
            if let Err(error) = close_session(&previous) {
                log::warn!("Could not close the previous Wayland shortcut session: {error}");
            }
        }
        Ok(display)
    }
}

struct DesktopIdentity {
    desktop_id: &'static str,
    executable: Option<std::path::PathBuf>,
}
impl Default for DesktopIdentity {
    fn default() -> Self {
        Self {
            desktop_id: "com.debpalash.omnivoice-studio",
            executable: None,
        }
    }
}

fn user_entry_path(desktop_id: &str) -> Option<std::path::PathBuf> {
    dirs_next::data_dir().map(|dir| {
        dir.join("applications")
            .join(format!("{desktop_id}.desktop"))
    })
}

/// A packaged (system-dir) entry — deb installs manage their own; never touch.
fn system_entry_exists(desktop_id: &str) -> bool {
    let filename = format!("{desktop_id}.desktop");
    std::env::var_os("XDG_DATA_DIRS")
        .map(|dirs| {
            std::env::split_paths(&dirs)
                .any(|dir| dir.join("applications").join(&filename).is_file())
        })
        .unwrap_or_else(|| {
            ["/usr/local/share", "/usr/share"].iter().any(|dir| {
                std::path::Path::new(dir)
                    .join("applications")
                    .join(&filename)
                    .is_file()
            })
        })
}

/// The `[Desktop Entry]` group's Exec target, unquoted. `None` when the main
/// group has no usable Exec line — which GLib treats the same as a missing
/// program. Scoped to the main group deliberately: a `[Desktop Action …]`
/// group carries its own `Exec=`, and accepting it would retain an entry GLib
/// still cannot resolve (CodeRabbit, #1526).
fn entry_exec_target(content: &str) -> Option<std::path::PathBuf> {
    let mut in_main_group = false;
    let mut exec = None;
    for line in content.lines() {
        let line = line.trim_start();
        if line.starts_with('[') {
            in_main_group = line == "[Desktop Entry]";
            continue;
        }
        if in_main_group {
            if let Some(value) = line.strip_prefix("Exec=") {
                exec = Some(value);
                break;
            }
        }
    }
    let raw = exec?.trim();
    let unquoted = raw
        .strip_prefix('"')
        .and_then(|rest| rest.split('"').next())
        .unwrap_or_else(|| raw.split_whitespace().next().unwrap_or(raw));
    if unquoted.is_empty() {
        return None;
    }
    Some(std::path::PathBuf::from(unquoted))
}

/// Whether a user-local identity entry must be rewritten before the portal
/// will accept it.
///
/// GLib refuses to resolve a desktop entry whose Exec program does not exist
/// (`GDesktopAppInfo` returns NULL), and the portal then rejects the bind with
/// "App info not found" — the shortcut silently dies for the whole session.
/// A dev entry pointing at a `target/debug` binary goes stale exactly this
/// way: a `cargo clean`, a moved checkout, or anything that relocates the
/// binary breaks system-wide dictation with only a log line to show for it.
fn entry_needs_rewrite(content: &str, exec_exists: impl Fn(&std::path::Path) -> bool) -> bool {
    match entry_exec_target(content) {
        Some(target) => !exec_exists(&target),
        None => true,
    }
}

fn desktop_exec_path() -> Result<std::path::PathBuf, String> {
    // AppImage's current_exe() points inside its transient mount. APPIMAGE is
    // the stable launcher path the desktop entry must retain.
    if let Some(path) = std::env::var_os("APPIMAGE").filter(|path| !path.is_empty()) {
        return Ok(path.into());
    }
    std::env::current_exe().map_err(|error| format!("could not locate VoiceStudio: {error}"))
}

fn desktop_exec_value(path: &std::path::Path) -> String {
    let escaped = path
        .to_string_lossy()
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('`', "\\`")
        .replace('$', "\\$");
    format!("\"{escaped}\"")
}

/// The host portal resolves un-sandboxed apps through their desktop entry.
/// Deb packages already install one; dev builds and standalone AppImages may
/// not. Add an invisible identity entry only when none exists.
fn ensure_desktop_identity(identity: &DesktopIdentity) -> Result<(), String> {
    if system_entry_exists(identity.desktop_id) {
        return Ok(());
    }
    let path =
        user_entry_path(identity.desktop_id).ok_or("could not locate the user data directory")?;
    if let Ok(existing) = std::fs::read_to_string(&path) {
        if !entry_needs_rewrite(&existing, |target| target.exists()) {
            return Ok(());
        }
        // Stale: GLib returns NULL for an entry whose Exec is gone, and the
        // portal then refuses the bind ("App info not found"). Rewrite with
        // where the app actually is NOW. The user dir with our app id is ours
        // to manage — packaged entries live in the system dirs handled above.
        log::info!(
            "Wayland portal identity at {} points at a missing program — rewriting",
            path.display()
        );
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("could not create applications directory: {error}"))?;
    }
    let entry = format!(
        "[Desktop Entry]\nType=Application\nName=VoiceStudio\nExec={}\nTerminal=false\nNoDisplay=true\nStartupWMClass=VoiceStudio\nX-VoiceStudio-Generated=true\n",
        desktop_exec_value(&identity.executable.clone().map(Ok).unwrap_or_else(desktop_exec_path)?)
    );
    std::fs::write(&path, entry)
        .map_err(|error| format!("could not create {}: {error}", path.display()))?;
    log::info!("Installed Wayland portal identity at {}", path.display());
    Ok(())
}

pub fn is_wayland_session() -> bool {
    std::env::var("XDG_SESSION_TYPE")
        .map(|kind| kind.eq_ignore_ascii_case("wayland"))
        .unwrap_or(false)
        || std::env::var_os("WAYLAND_DISPLAY").is_some()
}

/// Convert Tauri's cross-platform accelerator spelling to the portal format.
/// The portal may still let the user choose a different chord in its consent
/// dialog, so an unknown spelling is deliberately omitted rather than guessed.
fn portal_trigger(accelerator: &str) -> Option<String> {
    let mut modifiers: Vec<String> = Vec::new();
    let mut key = None;

    for part in accelerator
        .split('+')
        .map(str::trim)
        .filter(|part| !part.is_empty())
    {
        match part.to_ascii_lowercase().as_str() {
            "cmdorctrl" | "commandorcontrol" | "ctrl" | "control" => {
                if !modifiers.iter().any(|modifier| modifier == "CTRL") {
                    modifiers.push("CTRL".into());
                }
            }
            "shift" => modifiers.push("SHIFT".into()),
            "alt" | "option" => modifiers.push("ALT".into()),
            "cmd" | "command" | "super" | "meta" => modifiers.push("LOGO".into()),
            _ if key.is_none() => key = xkb_key_name(part),
            _ => return None,
        }
    }

    let key = key?;
    if modifiers.is_empty() {
        return None;
    }
    modifiers.push(key);
    Some(modifiers.join("+"))
}

fn xkb_key_name(key: &str) -> Option<String> {
    let lower = key.to_ascii_lowercase();
    if let Some(letter) = lower.strip_prefix("key") {
        if letter.len() == 1
            && letter
                .chars()
                .all(|character| character.is_ascii_alphabetic())
        {
            return Some(letter.to_owned());
        }
    }
    if let Some(digit) = lower.strip_prefix("digit") {
        if digit.len() == 1 && digit.chars().all(|character| character.is_ascii_digit()) {
            return Some(digit.to_owned());
        }
    }
    if lower.len() == 1
        && lower
            .chars()
            .all(|character| character.is_ascii_alphanumeric())
    {
        return Some(lower);
    }
    Some(
        match lower.as_str() {
            "space" => "space",
            "enter" | "return" => "Return",
            "escape" | "esc" => "Escape",
            "tab" => "Tab",
            "backspace" => "BackSpace",
            "delete" => "Delete",
            "insert" => "Insert",
            "home" => "Home",
            "end" => "End",
            "pageup" => "Page_Up",
            "pagedown" => "Page_Down",
            "arrowup" | "up" => "Up",
            "arrowdown" | "down" => "Down",
            "arrowleft" | "left" => "Left",
            "arrowright" | "right" => "Right",
            "minus" => "minus",
            "equal" => "equal",
            "comma" => "comma",
            "period" => "period",
            "slash" => "slash",
            "semicolon" => "semicolon",
            "quote" | "apostrophe" => "apostrophe",
            "bracketleft" => "bracketleft",
            "bracketright" => "bracketright",
            "backslash" => "backslash",
            "backquote" | "grave" => "grave",
            _ if lower.strip_prefix('f').is_some_and(|digits| {
                digits
                    .parse::<u8>()
                    .is_ok_and(|number| (1..=35).contains(&number))
            }) =>
            {
                return Some(key.to_ascii_uppercase());
            }
            _ => return None,
        }
        .into(),
    )
}

fn variant_string(value: &str) -> OwnedValue {
    OwnedValue::from(Str::from(value))
}

fn trigger_description(shortcuts: Vec<(String, VariantMap)>) -> Option<String> {
    shortcuts
        .into_iter()
        .find(|(id, _)| id == SHORTCUT_ID)
        .and_then(|(_, mut properties)| properties.remove("trigger_description"))
        .and_then(|value| String::try_from(value).ok())
        .filter(|description| !description.trim().is_empty())
}

fn request_path(connection: &Connection, token: &str) -> Result<OwnedObjectPath, String> {
    let sender = connection
        .unique_name()
        .ok_or("session bus did not assign a unique name")?
        .as_str()
        .trim_start_matches(':')
        .replace('.', "_");
    OwnedObjectPath::try_from(format!("{DESKTOP_PATH}/request/{sender}/{token}"))
        .map_err(|error| format!("invalid portal request path: {error}"))
}

fn response_for<F>(connection: &Connection, token: &str, call: F) -> Result<VariantMap, String>
where
    F: FnOnce() -> Result<OwnedObjectPath, zbus::Error>,
{
    // Subscribe before making the request: a fast portal is allowed to answer
    // immediately after returning the request handle.
    let expected = request_path(connection, token)?;
    let listener_connection = connection.clone();
    let listener_path = expected.clone();
    let (ready_tx, ready_rx) = mpsc::sync_channel(1);
    let (response_tx, response_rx) = mpsc::sync_channel(1);
    std::thread::Builder::new()
        .name("wayland-portal-response".into())
        .spawn(move || {
            let request = match Proxy::new(
                &listener_connection,
                DESKTOP_DESTINATION,
                listener_path.as_str(),
                REQUEST_INTERFACE,
            ) {
                Ok(request) => request,
                Err(error) => {
                    let _ = ready_tx.send(Err(format!("portal request listener: {error}")));
                    return;
                }
            };
            let mut responses = match request.receive_signal("Response") {
                Ok(responses) => responses,
                Err(error) => {
                    let _ = ready_tx.send(Err(format!("portal response listener: {error}")));
                    return;
                }
            };
            if ready_tx.send(Ok(())).is_err() {
                return;
            }
            let response = responses
                .next()
                .ok_or_else(|| "portal closed before answering the shortcut request".to_string());
            let _ = response_tx.send(response);
        })
        .map_err(|error| format!("could not start the portal response listener: {error}"))?;
    receive_with_timeout(
        &ready_rx,
        PORTAL_LISTENER_TIMEOUT,
        "portal response listener",
    )?;

    let returned = call().map_err(|error| format!("portal request failed: {error}"))?;
    if returned != expected {
        return Err(format!(
            "portal returned unexpected request path {returned} (expected {expected})"
        ));
    }

    let message = match receive_with_timeout(
        &response_rx,
        PORTAL_RESPONSE_TIMEOUT,
        "portal shortcut request",
    ) {
        Ok(message) => message,
        Err(error) => {
            if let Ok(request) = Proxy::new(
                connection,
                DESKTOP_DESTINATION,
                expected.as_str(),
                REQUEST_INTERFACE,
            ) {
                let _ = request.call::<_, _, ()>("Close", &());
            }
            return Err(error);
        }
    };
    let (code, results): (u32, VariantMap) = message
        .body()
        .deserialize()
        .map_err(|error| format!("invalid portal response: {error}"))?;
    if code != 0 {
        return Err(format!(
            "portal shortcut request was declined (response {code})"
        ));
    }
    Ok(results)
}

fn receive_with_timeout<T>(
    receiver: &mpsc::Receiver<Result<T, String>>,
    timeout: Duration,
    operation: &str,
) -> Result<T, String> {
    match receiver.recv_timeout(timeout) {
        Ok(result) => result,
        Err(mpsc::RecvTimeoutError::Timeout) => Err(format!(
            "{operation} timed out after {} seconds",
            timeout.as_secs()
        )),
        Err(mpsc::RecvTimeoutError::Disconnected) => {
            Err(format!("{operation} stopped before completing"))
        }
    }
}

fn bind(
    accelerator: &str,
    identity: &DesktopIdentity,
) -> Result<(PortalRegistration, String), String> {
    ensure_desktop_identity(identity)?;
    let connection = Connection::session()
        .map_err(|error| format!("could not connect to the desktop portal: {error}"))?;

    // GNOME's host portal uses the installed desktop entry to associate this
    // un-sandboxed process with its desktop id.
    let registry = Proxy::new(
        &connection,
        DESKTOP_DESTINATION,
        DESKTOP_PATH,
        REGISTRY_INTERFACE,
    )
    .map_err(|error| format!("could not open the portal registry: {error}"))?;
    let registry_options: VariantMap = HashMap::new();
    if let Err(error) =
        registry.call::<_, _, ()>("Register", &(identity.desktop_id, registry_options))
    {
        // Development builds and portable AppImages may not have a desktop
        // entry for the host registry to resolve. Portal v1 does not require
        // this handshake, so continue and let CreateSession be authoritative.
        log::warn!("Wayland portal host registration skipped: {error}");
    }

    let portal = Proxy::new(
        &connection,
        DESKTOP_DESTINATION,
        DESKTOP_PATH,
        GLOBAL_SHORTCUTS_INTERFACE,
    )
    .map_err(|error| format!("could not open the global-shortcuts portal: {error}"))?;

    let process = std::process::id();
    let sequence = REQUEST_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let create_token = format!("vs_create_{process}_{sequence}");
    let session_token = format!("vs_session_{process}_{sequence}");
    let mut create_options = VariantMap::new();
    create_options.insert("handle_token".into(), variant_string(&create_token));
    create_options.insert(
        "session_handle_token".into(),
        variant_string(&session_token),
    );
    let mut create_results = response_for(&connection, &create_token, || {
        portal.call("CreateSession", &(create_options,))
    })?;
    let session_value = create_results
        .remove("session_handle")
        .ok_or("portal did not return a shortcut session")?;
    // The portal specification declares an object path, but deployed portal
    // versions historically returned a string. Accept both wire formats.
    let session = match session_value
        .try_clone()
        .ok()
        .and_then(|value| OwnedObjectPath::try_from(value).ok())
    {
        Some(path) => path,
        None => {
            let path = String::try_from(session_value).map_err(|error| {
                format!("portal returned an invalid shortcut session handle: {error}")
            })?;
            OwnedObjectPath::try_from(path)
                .map_err(|error| format!("portal returned an invalid session path: {error}"))?
        }
    };

    let mut shortcut_info = VariantMap::new();
    shortcut_info.insert("description".into(), variant_string("VoiceStudio"));
    if let Some(trigger) = portal_trigger(&accelerator) {
        shortcut_info.insert("preferred_trigger".into(), variant_string(&trigger));
    }
    let shortcuts = vec![(SHORTCUT_ID.to_string(), shortcut_info)];
    let bind_token = format!("vs_bind_{process}_{sequence}");
    let mut bind_options = VariantMap::new();
    bind_options.insert("handle_token".into(), variant_string(&bind_token));
    let mut bind_results = response_for(&connection, &bind_token, || {
        portal.call(
            "BindShortcuts",
            &(session.clone(), shortcuts, "", bind_options),
        )
    })?;

    let display = bind_results
        .remove("shortcuts")
        .and_then(|value| Vec::<(String, VariantMap)>::try_from(value).ok())
        .and_then(trigger_description)
        .unwrap_or_else(|| display_accelerator(accelerator));

    drop(portal);
    drop(registry);
    Ok((
        PortalRegistration {
            connection,
            session,
        },
        display,
    ))
}

fn close_session(registration: &PortalRegistration) -> Result<(), String> {
    let session = Proxy::new(
        &registration.connection,
        DESKTOP_DESTINATION,
        registration.session.as_str(),
        SESSION_INTERFACE,
    )
    .map_err(|error| format!("could not open the shortcut session: {error}"))?;
    session
        .call::<_, _, ()>("Close", &())
        .map_err(|error| format!("could not close the shortcut session: {error}"))
}

/// Read the session handle and shortcut id out of an `Activated`/`Deactivated`
/// signal.
///
/// The portal declares `(o session_handle, s shortcut_id, t timestamp,
/// a{sv} options)` — the timestamp is **64-bit**. Deserializing the body into a
/// `u32` field fails zbus' signature check, so every key press was discarded as
/// an invalid signal and dictation never started on any Wayland compositor. The
/// 32-bit spelling stays as a fallback so a non-conforming portal degrades to
/// working rather than to silence.
fn shortcut_signal_target(message: &zbus::Message) -> Result<(OwnedObjectPath, String), String> {
    let body = message.body();
    if let Ok((session, shortcut_id, _timestamp, _options)) =
        body.deserialize::<(OwnedObjectPath, String, u64, VariantMap)>()
    {
        return Ok((session, shortcut_id));
    }
    body.deserialize::<(OwnedObjectPath, String, u32, VariantMap)>()
        .map(|(session, shortcut_id, _timestamp, _options)| (session, shortcut_id))
        .map_err(|error| error.to_string())
}

fn listen(callback: Callback, registration: PortalRegistration) -> Result<(), String> {
    let portal = Proxy::new(
        &registration.connection,
        DESKTOP_DESTINATION,
        DESKTOP_PATH,
        GLOBAL_SHORTCUTS_INTERFACE,
    )
    .map_err(|error| format!("could not open the global-shortcuts portal: {error}"))?;

    log::info!("Wayland dictation shortcut registered through xdg-desktop-portal");
    let mut signals = portal
        .receive_all_signals()
        .map_err(|error| format!("could not listen for portal shortcuts: {error}"))?;
    for message in &mut signals {
        let header = message.header();
        let member = header
            .member()
            .map(|name| name.as_str().to_owned())
            .unwrap_or_default();
        if member != "Activated" && member != "Deactivated" {
            continue;
        }
        let (signal_session, shortcut_id) = match shortcut_signal_target(&message) {
            Ok(target) => target,
            Err(error) => {
                log::warn!("Invalid Wayland shortcut signal: {error}");
                continue;
            }
        };
        if signal_session != registration.session || shortcut_id != SHORTCUT_ID {
            continue;
        }
        if member == "Activated" {
            log::info!("Wayland shortcut pressed: dictation start");
            callback(true);
        } else {
            log::info!("Wayland shortcut released: dictation stop");
            callback(false);
        }
    }
    Err("global-shortcuts portal closed the session".into())
}

fn start_listener(callback: Callback, registration: PortalRegistration) -> Result<(), String> {
    std::thread::Builder::new()
        .name("wayland-global-shortcut".into())
        .spawn(move || {
            if let Err(error) = listen(callback, registration) {
                log::info!("Wayland shortcut listener stopped: {error}");
            }
        })
        .map(|_| ())
        .map_err(|error| format!("failed to start Wayland shortcut listener: {error}"))
}

#[cfg(test)]
mod tests {
    use super::{
        desktop_exec_value, portal_trigger, receive_with_timeout, shortcut_signal_target,
        trigger_description, variant_string, PortalShortcutState, VariantMap,
        GLOBAL_SHORTCUTS_INTERFACE, SHORTCUT_ID,
    };
    use std::collections::HashMap;
    use std::path::Path;
    use std::sync::mpsc;
    use std::time::Duration;
    use zbus::zvariant::OwnedObjectPath;

    #[test]
    fn converts_tauri_accelerators_to_portal_triggers() {
        assert_eq!(
            portal_trigger("CmdOrCtrl+Shift+Space").as_deref(),
            Some("CTRL+SHIFT+space")
        );
        assert_eq!(
            portal_trigger("Alt+Control+K").as_deref(),
            Some("ALT+CTRL+k")
        );
        assert_eq!(
            portal_trigger("Super+PageUp").as_deref(),
            Some("LOGO+Page_Up")
        );
        assert_eq!(
            portal_trigger("Ctrl+BracketLeft").as_deref(),
            Some("CTRL+bracketleft")
        );
        assert_eq!(portal_trigger("Cmd+Digit1").as_deref(), Some("LOGO+1"));
    }

    #[test]
    fn rejects_modifier_free_or_ambiguous_accelerators() {
        assert_eq!(portal_trigger("Space"), None);
        assert_eq!(portal_trigger("Ctrl+K+L"), None);
    }

    #[test]
    fn a_stale_identity_entry_is_rewritten() {
        // The class from 2026-08-13: the entry's Exec pointed at a binary that
        // had been moved. GLib then resolves the entry to NULL and the portal
        // refuses the bind with "App info not found" — system-wide dictation
        // silently dead for the whole session.
        let stale = "[Desktop Entry]\nType=Application\nExec=/gone/omnivoice-studio\n";
        assert!(super::entry_needs_rewrite(stale, |_| false));

        let healthy = "[Desktop Entry]\nType=Application\nExec=\"/opt/VoiceStudio.AppImage\"\n";
        assert!(!super::entry_needs_rewrite(healthy, |path| {
            path == std::path::Path::new("/opt/VoiceStudio.AppImage")
        }));
    }

    #[test]
    fn exec_targets_parse_quoted_legacy_and_missing_lines() {
        use super::entry_exec_target;
        // Current writer: quoted.
        assert_eq!(
            entry_exec_target("[Desktop Entry]\nExec=\"/tmp/Voice Studio/app\"\n").as_deref(),
            Some(std::path::Path::new("/tmp/Voice Studio/app"))
        );
        // Pre-quoting entries from older builds still parse.
        assert_eq!(
            entry_exec_target("[Desktop Entry]\nExec=/home/u/target/debug/omnivoice-studio\n")
                .as_deref(),
            Some(std::path::Path::new(
                "/home/u/target/debug/omnivoice-studio"
            ))
        );
        // No Exec at all resolves to NULL in GLib — treat as needing rewrite.
        assert_eq!(
            entry_exec_target("[Desktop Entry]\nType=Application\n"),
            None
        );
        assert!(super::entry_needs_rewrite("[Desktop Entry]\n", |_| true));
        // An action group's Exec is NOT the entry's Exec: GLib still resolves
        // the entry to NULL without a main-group Exec, so accepting this would
        // keep exactly the stale entry the rewrite exists to replace.
        let action_only =
            "[Desktop Entry]\nType=Application\n[Desktop Action new]\nExec=/bin/true\n";
        assert_eq!(entry_exec_target(action_only), None);
        assert!(super::entry_needs_rewrite(action_only, |_| true));
    }

    #[test]
    fn desktop_exec_paths_are_quoted_and_escaped() {
        assert_eq!(
            desktop_exec_value(Path::new("/tmp/Voice Studio/$build")),
            "\"/tmp/Voice Studio/\\$build\""
        );
    }

    #[test]
    fn uses_the_portals_effective_trigger_description() {
        let mut properties = HashMap::new();
        properties.insert("trigger_description".into(), variant_string("Meta+Shift+V"));
        assert_eq!(
            trigger_description(vec![("voice-dictation".into(), properties)]).as_deref(),
            Some("Meta+Shift+V")
        );
    }

    #[test]
    fn newer_rebinds_supersede_in_flight_registration() {
        let state = PortalShortcutState::default();
        let startup = state.reserve();
        let changed = state.reserve();
        assert!(!state.is_current(startup));
        assert!(state.is_current(changed));
    }

    fn shortcut_signal<T>(timestamp: T) -> zbus::Message
    where
        T: serde::Serialize + zbus::zvariant::Type,
    {
        let session =
            OwnedObjectPath::try_from("/org/freedesktop/portal/desktop/session/1").unwrap();
        zbus::Message::signal(super::DESKTOP_PATH, GLOBAL_SHORTCUTS_INTERFACE, "Activated")
            .unwrap()
            .build(&(session, SHORTCUT_ID, timestamp, VariantMap::new()))
            .unwrap()
    }

    /// The portal spells the timestamp `t`; a `u32` field made zbus reject every
    /// signal, which silently killed Wayland dictation.
    #[test]
    fn reads_portal_signals_with_a_64_bit_timestamp() {
        let message = shortcut_signal(1_786_563_484_746_u64);
        let (session, shortcut_id) = shortcut_signal_target(&message)
            .expect("64-bit timestamps are the portal's declared spelling");
        assert_eq!(
            session.as_str(),
            "/org/freedesktop/portal/desktop/session/1"
        );
        assert_eq!(shortcut_id, SHORTCUT_ID);
    }

    #[test]
    fn still_reads_a_32_bit_timestamp_from_a_nonconforming_portal() {
        let message = shortcut_signal(42_u32);
        let (_session, shortcut_id) = shortcut_signal_target(&message)
            .expect("a 32-bit timestamp must not drop the key press");
        assert_eq!(shortcut_id, SHORTCUT_ID);
    }

    #[test]
    fn portal_response_wait_is_bounded() {
        let (_sender, receiver) = mpsc::channel::<Result<(), String>>();
        let error = receive_with_timeout(
            &receiver,
            Duration::from_millis(1),
            "portal shortcut request",
        )
        .unwrap_err();
        assert!(error.contains("timed out"));
    }
}

fn display_accelerator(accelerator: &str) -> String {
    accelerator
        .split('+')
        .map(|part| match part.to_ascii_lowercase().as_str() {
            "cmdorctrl" | "commandorcontrol" | "ctrl" | "control" => "Ctrl",
            "cmd" | "command" | "meta" | "super" => "Super",
            "alt" | "option" => "Alt",
            "shift" => "Shift",
            _ => part,
        })
        .collect::<Vec<_>>()
        .join("+")
}
