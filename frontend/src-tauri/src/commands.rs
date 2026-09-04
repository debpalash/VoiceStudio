//! Tauri IPC commands: sysinfo, logs, HF cache, paste, tray, quit, dictation shortcut.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::image::Image;
use tauri::{Emitter, Manager};
use tauri_plugin_dialog::DialogExt;

use crate::config::{load_config, save_config};
use crate::dictation_shortcut::{update_tray_hint, DictationShortcutManager, ShortcutInfo};
use crate::{AppFlags, TrayHandle};
use crate::{TRAY_ICON_DEFAULT, TRAY_ICON_RECORDING};

// ── Native host-path authorization ───────────────────────────────────────

#[derive(Serialize, Deserialize)]
struct AuthorizedHostPath {
    token: String,
    kind: String,
    path: String,
}

#[derive(Serialize)]
pub struct AuthorizedPathSelection {
    authorization: String,
    path: String,
}

/// Directory for one-shot host-path capability files (and the
/// paired `revealed-paths` ledger) — must agree with what the *running
/// backend* resolves in `backend/core/path_authorization.py`, since the
/// backend is what reads these tokens back over loopback HTTP.
///
/// Prefers the backend's own advertised `data_dir` (`GET /system/info`, see
/// `backend::backend_data_dir`) so the two processes cannot disagree; falls
/// back to Tauri's own resolution (`setup::resolved_data_dir` / historical
/// behavior) when the backend isn't reachable yet, e.g. very early startup.
/// See #1781 for the split this closes: a dev backend spawned without
/// `OMNIVOICE_*` env, or a custom data folder / portable mode applied after
/// the backend already started, previously left Tauri writing capability
/// files the backend could never find, 403ing every export.
pub fn path_authorization_dir<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> PathBuf {
    authorization_dir_from(crate::backend::backend_data_dir(crate::backend_port()), || {
        crate::setup::resolved_data_dir(app).unwrap_or_else(crate::setup::default_data_dir)
    })
}

/// The resolution rule itself, with the two inputs passed in rather than
/// fetched, so it is unit-testable without an `AppHandle`.
///
/// Constructing one in a test (`tauri::test::mock_builder`) aborts the whole
/// test binary on the Windows CI runner — it exits before the harness prints
/// a single line — and nothing else in this crate builds a Tauri app in a
/// unit test. Keeping the decision in a plain function means the branch that
/// matters for #1781 is covered on every platform, and the wrapper above is
/// left as two argument expressions with no logic of its own.
fn authorization_dir_from(
    advertised: Option<String>,
    tauri_fallback: impl FnOnce() -> PathBuf,
) -> PathBuf {
    advertised
        .map(PathBuf::from)
        .unwrap_or_else(tauri_fallback)
        .join(".path-authorizations")
}

fn remember_reveal_path<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    path: &Path,
) -> Result<(), String> {
    let dir = path_authorization_dir(app);
    fs::create_dir_all(&dir).map_err(|e| format!("Could not create authorization store: {e}"))?;
    let ledger = dir.join("revealed-paths");
    let selected = path.to_string_lossy().into_owned();
    let mut paths: Vec<String> = fs::read_to_string(&ledger)
        .unwrap_or_default()
        .lines()
        .map(str::to_owned)
        .collect();
    paths.retain(|item| item != &selected);
    paths.push(selected);
    if paths.len() > 1024 {
        paths.drain(..paths.len() - 1024);
    }
    fs::write(&ledger, format!("{}\n", paths.join("\n")))
        .map_err(|e| format!("Could not remember selected path: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&ledger, fs::Permissions::from_mode(0o600))
            .map_err(|e| format!("Could not protect selected paths: {e}"))?;
    }
    Ok(())
}

fn reveal_path_is_authorized<R: tauri::Runtime>(app: &tauri::AppHandle<R>, target: &Path) -> bool {
    if let Ok(data_root) = fs::canonicalize(
        crate::setup::resolved_data_dir(app).unwrap_or_else(crate::setup::default_data_dir),
    ) {
        if target.starts_with(data_root) {
            return true;
        }
    }
    let Ok(ledger) = fs::read_to_string(path_authorization_dir(app).join("revealed-paths")) else {
        return false;
    };
    ledger.lines().any(|selected| {
        fs::canonicalize(selected)
            .map(|remembered| remembered == target)
            .unwrap_or(false)
    })
}

fn validate_host_path(kind: &str, path: PathBuf) -> Result<PathBuf, String> {
    if !matches!(
        kind,
        "models_dir" | "ffmpeg" | "ffprobe" | "dub_export" | "soni_input" | "soni_output_dir"
    ) {
        return Err("Unsupported host-path capability".into());
    }
    if path.to_string_lossy().chars().any(|c| c.is_control()) {
        return Err("Path contains invalid control characters".into());
    }
    if kind == "models_dir" && path.as_os_str().is_empty() {
        return Ok(PathBuf::new()); // explicit reset to the platform default
    }
    if !path.is_absolute() {
        return Err("Path must be absolute".into());
    }
    if matches!(kind, "models_dir" | "soni_output_dir") {
        fs::create_dir_all(&path).map_err(|e| format!("Directory is not writable: {e}"))?;
        let probe = path.join(".voicestudio-write-test");
        fs::write(&probe, b"ok").map_err(|e| format!("Directory is not writable: {e}"))?;
        let _ = fs::remove_file(probe);
    } else if kind == "dub_export" {
        let parent = path
            .parent()
            .ok_or_else(|| "Save destination must have a parent directory".to_string())?;
        if !parent.is_dir() {
            return Err("Save destination directory does not exist".into());
        }
    } else if kind == "soni_input" {
        if !path.is_file() {
            return Err("Selected media input is not a file".into());
        }
    } else {
        if !path.is_file() {
            return Err("Selected media tool is not a file".into());
        }
        let mut child = crate::tools::no_window(
            std::process::Command::new(&path)
                .arg("-version")
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped()),
        )
        .spawn()
        .map_err(|e| format!("Selected media tool could not run: {e}"))?;
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            match child.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(25));
                }
                Ok(None) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("Selected media tool did not respond within 5 seconds".into());
                }
                Err(e) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(format!("Selected media tool could not be checked: {e}"));
                }
            }
        }
        let output = child
            .wait_with_output()
            .map_err(|e| format!("Selected media tool output could not be read: {e}"))?;
        let version_text = format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        )
        .to_ascii_lowercase();
        if !output.status.success() || !version_text.contains(kind) {
            return Err("Selected media tool failed its version check".into());
        }
    }
    Ok(path)
}

#[tauri::command]
pub async fn authorize_host_path(
    app: tauri::AppHandle,
    kind: String,
    suggested_name: Option<String>,
    reset: Option<bool>,
) -> Result<Option<AuthorizedPathSelection>, String> {
    let selected = if kind == "models_dir" && reset.unwrap_or(false) {
        Some(PathBuf::new())
    } else {
        let dialog = app.dialog().file();
        let picked = match kind.as_str() {
            "models_dir" | "soni_output_dir" => dialog.blocking_pick_folder(),
            "ffmpeg" | "ffprobe" | "soni_input" => dialog.blocking_pick_file(),
            "dub_export" => {
                let mut save = app.dialog().file();
                if let Some(name) = suggested_name.as_deref() {
                    save = save.set_file_name(name);
                }
                save.blocking_save_file()
            }
            _ => return Err("Unsupported host-path capability".into()),
        };
        picked.and_then(|value| value.into_path().ok())
    };
    let Some(selected) = selected else {
        return Ok(None);
    };
    let validated = validate_host_path(&kind, selected)?;
    let mut random = [0_u8; 32];
    getrandom::fill(&mut random).map_err(|e| format!("Secure randomness unavailable: {e}"))?;
    let token: String = random.iter().map(|b| format!("{b:02x}")).collect();
    let dir = path_authorization_dir(&app);
    fs::create_dir_all(&dir).map_err(|e| format!("Could not create authorization store: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o700))
            .map_err(|e| format!("Could not protect authorization store: {e}"))?;
    }
    let target = dir.join(format!("{token}.json"));
    let payload = AuthorizedHostPath {
        token: token.clone(),
        kind,
        path: validated.to_string_lossy().into_owned(),
    };
    fs::write(
        &target,
        serde_json::to_vec(&payload).map_err(|e| e.to_string())?,
    )
    .map_err(|e| format!("Could not authorize path: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&target, fs::Permissions::from_mode(0o600))
            .map_err(|e| format!("Could not protect authorization: {e}"))?;
    }
    if payload.kind == "dub_export" {
        remember_reveal_path(&app, &validated)?;
    }
    Ok(Some(AuthorizedPathSelection {
        authorization: token,
        path: validated.to_string_lossy().into_owned(),
    }))
}

#[cfg(test)]
mod host_path_authorization_tests {
    use super::validate_host_path;
    use std::path::PathBuf;

    #[test]
    fn rejects_unknown_relative_and_control_character_paths() {
        assert!(validate_host_path("shell", PathBuf::from("/tmp/tool")).is_err());
        assert!(validate_host_path("models_dir", PathBuf::from("relative/models")).is_err());
        assert!(validate_host_path("models_dir", PathBuf::from("/tmp/bad\npath")).is_err());
    }

    #[test]
    fn empty_models_path_is_the_authorized_default_reset() {
        assert_eq!(
            validate_host_path("models_dir", PathBuf::new()).unwrap(),
            PathBuf::new()
        );
    }

    #[test]
    fn dub_export_accepts_only_absolute_paths_in_existing_directories() {
        let parent = std::env::temp_dir();
        let destination = parent.join("voicestudio-authorized-export.wav");
        assert_eq!(
            validate_host_path("dub_export", destination.clone()).unwrap(),
            destination,
        );
        assert!(validate_host_path("dub_export", PathBuf::from("relative/export.wav")).is_err());
        assert!(
            validate_host_path("dub_export", parent.join("missing-directory/export.wav"),).is_err()
        );
    }

    /// Regression for #1781: a native save dialog succeeded and Tauri wrote
    /// the one-shot capability file, but the backend 403'd every export
    /// because it scanned a DIFFERENT `.path-authorizations` directory (its
    /// own `DATA_DIR`, resolved independently — e.g. the dev backend spawned
    /// without `OMNIVOICE_*` env, or a custom data folder / portable mode
    /// applied after the backend already started). When a backend is
    /// reachable, its advertised `data_dir` must win so the two processes
    /// are structurally unable to disagree.
    ///
    /// Exercised through `authorization_dir_from` rather than
    /// `path_authorization_dir`: the wrapper needs an `AppHandle`, and
    /// building one in a unit test aborts the entire test binary on the
    /// Windows runner. The HTTP side (`backend::backend_data_dir`, including
    /// its absolute-path filter) has its own tests in `backend.rs`.
    #[test]
    fn prefers_the_running_backends_advertised_data_dir() {
        // Platform-appropriate absolute fixture: a Unix-style path is NOT
        // absolute on Windows (no drive prefix), and `PathBuf::join`
        // normalizes separators per platform.
        #[cfg(windows)]
        let advertised = r"C:\backend\advertised\data";
        #[cfg(not(windows))]
        let advertised = "/backend/advertised/data";

        let dir = super::authorization_dir_from(Some(advertised.to_string()), || {
            panic!("must not fall back to Tauri's resolution while a backend advertises a dir")
        });

        assert_eq!(
            dir,
            PathBuf::from(advertised).join(".path-authorizations"),
            "must write into the backend's own data_dir, not Tauri's independent resolution"
        );
    }

    /// When no backend answers (unreachable, not started yet, or advertising
    /// something unusable), the resolver must fall back to Tauri's own
    /// resolution — the pre-#1781 behavior — rather than erroring or writing
    /// somewhere unpredictable.
    #[test]
    fn falls_back_to_tauris_own_resolution_when_the_backend_is_unreachable() {
        let fallback = std::env::temp_dir().join("voicestudio-fallback-fixture");

        let dir = super::authorization_dir_from(None, || fallback.clone());

        assert_eq!(dir, fallback.join(".path-authorizations"));
    }
}

// ── System metrics ────────────────────────────────────────────────────────

#[derive(Serialize, Clone)]
pub struct SysinfoPayload {
    cpu: f64,
    ram: f64,
    total_ram: f64,
    vram: f64,
    gpu_active: bool,
}

#[tauri::command]
pub fn get_sysinfo() -> SysinfoPayload {
    use sysinfo::System;

    let mut sys = System::new();
    sys.refresh_cpu_usage();
    sys.refresh_memory();

    let cpu = sys.global_cpu_usage() as f64;
    let ram = sys.used_memory() as f64 / (1024.0 * 1024.0 * 1024.0);
    let total_ram = sys.total_memory() as f64 / (1024.0 * 1024.0 * 1024.0);

    SysinfoPayload {
        cpu: (cpu * 100.0).round() / 100.0,
        ram: (ram * 100.0).round() / 100.0,
        total_ram: (total_ram * 100.0).round() / 100.0,
        vram: 0.0,
        gpu_active: false,
    }
}

// ── Log tail ──────────────────────────────────────────────────────────────

#[derive(Serialize, Clone)]
pub struct LogTailPayload {
    lines: Vec<String>,
    path: String,
    exists: bool,
    total_lines: usize,
}

#[tauri::command]
pub fn read_log_tail(source: String, tail: Option<usize>) -> LogTailPayload {
    let tail = tail.unwrap_or(300).clamp(10, 2000);

    let path = match source.as_str() {
        "backend" => backend_runtime_log_path(),
        "tauri" => tauri_log_path(),
        _ => {
            return LogTailPayload {
                lines: vec![],
                path: String::new(),
                exists: false,
                total_lines: 0,
            }
        }
    };

    let path_str = path.to_string_lossy().to_string();
    if !path.exists() {
        return LogTailPayload {
            lines: vec![],
            path: path_str,
            exists: false,
            total_lines: 0,
        };
    }

    match fs::read_to_string(&path) {
        Ok(content) => {
            let all_lines: Vec<&str> = content.lines().collect();
            let total = all_lines.len();
            let start = total.saturating_sub(tail);
            let lines: Vec<String> = all_lines[start..]
                .iter()
                .map(|l| format!("{}\n", l))
                .collect();
            LogTailPayload {
                lines,
                path: path_str,
                exists: true,
                total_lines: total,
            }
        }
        Err(_) => LogTailPayload {
            lines: vec![],
            path: path_str,
            exists: true,
            total_lines: 0,
        },
    }
}

fn backend_runtime_log_path() -> PathBuf {
    let data_dir = if cfg!(target_os = "macos") {
        dirs_data_dir().join("OmniVoice")
    } else if cfg!(target_os = "windows") {
        PathBuf::from(std::env::var("APPDATA").unwrap_or_else(|_| ".".to_string()))
            .join("OmniVoice")
    } else {
        PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string()))
            .join(".omnivoice")
    };
    data_dir.join("omnivoice.log")
}

fn dirs_data_dir() -> PathBuf {
    #[cfg(target_os = "macos")]
    {
        PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string()))
            .join("Library/Application Support")
    }
    #[cfg(not(target_os = "macos"))]
    {
        PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string()))
    }
}

fn tauri_log_path() -> PathBuf {
    let bid = "com.debpalash.omnivoice-studio";
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());

    if cfg!(target_os = "macos") {
        PathBuf::from(&home)
            .join("Library/Logs")
            .join(bid)
            .join("tauri.log")
    } else if cfg!(target_os = "windows") {
        let appdata = std::env::var("APPDATA").unwrap_or_else(|_| home.clone());
        PathBuf::from(appdata)
            .join(bid)
            .join("logs")
            .join("tauri.log")
    } else {
        PathBuf::from(&home)
            .join(".local/share")
            .join(bid)
            .join("logs")
            .join("tauri.log")
    }
}

// ── HuggingFace cache scan ────────────────────────────────────────────────

#[derive(Serialize, Clone)]
struct HfCacheRepo {
    repo_id: String,
    size_on_disk: u64,
    nb_files: usize,
}

#[derive(Serialize, Clone)]
pub struct HfCacheScanResult {
    repos: Vec<HfCacheRepo>,
    cache_dir: String,
}

#[tauri::command]
pub fn hf_cache_scan() -> HfCacheScanResult {
    let cache_dir = hf_hub_cache_dir();
    if !cache_dir.is_dir() {
        return HfCacheScanResult {
            repos: vec![],
            cache_dir: cache_dir.to_string_lossy().to_string(),
        };
    }

    let mut repos: Vec<HfCacheRepo> = Vec::new();

    if let Ok(entries) = fs::read_dir(&cache_dir) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if !name.starts_with("models--") && !name.starts_with("datasets--") {
                continue;
            }
            let repo_path = entry.path();
            if !repo_path.is_dir() {
                continue;
            }

            let repo_id = name
                .strip_prefix("models--")
                .or_else(|| name.strip_prefix("datasets--"))
                .unwrap_or(&name)
                .replace("--", "/");

            let mut total_size: u64 = 0;
            let mut nb_files: usize = 0;

            for entry in walkdir::WalkDir::new(&repo_path)
                .follow_links(true)
                .into_iter()
                .flatten()
            {
                if entry.file_type().is_file() {
                    if let Ok(meta) = entry.metadata() {
                        total_size += meta.len();
                        nb_files += 1;
                    }
                }
            }

            if total_size > 0 {
                repos.push(HfCacheRepo {
                    repo_id,
                    size_on_disk: total_size,
                    nb_files,
                });
            }
        }
    }

    HfCacheScanResult {
        repos,
        cache_dir: cache_dir.to_string_lossy().to_string(),
    }
}

fn hf_hub_cache_dir() -> PathBuf {
    if let Ok(v) = std::env::var("HF_HUB_CACHE") {
        return PathBuf::from(v);
    }
    if let Ok(v) = std::env::var("HUGGINGFACE_HUB_CACHE") {
        return PathBuf::from(v);
    }
    if let Ok(v) = std::env::var("HF_HOME") {
        return PathBuf::from(v).join("hub");
    }
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home)
        .join(".cache")
        .join("huggingface")
        .join("hub")
}

// ── Dictation output ─────────────────────────────────────────────────────

/// Error-kind builder the dictation widget switches on. Kinds are a plain
/// string prefix ("a11y:" | "clipboard:" | "paste:" | "preflight:") so the
/// JS side can do `err.split(':')[0]` without a serde enum crossing the IPC
/// boundary.
fn kind_err(kind: &str, detail: impl std::fmt::Display) -> String {
    format!("{kind}:{detail}")
}

/// macOS Accessibility grant check — CGEvent key synthesis silently no-ops
/// without it. Direct FFI against ApplicationServices: one symbol, not worth
/// a crate.
#[cfg(target_os = "macos")]
fn accessibility_trusted() -> bool {
    #[link(name = "ApplicationServices", kind = "framework")]
    extern "C" {
        fn AXIsProcessTrusted() -> bool;
    }
    unsafe { AXIsProcessTrusted() }
}

/// True when the app may synthesize keyboard input. On macOS this is the
/// Accessibility grant (System Settings → Privacy & Security → Accessibility);
/// other OSes don't gate synthetic input behind a permission, so always true.
#[tauri::command]
pub fn check_accessibility() -> bool {
    #[cfg(target_os = "macos")]
    {
        accessibility_trusted()
    }
    #[cfg(not(target_os = "macos"))]
    {
        true
    }
}

/// Deep-link into the macOS Privacy → Accessibility pane so the widget can
/// walk the user straight to the toggle an "a11y:" error asked for. No-op on
/// other OSes (nothing to grant there).
#[tauri::command]
pub fn open_accessibility_settings() {
    #[cfg(target_os = "macos")]
    {
        let _ = std::process::Command::new("open")
            .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
            .spawn();
    }
}

// ── OS permission probes (microphone / input monitoring) ─────────────────
//
// Cross-platform-honest: these never guess a grant state they can't know.
// `check_microphone` returns one of "granted" | "denied" | "prompt" |
// "unknown" — "unknown" means the OS gives us no readable answer (Linux has
// no per-app mic TCC; older Windows lacks the ConsentStore key), and the JS
// side must not treat it as either granted or denied.

/// macOS microphone grant via `[AVCaptureDevice authorizationStatusForMediaType:
/// AVMediaTypeAudio]`. Hand-rolled ObjC-runtime FFI, same spirit as
/// `accessibility_trusted()` above: three runtime symbols + one framework
/// constant, not worth a crate.
#[cfg(target_os = "macos")]
fn microphone_auth_status() -> &'static str {
    use std::os::raw::{c_char, c_void};

    #[link(name = "objc")]
    extern "C" {
        fn objc_getClass(name: *const c_char) -> *mut c_void;
        fn sel_registerName(name: *const c_char) -> *mut c_void;
        // Deliberately signature-less: objc_msgSend is variadic-by-convention
        // and must be cast to the concrete fn type per call site.
        fn objc_msgSend();
    }
    // Linking AVFoundation is what makes the AVCaptureDevice class and the
    // AVMediaTypeAudio NSString constant exist at runtime.
    #[link(name = "AVFoundation", kind = "framework")]
    extern "C" {
        #[allow(non_upper_case_globals)]
        static AVMediaTypeAudio: *mut c_void;
    }

    unsafe {
        let cls = objc_getClass(c"AVCaptureDevice".as_ptr());
        if cls.is_null() {
            return "unknown";
        }
        let sel = sel_registerName(c"authorizationStatusForMediaType:".as_ptr());
        let msg_send: extern "C" fn(*mut c_void, *mut c_void, *mut c_void) -> isize =
            std::mem::transmute(objc_msgSend as unsafe extern "C" fn());
        // AVAuthorizationStatus: 0 notDetermined, 1 restricted, 2 denied,
        // 3 authorized. Anything newer/unexpected is honestly "unknown".
        match msg_send(cls, sel, AVMediaTypeAudio) {
            0 => "prompt",
            1 | 2 => "denied",
            3 => "granted",
            _ => "unknown",
        }
    }
}

/// Windows microphone consent from the CapabilityAccessManager ConsentStore.
/// A desktop (unpackaged) app's getUserMedia is gated by TWO per-user (HKCU)
/// toggles: the master "Microphone access" switch (the ConsentStore key
/// itself) AND "Let desktop apps access your microphone" (the `NonPackaged`
/// subkey). Reading only the master used to report "granted" while the
/// desktop-app toggle silently blocked capture — so the probe reads the whole
/// effective chain: denied if EITHER is Deny, granted only when BOTH read
/// Allow, otherwise honestly "unknown" (key missing on older builds, or an
/// unexpected value).
#[cfg(target_os = "windows")]
fn microphone_consent_from_registry() -> &'static str {
    use windows::core::{w, PCWSTR};
    use windows::Win32::Foundation::ERROR_SUCCESS;
    use windows::Win32::System::Registry::{RegGetValueW, HKEY_CURRENT_USER, RRF_RT_REG_SZ};

    // "Allow" / "Deny" / "Prompt" — 16 UTF-16 units is plenty; RegGetValueW
    // writes a NUL-terminated string and `size` is in bytes. None = key or
    // value missing / unreadable.
    fn read_consent(subkey: PCWSTR) -> Option<String> {
        let mut buf = [0u16; 16];
        let mut size = (buf.len() * std::mem::size_of::<u16>()) as u32;
        let status = unsafe {
            RegGetValueW(
                HKEY_CURRENT_USER,
                subkey,
                w!("Value"),
                RRF_RT_REG_SZ,
                None,
                Some(buf.as_mut_ptr().cast()),
                Some(&mut size),
            )
        };
        if status != ERROR_SUCCESS {
            return None;
        }
        let len = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
        Some(String::from_utf16_lossy(&buf[..len]))
    }

    let master = read_consent(w!(
        r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
    ));
    let non_packaged = read_consent(w!(
        r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged"
    ));

    let is_deny = |v: &Option<String>| matches!(v.as_deref(), Some("Deny"));
    let is_allow = |v: &Option<String>| matches!(v.as_deref(), Some("Allow"));
    if is_deny(&master) || is_deny(&non_packaged) {
        return "denied";
    }
    if is_allow(&master) && is_allow(&non_packaged) {
        return "granted";
    }
    // Either toggle missing (older Windows builds) or an unexpected value —
    // don't guess.
    "unknown"
}

/// Microphone permission state: "granted" | "denied" | "prompt" | "unknown".
/// macOS reads the TCC grant via AVFoundation; Windows reads the per-user
/// ConsentStore toggle; Linux is always "unknown" (PulseAudio/PipeWire has no
/// per-app mic permission and we don't use the portal).
#[tauri::command]
pub fn check_microphone() -> String {
    #[cfg(target_os = "macos")]
    {
        microphone_auth_status().to_string()
    }
    #[cfg(target_os = "windows")]
    {
        microphone_consent_from_registry().to_string()
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        "unknown".to_string()
    }
}

/// Deep-link into the OS microphone-privacy pane. Errors use the same
/// `kind:detail` convention as the paste commands ("settings:" kind) so the
/// JS side can switch on `err.split(':')[0]`.
#[tauri::command]
pub fn open_microphone_settings() -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
            .spawn()
            .map(|_| ())
            .map_err(|e| {
                kind_err(
                    "settings",
                    format!("failed to open microphone settings: {e}"),
                )
            })
    }
    #[cfg(target_os = "windows")]
    {
        // `start` is a cmd builtin — there's no ms-settings executable to
        // spawn directly. CREATE_NO_WINDOW stops the cmd console flash
        // (same pattern as the nvidia-smi probe in setup.rs).
        use std::os::windows::process::CommandExt;
        std::process::Command::new("cmd")
            .args(["/C", "start", "ms-settings:privacy-microphone"])
            .creation_flags(0x0800_0000) // CREATE_NO_WINDOW
            .spawn()
            .map(|_| ())
            .map_err(|e| {
                kind_err(
                    "settings",
                    format!("failed to open microphone settings: {e}"),
                )
            })
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        // No per-app mic permission pane exists on Linux — an xdg-open target
        // would be a guess that varies by desktop. Err so the JS side can show
        // "open your system sound settings" instead of pretending we did.
        Err(kind_err(
            "settings",
            "no microphone permission pane on this OS; open your system sound settings",
        ))
    }
}

/// Deep-link into macOS Privacy → Input Monitoring (the grant global-shortcut
/// key listening needs on newer macOS). macOS-only: no such pane exists
/// elsewhere, so other OSes get a "settings:" Err rather than a silent no-op.
#[tauri::command]
pub fn open_input_monitoring_settings() -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent")
            .spawn()
            .map(|_| ())
            .map_err(|e| {
                kind_err(
                    "settings",
                    format!("failed to open input monitoring settings: {e}"),
                )
            })
    }
    #[cfg(not(target_os = "macos"))]
    {
        Err(kind_err(
            "settings",
            "input monitoring settings are macOS-only",
        ))
    }
}

#[tauri::command]
pub async fn simulate_paste(
    text: String,
    session_id: u64,
    flags: tauri::State<'_, AppFlags>,
) -> Result<crate::dictation_output::DeliveryOutcome, String> {
    // macOS: a revoked/missing Accessibility grant prevents synthesis, but it
    // must not discard the result. Keep the full transcript copied and report
    // the fallback truthfully.
    #[cfg(target_os = "macos")]
    if !accessibility_trusted() {
        let output = flags.output.clone();
        return tauri::async_runtime::spawn_blocking(move || {
            output.copy_for_session(session_id, &text)
        })
        .await
        .map_err(|error| kind_err("clipboard", format!("output worker failed: {error}")))?;
    }

    let output = flags.output.clone();
    tauri::async_runtime::spawn_blocking(move || output.deliver(session_id, &text))
        .await
        .map_err(|error| kind_err("paste", format!("output worker failed: {error}")))?
}

/// Preserve the authoritative transcript without emitting any keyboard input.
/// Used after live typing may have left an unknown prefix in the target: a
/// second insertion would duplicate text, but losing the complete result is
/// not an acceptable fallback.
#[tauri::command]
pub async fn copy_dictation_output_session(
    text: String,
    session_id: u64,
    flags: tauri::State<'_, AppFlags>,
) -> Result<crate::dictation_output::DeliveryOutcome, String> {
    let output = flags.output.clone();
    tauri::async_runtime::spawn_blocking(move || output.copy_for_session(session_id, &text))
        .await
        .map_err(|error| kind_err("clipboard", format!("output worker failed: {error}")))?
}

// ── Simulate live typing ──────────────────────────────────────────────────

/// Type a string at the current cursor and/or emit N backspaces, for live
/// word-by-word dictation (text appears in the focused field as you speak).
///
/// `backspaces` are sent FIRST (to retract characters a streaming recognizer
/// revised), then `text` is typed. Either may be empty/zero, so a single call
/// can correct-then-type in one round trip.
///
/// The session-bound output layer reactivates the destination captured at
/// shortcut-down before emitting input. On Wayland it selects one compatible
/// compositor helper before emission and never retries after a possible
/// partial write.
///
/// Returns `Err` if the input layer is unavailable (e.g. accessibility not
/// granted). Because a failed input call may already have emitted a prefix,
/// the JS caller suppresses later insertion for that session. `preflight:`
/// explicitly means nothing was emitted and a final paste remains safe.
#[tauri::command]
pub async fn simulate_type(
    text: String,
    backspaces: Option<u32>,
    session_id: u64,
    flags: tauri::State<'_, AppFlags>,
) -> Result<crate::dictation_output::DeliveryOutcome, String> {
    // Same a11y gate as simulate_paste — `.text()`/`.key()` go through the
    // identical CGEvent path on macOS and would silently no-op without it.
    #[cfg(target_os = "macos")]
    if !accessibility_trusted() {
        return Err(kind_err("a11y", "accessibility permission not granted"));
    }

    let output = flags.output.clone();
    tauri::async_runtime::spawn_blocking(move || {
        output.type_delta(session_id, &text, backspaces.unwrap_or(0))
    })
    .await
    .map_err(|error| kind_err("paste", format!("output worker failed: {error}")))?
}

#[tauri::command]
pub async fn activate_dictation_output_session(
    session_id: u64,
    flags: tauri::State<'_, AppFlags>,
) -> Result<(), String> {
    let output = flags.output.clone();
    tauri::async_runtime::spawn_blocking(move || output.activate_session(session_id))
        .await
        .map_err(|error| kind_err("paste", format!("output worker failed: {error}")))?
}

#[tauri::command]
pub async fn reject_dictation_output_session(
    session_id: u64,
    flags: tauri::State<'_, AppFlags>,
) -> Result<(), String> {
    let output = flags.output.clone();
    tauri::async_runtime::spawn_blocking(move || output.reject_session_candidate(session_id))
        .await
        .map_err(|error| kind_err("paste", format!("output worker failed: {error}")))?;
    Ok(())
}

#[tauri::command]
pub async fn finish_dictation_output_session(
    session_id: u64,
    flags: tauri::State<'_, AppFlags>,
) -> Result<(), String> {
    let output = flags.output.clone();
    tauri::async_runtime::spawn_blocking(move || output.finish_session(session_id))
        .await
        .map_err(|error| kind_err("paste", format!("output worker failed: {error}")))?;
    Ok(())
}

// ── Tray icon swap ────────────────────────────────────────────────────────

#[tauri::command]
pub fn set_tray_recording(
    app: tauri::AppHandle,
    recording: bool,
    tray_handle: tauri::State<'_, TrayHandle>,
    flags: tauri::State<'_, AppFlags>,
    shortcuts: tauri::State<'_, DictationShortcutManager>,
) -> Result<(), String> {
    // Record the state BEFORE the icon swap: the tray's Start/Stop item reads
    // this to decide which event to emit, and it must stay correct even if the
    // icon fails to decode. (It used to read `widget.is_visible()`, which the
    // permanently-hidden widget made meaningless.)
    flags.dictating.store(recording, Ordering::SeqCst);
    log::info!("Dictation recording state: {recording}");
    let bytes = if recording {
        TRAY_ICON_RECORDING
    } else {
        TRAY_ICON_DEFAULT
    };
    let img = Image::from_bytes(bytes).map_err(|e| format!("decode tray icon: {e}"))?;
    let lock = tray_handle.tray.lock().map_err(|_| "tray lock poisoned")?;
    if let Some(ref tray) = *lock {
        tray.set_icon(Some(img))
            .map_err(|e| format!("set_icon: {e}"))?;
    }
    update_tray_hint(&app, &shortcuts.info().display, recording);
    Ok(())
}

// ── Quit ──────────────────────────────────────────────────────────────────

#[tauri::command]
pub fn quit_app(app: tauri::AppHandle, flags: tauri::State<'_, AppFlags>) {
    flags.quitting.store(true, Ordering::SeqCst);
    app.exit(0);
}

// ── Dictation hotkey ──────────────────────────────────────────────────────

#[tauri::command]
pub fn get_dictation_shortcut(app: tauri::AppHandle) -> String {
    load_config(&app).dictation_shortcut
}

#[tauri::command]
pub fn get_effective_dictation_shortcut(
    state: tauri::State<'_, DictationShortcutManager>,
) -> ShortcutInfo {
    state.info()
}

#[tauri::command]
pub fn request_dictation_capture(app: tauri::AppHandle, action: String) -> Result<(), String> {
    if action != "start" && action != "stop" && action != "toggle" {
        return Err("capture action must be start, stop, or toggle".into());
    }
    crate::dispatch_dictation_capture(&app, &action);
    Ok(())
}

/// Distance from the bottom edge of the work area, in logical pixels — clear of
/// a dock/taskbar without floating in the middle of the screen.
const PILL_BOTTOM_MARGIN: f64 = 56.0;

/// Bottom-centre the pill inside a monitor. Pure geometry so the placement can
/// be tested without a display server; every value is physical pixels except
/// `scale`, which converts the logical margin.
fn pill_bottom_centre(
    origin: (i32, i32),
    area: (u32, u32),
    size: (u32, u32),
    scale: f64,
) -> (i32, i32) {
    let x = origin.0 + (area.0 as i32 - size.0 as i32) / 2;
    let margin = (PILL_BOTTOM_MARGIN * scale).round() as i32;
    let y = origin.1 + area.1 as i32 - size.1 as i32 - margin;
    // A pill taller than its monitor would otherwise be placed off the top.
    (x.max(origin.0), y.max(origin.1))
}

/// Place the pill above the bottom edge of the screen the pointer is on.
///
/// Wayland denies clients any say in their own placement, so `set_position` is
/// a no-op there and the compositor decides — the pill still appears, just
/// wherever that compositor puts it. Every other platform honours it.
fn place_dictation_pill(app: &tauri::AppHandle, win: &tauri::WebviewWindow) {
    let monitor = app
        .cursor_position()
        .ok()
        .and_then(|point| app.monitor_from_point(point.x, point.y).ok().flatten())
        .or_else(|| win.current_monitor().ok().flatten())
        .or_else(|| app.primary_monitor().ok().flatten());
    let Some(monitor) = monitor else {
        log::warn!("pill: no monitor available to place the capture pill on");
        return;
    };
    let size = match win.outer_size() {
        Ok(size) => size,
        Err(error) => {
            log::warn!("pill: could not measure the capture pill: {error}");
            return;
        }
    };
    let area = monitor.size();
    let origin = monitor.position();
    let (x, y) = pill_bottom_centre(
        (origin.x, origin.y),
        (area.width, area.height),
        (size.width, size.height),
        monitor.scale_factor(),
    );
    if let Err(error) = win.set_position(tauri::PhysicalPosition::new(x, y)) {
        log::warn!("pill: could not place the capture pill: {error}");
        return;
    }
    log::info!(
        "pill: placed at {x},{y} ({}x{} on a {}x{} monitor)",
        size.width,
        size.height,
        area.width,
        area.height
    );
}

/// Show the dictation pill for the duration of a capture.
///
/// Called by the capture widget on every state that the user must see —
/// recording, transcribing, the result flash, an error, the Accessibility
/// prompt. `dismiss()` in the widget owns hiding it again, and the idle
/// reconcile there is the backstop.
#[tauri::command]
pub fn show_dictation_pill(app: tauri::AppHandle) -> Result<(), String> {
    let Some(win) = app.get_webview_window("widget") else {
        return Err("the capture window is not available".into());
    };
    place_dictation_pill(&app, &win);
    // The pill must never take focus: the paste lands in whatever app the user
    // was typing into, and stealing foreground breaks that (#982, #287).
    #[cfg(target_os = "windows")]
    crate::show_pill_noactivate(&win);
    #[cfg(not(target_os = "windows"))]
    win.show()
        .map_err(|error| format!("could not show the capture pill: {error}"))?;
    log::info!("pill: shown (visible={:?})", win.is_visible());
    Ok(())
}

#[cfg(test)]
mod pill_placement_tests {
    use super::{pill_bottom_centre, PILL_BOTTOM_MARGIN};

    #[test]
    fn centres_horizontally_and_sits_above_the_bottom_edge() {
        let (x, y) = pill_bottom_centre((0, 0), (1920, 1080), (300, 64), 1.0);
        assert_eq!(x, 810);
        assert_eq!(y, 1080 - 64 - PILL_BOTTOM_MARGIN as i32);
    }

    #[test]
    fn places_relative_to_the_monitor_origin_on_a_second_screen() {
        // A monitor to the right of / above the primary has a non-zero origin;
        // ignoring it puts the pill on the wrong screen entirely.
        let (x, y) = pill_bottom_centre((1920, -200), (2560, 1440), (600, 128), 2.0);
        assert_eq!(x, 1920 + (2560 - 600) / 2);
        assert_eq!(y, -200 + 1440 - 128 - (PILL_BOTTOM_MARGIN * 2.0) as i32);
    }

    #[test]
    fn never_places_the_pill_off_the_top_or_left_of_its_monitor() {
        let (x, y) = pill_bottom_centre((0, 0), (200, 100), (300, 300), 1.0);
        assert_eq!((x, y), (0, 0));
    }
}

#[tauri::command]
pub fn begin_dictation_capture_registration(app: tauri::AppHandle) -> Result<u64, String> {
    let flags = app.state::<AppFlags>();
    let mut capture = flags
        .capture
        .lock()
        .map_err(|_| "Dictation capture state lock poisoned".to_string())?;
    Ok(capture.begin_registration())
}

#[tauri::command]
pub fn mark_dictation_capture_ready(app: tauri::AppHandle, registration_id: u64) {
    let flags = app.state::<AppFlags>();
    let Ok(mut capture) = flags.capture.lock() else {
        log::warn!("Dictation capture state lock poisoned");
        return;
    };
    let pending = capture.mark_registration_ready(registration_id);
    drop(capture);
    for event in pending {
        if let Err(error) = app.emit(event.name, event.payload) {
            log::warn!(
                "Queued dictation event {} could not emit: {error}",
                event.name
            );
        }
    }
}

#[tauri::command]
pub fn acknowledge_dictation_capture_delivery(
    app: tauri::AppHandle,
    registration_id: u64,
    delivery_id: u64,
) {
    let flags = app.state::<AppFlags>();
    let Ok(mut capture) = flags.capture.lock() else {
        log::warn!("Dictation capture state lock poisoned");
        return;
    };
    capture.acknowledge(registration_id, delivery_id);
}

#[tauri::command]
pub fn end_dictation_capture_registration(app: tauri::AppHandle, registration_id: u64) {
    let flags = app.state::<AppFlags>();
    let Ok(mut capture) = flags.capture.lock() else {
        log::warn!("Dictation capture state lock poisoned");
        return;
    };
    capture.end_registration(registration_id);
}

#[tauri::command]
pub fn set_dictation_shortcut(
    app: tauri::AppHandle,
    accelerator: String,
    state: tauri::State<'_, DictationShortcutManager>,
) -> Result<ShortcutInfo, String> {
    let info = state.serialize_update(|| {
        let mut cfg = load_config(&app);
        let previous = cfg.dictation_shortcut.clone();
        let path = crate::config::config_path(&app)
            .ok_or_else(|| "Could not locate the VoiceStudio config directory".to_string())?;
        apply_shortcut_change(
            &accelerator,
            &previous,
            |value| state.replace(&app, value),
            || {
                cfg.dictation_shortcut = accelerator.clone();
                crate::config::save_config_at(&path, &cfg)
            },
        )
    })?;
    log::info!("Dictation shortcut updated to {accelerator}");
    Ok(info)
}

fn apply_shortcut_change<T, A, P>(
    replacement: &str,
    previous: &str,
    mut activate: A,
    persist: P,
) -> Result<T, String>
where
    A: FnMut(&str) -> Result<T, String>,
    P: FnOnce() -> Result<(), String>,
{
    let active = activate(replacement)?;
    if let Err(error) = persist() {
        let rollback = activate(previous);
        return Err(match rollback {
            Ok(_) => format!("Could not save the shortcut: {error}"),
            Err(rollback_error) => format!(
                "Could not save the shortcut ({error}); restoring the previous shortcut also failed: {rollback_error}"
            ),
        });
    }
    Ok(active)
}

#[cfg(test)]
mod shortcut_change_tests {
    use super::apply_shortcut_change;
    use std::cell::RefCell;

    #[test]
    fn activates_the_replacement_before_persisting_it() {
        let events = RefCell::new(Vec::new());
        let result = apply_shortcut_change(
            "Ctrl+Alt+K",
            "Ctrl+Shift+Space",
            |value| {
                events.borrow_mut().push(format!("activate:{value}"));
                Ok(value.to_owned())
            },
            || {
                events.borrow_mut().push("persist".into());
                Ok(())
            },
        )
        .unwrap();

        assert_eq!(result, "Ctrl+Alt+K");
        assert_eq!(events.into_inner(), ["activate:Ctrl+Alt+K", "persist"]);
    }

    #[test]
    fn restores_the_previous_binding_when_persistence_fails() {
        let events = RefCell::new(Vec::new());
        let error = apply_shortcut_change(
            "Ctrl+Alt+K",
            "Ctrl+Shift+Space",
            |value| {
                events.borrow_mut().push(format!("activate:{value}"));
                Ok(())
            },
            || Err("disk full".into()),
        )
        .unwrap_err();

        assert!(error.contains("disk full"));
        assert_eq!(
            events.into_inner(),
            ["activate:Ctrl+Alt+K", "activate:Ctrl+Shift+Space"]
        );
    }
}

// ── Launch-mode persistence ───────────────────────────────────────────────

#[tauri::command]
pub fn get_launch_as_widget(app: tauri::AppHandle) -> bool {
    load_config(&app).launch_as_widget
}

/// Persist the launch-mode preference. Takes effect on next app launch.
/// Caller decides whether to relaunch immediately (typical UX pattern:
/// tray-menu trigger relaunches; Settings checkbox just persists).
#[tauri::command]
pub fn set_launch_as_widget(app: tauri::AppHandle, value: bool) -> Result<bool, String> {
    let mut cfg = load_config(&app);
    cfg.launch_as_widget = value;
    save_config(&app, &cfg);
    log::info!("Launch mode updated: launch_as_widget={value}");
    Ok(value)
}

#[tauri::command]
pub fn save_text_file(path: String, contents: String) -> Result<(), String> {
    // Subtitle exports (#309). The path comes from the OS save dialog in this
    // process — the user's dialog interaction *is* the authorization, which is
    // why this write lives here and not behind a loopback-HTTP query param.
    let p = std::path::Path::new(&path);
    if !p.is_absolute() {
        return Err("save path must be absolute".into());
    }
    if let Some(dir) = p.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("create dir: {e}"))?;
    }
    std::fs::write(p, contents).map_err(|e| format!("write: {e}"))
}

#[tauri::command]
pub fn reveal_host_path(app: tauri::AppHandle, path: String) -> Result<(), String> {
    // Revealing is a native-shell action, never a loopback HTTP authority.
    // Canonicalization rejects missing paths and removes traversal/symlinks;
    // argv-only spawning avoids shell interpretation on every platform.
    let target = std::fs::canonicalize(&path)
        .map_err(|_| "That file or folder is no longer on disk".to_string())?;
    if !reveal_path_is_authorized(&app, &target) {
        return Err("That path was not selected by VoiceStudio".into());
    }
    let folder = if target.is_dir() {
        target.clone()
    } else {
        target
            .parent()
            .ok_or_else(|| "That path has no containing folder".to_string())?
            .to_path_buf()
    };
    let mut command = if cfg!(target_os = "macos") {
        let mut command = std::process::Command::new("open");
        if target.is_file() {
            command.arg("-R").arg(&target);
        } else {
            command.arg(&folder);
        }
        command
    } else if cfg!(target_os = "windows") {
        let mut command = std::process::Command::new("explorer");
        if target.is_file() {
            command.arg("/select,").arg(&target);
        } else {
            command.arg(&folder);
        }
        command
    } else {
        let mut command = std::process::Command::new("xdg-open");
        command.arg(&folder);
        command
    };
    let mut child = crate::tools::no_window(&mut command)
        .spawn()
        .map_err(|e| format!("Could not open the containing folder: {e}"))?;
    std::thread::spawn(move || {
        let _ = child.wait();
    });
    Ok(())
}

// ── WebView cache repair (issue #879) ─────────────────────────────────────
//
// After an unclean shutdown (e.g. a Windows BSOD), WebView2's profile cache
// (%LOCALAPPDATA%\<identifier>\EBWebView) can corrupt. Tauri's IPC custom
// protocol then fails ("IPC custom protocol failed, Tauri will now use the
// postMessage interface instead") and the postMessage fallback can break too,
// so the splash never hears bootstrap events even with a healthy backend.
// The splash's recovery panel (Windows-only affordance, error-state only)
// calls `clear_webview_cache_and_relaunch` to fix it in one click.
//
// Deleting caches from inside a running app fails — the WebView2 browser
// processes hold locks on the profile — so this is a two-step dance:
//   1. the command writes a marker file, then requests a relaunch through the
//      bounded frontend persistence handshake;
//   2. the fresh process calls `clear_webview_cache_if_marked()` at the very
//      top of `run()`, before any webview exists, and deletes cache-only
//      subdirectories there — retrying briefly while the old instance's
//      WebView2 children finish exiting. Local Storage and IndexedDB are never
//      touched: they contain the user's settings and long-form projects.
//
// Everything below compiles on every platform (runtime `cfg!` guards, not
// `#[cfg]`) so a macOS/Linux `cargo check` validates the whole path; the
// behavior itself is Windows-only and the frontend never renders the button
// elsewhere.

const CLEAR_WEBVIEW_MARKER: &str = ".clear-webview-cache";
const WEBVIEW_PROFILE_DIR: &str = "EBWebView";
const WEBVIEW_CACHE_RELATIVE_DIRS: &[&str] = &[
    "Default/Cache",
    "Default/Code Cache",
    "Default/GPUCache",
    "Default/DawnCache",
    "Default/Service Worker/CacheStorage",
    "Default/Service Worker/ScriptCache",
    "GPUCache",
    "DawnCache",
    "ShaderCache",
    "GrShaderCache",
    "GraphiteDawnCache",
];
/// Retry budget for step 2: the requested restart spawns the new process
/// before the old one has fully exited, so its WebView2 children may still
/// hold locks on the profile — 20 × 500 ms rides out that handoff.
const CLEAR_WEBVIEW_ATTEMPTS: u32 = 20;
const CLEAR_WEBVIEW_RETRY_DELAY: Duration = Duration::from_millis(500);

/// (marker file, WebView profile dir) under the pre-app local data dir. Mirrors
/// `config::config_path_pre_app()` — `%LOCALAPPDATA%\<identifier>` on
/// Windows — because step 2 runs before an `AppHandle` exists.
fn webview_cache_paths() -> Option<(PathBuf, PathBuf)> {
    let base = dirs_next::data_local_dir()?.join(crate::config::BUNDLE_IDENTIFIER);
    Some((
        base.join(CLEAR_WEBVIEW_MARKER),
        base.join(WEBVIEW_PROFILE_DIR),
    ))
}

#[tauri::command]
pub fn clear_webview_cache_and_relaunch(app: tauri::AppHandle) -> Result<(), String> {
    if !cfg!(target_os = "windows") {
        return Err("WebView cache repair is only available on Windows (WebView2)".into());
    }
    let (marker, profile) = webview_cache_paths()
        .ok_or_else(|| "could not resolve the local app data directory".to_string())?;
    if let Some(parent) = marker.parent() {
        let _ = fs::create_dir_all(parent);
    }
    fs::write(
        &marker,
        b"requested by the splash recovery panel (issue #879)\n",
    )
    .map_err(|e| format!("write {}: {e}", marker.display()))?;
    log::warn!(
        "WebView cache repair requested (#879) — relaunching to clear caches under {}",
        profile.display()
    );
    crate::persistence_exit::request_restart(&app)
}

/// Startup half of the repair: if the previous run left the marker, delete
/// the WebView2 profile cache before any webview is created. Called at the
/// top of `run()`. One-shot by design — the marker is removed first so a
/// failing repair can never loop across launches.
pub fn clear_webview_cache_if_marked() {
    if !cfg!(target_os = "windows") {
        return;
    }
    let Some((marker, profile)) = webview_cache_paths() else {
        return;
    };
    clear_webview_cache_at(
        &marker,
        &profile,
        CLEAR_WEBVIEW_ATTEMPTS,
        CLEAR_WEBVIEW_RETRY_DELAY,
    );
}

/// Filesystem half of [`clear_webview_cache_if_marked`], parameterized over
/// paths and retry policy so the contract is unit-testable on every platform
/// (the wrapper above is Windows-gated and pins the real paths/policy).
/// Contract, pinned by `webview_cache_repair_tests`:
///   - no marker → nothing is touched;
///   - the marker is consumed FIRST, unconditionally — one-shot, so a failing
///     repair can never loop across launches;
///   - Local Storage and IndexedDB survive every repair;
///   - a missing cache dir is success; a locked one is retried, then given up
///     on with an error log — startup is never bricked over a failed repair.
fn clear_webview_cache_at(marker: &Path, profile: &Path, attempts: u32, retry_delay: Duration) {
    if !marker.exists() {
        return;
    }
    let _ = fs::remove_file(marker);
    if !profile.exists() {
        return;
    }

    let mut cleared_any = false;
    // The requested restart may start the new process while old WebView2 cache
    // handles are closing. Retry all remaining cache-only directories as one
    // bounded batch; never remove the profile root or persistent storage dirs.
    for attempt in 1..=attempts {
        let mut retry_needed = false;
        for relative in WEBVIEW_CACHE_RELATIVE_DIRS {
            let cache = profile.join(relative);
            if !cache.exists() {
                continue;
            }
            match fs::remove_dir_all(&cache) {
                Ok(()) => cleared_any = true,
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) if attempt < attempts => {
                    retry_needed = true;
                    log::debug!("WebView2 cache still locked at {} ({e})", cache.display());
                }
                Err(e) => {
                    // Never brick startup over a failed repair: WebView2 rebuilds
                    // whatever subset was cleared, and the user can retry.
                    log::error!(
                        "could not clear WebView2 cache at {}: {e} — continuing startup",
                        cache.display()
                    );
                }
            }
        }
        if !retry_needed {
            if cleared_any {
                log::warn!(
                    "cleared WebView2 caches under {} (attempt {attempt}) — issue #879 repair",
                    profile.display()
                );
            }
            return;
        }
        std::thread::sleep(retry_delay);
    }
}

#[cfg(test)]
mod webview_cache_repair_tests {
    use super::clear_webview_cache_at;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::time::Duration;

    /// Tests must not sleep 20 × 500 ms — the retry policy is a parameter.
    const FEW: u32 = 3;
    const NO_WAIT: Duration = Duration::from_millis(1);

    /// Marker file + cache dir (with nested content, like a real profile)
    /// under a fresh temp dir.
    fn seed(dir: &Path) -> (PathBuf, PathBuf) {
        let marker = dir.join(super::CLEAR_WEBVIEW_MARKER);
        let cache = dir.join(super::WEBVIEW_PROFILE_DIR);
        fs::write(&marker, b"test").unwrap();
        fs::create_dir_all(cache.join("Default/Cache")).unwrap();
        fs::write(cache.join("Default/Cache/data_0"), b"x").unwrap();
        fs::create_dir_all(cache.join("Default/Code Cache/js")).unwrap();
        fs::write(cache.join("Default/Code Cache/js/data_0"), b"x").unwrap();
        fs::create_dir_all(cache.join("Default/Local Storage/leveldb")).unwrap();
        fs::write(
            cache.join("Default/Local Storage/leveldb/data"),
            b"settings",
        )
        .unwrap();
        fs::create_dir_all(cache.join("Default/IndexedDB/omnivoice.longform.leveldb")).unwrap();
        fs::write(
            cache.join("Default/IndexedDB/omnivoice.longform.leveldb/data"),
            b"projects",
        )
        .unwrap();
        (marker, cache)
    }

    #[test]
    fn marker_present_clears_only_caches_and_consumes_marker_once() {
        let dir = tempfile::tempdir().unwrap();
        let (marker, cache) = seed(dir.path());
        clear_webview_cache_at(&marker, &cache, FEW, NO_WAIT);
        assert!(
            !cache.join("Default/Cache").exists(),
            "HTTP cache must be removed"
        );
        assert!(
            !cache.join("Default/Code Cache").exists(),
            "compiled-code cache must be removed"
        );
        assert!(
            cache.join("Default/Local Storage/leveldb/data").exists(),
            "settings in Local Storage must survive"
        );
        assert!(
            cache
                .join("Default/IndexedDB/omnivoice.longform.leveldb/data")
                .exists(),
            "projects in IndexedDB must survive"
        );
        assert!(!marker.exists(), "marker must be consumed");
        // One-shot: with the marker gone, a rebuilt cache is left alone.
        fs::create_dir_all(cache.join("Default/Cache")).unwrap();
        clear_webview_cache_at(&marker, &cache, FEW, NO_WAIT);
        assert!(
            cache.join("Default/Cache").exists(),
            "second call without a marker is a no-op"
        );
    }

    #[test]
    fn no_marker_touches_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let (marker, cache) = seed(dir.path());
        fs::remove_file(&marker).unwrap();
        clear_webview_cache_at(&marker, &cache, FEW, NO_WAIT);
        assert!(
            cache.join("Default/Cache/data_0").exists(),
            "without a marker the cache must be untouched"
        );
    }

    #[test]
    fn missing_cache_dir_still_consumes_marker_and_returns() {
        let dir = tempfile::tempdir().unwrap();
        let marker = dir.path().join(super::CLEAR_WEBVIEW_MARKER);
        let cache = dir.path().join(super::WEBVIEW_PROFILE_DIR);
        fs::write(&marker, b"test").unwrap();
        clear_webview_cache_at(&marker, &cache, FEW, NO_WAIT);
        assert!(
            !marker.exists(),
            "marker consumed even with nothing to clear"
        );
    }

    /// A cache that can't be deleted (Windows: WebView2 file locks; simulated
    /// here with a write-protected dir) must never panic or brick startup —
    /// and the marker is STILL consumed, so the failure can't loop across
    /// launches.
    #[cfg(unix)]
    #[test]
    fn locked_cache_never_panics_and_marker_is_still_consumed() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let (marker, cache) = seed(dir.path());
        // Deny writes on the cache dir so its entries can't be unlinked.
        let default_dir = cache.join("Default");
        fs::set_permissions(&default_dir, fs::Permissions::from_mode(0o555)).unwrap();
        clear_webview_cache_at(&marker, &cache, FEW, NO_WAIT);
        assert!(
            !marker.exists(),
            "one-shot: marker consumed even on failure"
        );
        assert!(
            cache.join("Default/Cache").exists(),
            "a locked cache survives the failed repair"
        );
        // Restore permissions so TempDir can clean up.
        fs::set_permissions(&default_dir, fs::Permissions::from_mode(0o755)).unwrap();
    }
}

#[cfg(test)]
mod paste_error_tests {
    use super::kind_err;

    #[test]
    fn kind_err_prefixes_with_kind() {
        assert_eq!(kind_err("a11y", "not granted"), "a11y:not granted");
        assert_eq!(
            kind_err("clipboard", "write failed: busy"),
            "clipboard:write failed: busy"
        );
        assert_eq!(
            kind_err("paste", "key press failed"),
            "paste:key press failed"
        );
    }

    #[test]
    fn kind_survives_colons_in_detail() {
        // The widget does `err.split(':')[0]` — details containing ':' (OS
        // error strings usually do) must not corrupt the kind.
        let e = kind_err("clipboard", "init failed: os error 5");
        assert_eq!(e.split_once(':').map(|(k, _)| k), Some("clipboard"));
    }
}
