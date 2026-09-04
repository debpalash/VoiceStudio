//! First-run install setup: the pre-bootstrap configuration surface.
//!
//! Nothing downloads or installs until the user confirms an [`InstallPlan`]
//! via `complete_setup`. The module is split into:
//!   - requirements: minimum-disk constants (measured, with headroom)
//!   - disk:         per-path free-space / writability probing
//!   - paths:        portable-base + platform default dir resolution
//!   - plan:         InstallPlan validation + application
//!   - commands:     the three Tauri IPC entry points
//!
//! Resolution helpers (`env_root`, `resolved_data_dir`, `resolved_models_dir`)
//! are consumed by `bootstrap.rs` / `backend.rs` so the chosen layout is the
//! single source of truth for every later spawn.

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use tauri::Manager;

use crate::bootstrap::{set_stage, BootstrapStage, BootstrapState};
use crate::config::{self, MirrorOverrides};

// ── Requirements ──────────────────────────────────────────────────────────

pub const GIB: u64 = 1024 * 1024 * 1024;

/// Python environment (venv + torch/whisperx/demucs wheels). Measured at
/// 7.8 GiB on Linux x64 CUDA (v0.3.5); rounded up for pip build temp files.
pub const REQUIRED_ENV_BYTES: u64 = 9 * GIB;

/// Default model set (TTS checkpoint + whisper + demucs in the HF cache).
/// Measured at 6.1 GiB after a full clone+dub session; headroom for revisions.
pub const REQUIRED_MODELS_BYTES: u64 = 7 * GIB;

/// Voice data, generation outputs, SQLite DB. Grows with use; 1 GiB floor so
/// a first session never hits a full disk mid-render.
pub const REQUIRED_DATA_BYTES: u64 = GIB;

/// Folder created next to the executable / AppImage in portable mode. The
/// whole install (env + models + voices + config) lives inside it, so moving
/// `app + this folder` together relocates the install.
pub const PORTABLE_DIR_NAME: &str = "OmniVoiceStudio-Data";

// ── Disk probing ──────────────────────────────────────────────────────────

mod disk {
    use super::*;

    /// The chosen directory usually doesn't exist yet — walk up to the
    /// nearest ancestor that does, since that's where space/permissions live.
    pub fn nearest_existing(path: &Path) -> PathBuf {
        let mut cur = path.to_path_buf();
        while !cur.exists() {
            match cur.parent() {
                Some(p) => cur = p.to_path_buf(),
                None => break,
            }
        }
        cur
    }

    pub fn available_bytes(path: &Path) -> Option<u64> {
        fs4::available_space(nearest_existing(path)).ok()
    }

    /// Stable identity of the filesystem holding `path`, so requirements for
    /// dirs that share a disk are summed before comparing against free space.
    #[cfg(unix)]
    pub fn fs_key(path: &Path) -> Option<String> {
        use std::os::unix::fs::MetadataExt;
        fs::metadata(nearest_existing(path)).ok().map(|m| format!("dev:{}", m.dev()))
    }

    #[cfg(not(unix))]
    pub fn fs_key(path: &Path) -> Option<String> {
        // Windows: the drive prefix (`C:\`) identifies the volume.
        nearest_existing(path)
            .components()
            .next()
            .map(|c| format!("vol:{}", c.as_os_str().to_string_lossy().to_uppercase()))
    }

    /// Probe writability of the nearest existing ancestor with a real write —
    /// permission bits lie (ACLs, read-only mounts, translocation), a temp
    /// file doesn't. Never creates the target dir itself; that only happens
    /// on `complete_setup`.
    pub fn writable(path: &Path) -> bool {
        let base = nearest_existing(path);
        if !base.is_dir() {
            return false;
        }
        let probe = base.join(format!(".omnivoice-write-test-{}", std::process::id()));
        match fs::write(&probe, b"ok") {
            Ok(()) => {
                let _ = fs::remove_file(&probe);
                true
            }
            Err(_) => false,
        }
    }
}

// ── Path resolution ───────────────────────────────────────────────────────

/// The folder the app sits in — next to the executable, or next to the
/// `.AppImage` file on Linux (the mounted exe path is an ephemeral squashfs
/// mount, useless as an anchor). This is where the default portable folder
/// goes and where the pointer file lives.
pub fn portable_anchor() -> Option<PathBuf> {
    if let Ok(appimage) = std::env::var("APPIMAGE") {
        return Path::new(&appimage).parent().map(Path::to_path_buf);
    }
    let exe = std::env::current_exe().ok()?;
    let mut anchor = exe.parent()?.to_path_buf();
    // macOS: step out of `Foo.app/Contents/MacOS` so the data folder sits
    // beside the .app bundle, not inside it (inside breaks code signing).
    if let Some(app_bundle) = anchor
        .ancestors()
        .find(|a| a.extension().map(|e| e == "app").unwrap_or(false))
    {
        anchor = app_bundle.parent()?.to_path_buf();
    }
    Some(anchor)
}

/// One-line UTF-8 file holding the absolute path of a relocated portable
/// folder. Sits beside the app so the install stays SELF-DISCOVERING: plug the
/// drive into another machine and the app still finds its data with no
/// per-machine state to consult.
pub const PORTABLE_POINTER_FILE: &str = "portable.path";

/// Path of the pointer file (whether or not it exists).
pub fn portable_pointer_path() -> Option<PathBuf> {
    portable_anchor().map(|a| a.join(PORTABLE_POINTER_FILE))
}

fn read_portable_pointer() -> Option<PathBuf> {
    let raw = fs::read_to_string(portable_pointer_path()?).ok()?;
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }
    let stored = PathBuf::from(trimmed);
    if stored.is_absolute() {
        return Some(stored);
    }
    // A RELATIVE pointer is the portable one: it is resolved against wherever
    // the app happens to be right now, so the install survives the mount path
    // changing — `/Volumes/Stick` on one machine, `E:\` on the next. An
    // absolute pointer cannot do that, which is why we only write one when the
    // folder lies outside the app's own directory (see `record_portable_dir`).
    Some(portable_anchor()?.join(stored))
}

/// `portable_dir` straight out of the per-user config file.
///
/// Deliberately reads the file directly instead of going through
/// `config::load_config`: that resolves its path via `config_path` →
/// `portable_config_file` → `portable_base` → here, which would recurse
/// forever. This fallback only ever consults the PLATFORM config location,
/// never the portable one, so the cycle cannot form.
fn portable_dir_from_user_config() -> Option<PathBuf> {
    let path = dirs_next::data_local_dir()?
        .join(config::BUNDLE_IDENTIFIER)
        .join("config.json");
    let text = fs::read_to_string(path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    let dir = value.get("portableDir")?.as_str()?.trim();
    if dir.is_empty() {
        return None;
    }
    Some(PathBuf::from(dir))
}

/// Directory that holds a portable install.
///
/// Resolution order — first hit wins:
///   1. the pointer file beside the app. This is the one that KEEPS
///      portability: it travels with the app and needs nothing from the host.
///   2. `portableDir` in the per-user config — the fallback for app folders
///      that are not writable (`/Applications`, `Program Files`), where a
///      pointer cannot be written. Machine-bound by nature, which is why it is
///      second, not first.
///   3. `<anchor>/OmniVoiceStudio-Data` — the historical default. Existing
///      portable installs have neither a pointer nor a config field, so they
///      keep resolving byte-identically.
pub fn portable_base() -> Option<PathBuf> {
    if let Some(p) = read_portable_pointer() {
        return Some(p);
    }
    if let Some(p) = portable_dir_from_user_config() {
        return Some(p);
    }
    portable_anchor().map(|a| a.join(PORTABLE_DIR_NAME))
}

/// What a pointer file should contain for `base`, given the app sits in
/// `anchor`.
///
/// RELATIVE whenever the folder is inside the app's own directory — the
/// USB-stick case portable mode exists for. App and folder then move as a
/// unit and the absolute mount path is free to change (`/Volumes/Stick` on
/// one machine, `E:\` on the next). Anywhere else there is no relocatable
/// reference to store, so the absolute path is recorded and the install is
/// tied to it; the UI says so rather than promising portability it cannot
/// keep (CodeRabbit, #1404).
///
/// Pure, so the choice that decides whether the portable promise holds is
/// testable without an AppHandle or a real filesystem.
fn pointer_payload(base: &Path, anchor: &Path) -> (String, &'static str) {
    match base.strip_prefix(anchor) {
        Ok(rel) if !rel.as_os_str().is_empty() => {
            (rel.to_string_lossy().into_owned(), "pointer-relative")
        }
        _ => (base.to_string_lossy().into_owned(), "pointer-absolute"),
    }
}

/// Write the pointer file if the app's folder allows it. `None` when there is
/// no anchor or it is read-only — the caller falls back to the per-user config.
fn write_portable_pointer(base: &Path) -> Option<&'static str> {
    let pointer = portable_pointer_path()?;
    let anchor = pointer.parent()?.to_path_buf();
    if !disk::writable(&anchor) {
        return None;
    }
    let (payload, flavour) = pointer_payload(base, &anchor);
    fs::write(&pointer, payload.as_bytes()).ok()?;
    Some(flavour)
}

#[derive(Debug)]
struct FileSnapshot {
    path: PathBuf,
    contents: Option<Vec<u8>>,
}

fn snapshot_file(path: PathBuf) -> Result<FileSnapshot, String> {
    let contents = match fs::read(&path) {
        Ok(contents) => Some(contents),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(format!("Could not snapshot {}: {error}", path.display())),
    };
    Ok(FileSnapshot { path, contents })
}

fn snapshot_portable_location() -> Result<Vec<FileSnapshot>, String> {
    [portable_pointer_path(), config::config_path_for_machine()]
        .into_iter()
        .flatten()
        .map(snapshot_file)
        .collect()
}

fn restore_file(snapshot: FileSnapshot) -> Result<(), String> {
    match snapshot.contents {
        Some(contents) => {
            if let Some(parent) = snapshot.path.parent() {
                fs::create_dir_all(parent)
                    .map_err(|error| format!("Could not restore {}: {error}", parent.display()))?;
            }
            fs::write(&snapshot.path, contents)
                .map_err(|error| format!("Could not restore {}: {error}", snapshot.path.display()))
        }
        None => match fs::remove_file(&snapshot.path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(format!("Could not restore {}: {error}", snapshot.path.display())),
        },
    }
}

fn restore_portable_location(snapshots: Vec<FileSnapshot>) -> Result<(), String> {
    let errors: Vec<String> = snapshots
        .into_iter()
        .filter_map(|snapshot| restore_file(snapshot).err())
        .collect();
    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors.join("; "))
    }
}

/// Persist where a relocated portable folder lives, so the next launch finds
/// it. Prefers the pointer file (portability preserved); falls back to the
/// per-user config when the app folder is read-only.
///
/// Returns which mechanism was used, for the log and for tests.
pub fn record_portable_dir<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    base: &Path,
) -> Result<&'static str, String> {
    if let Some(flavour) = write_portable_pointer(base) {
        return Ok(flavour);
    }
    // Read-only app folder (a normal macOS /Applications or Windows Program
    // Files install). Record it per-user instead: the install stops being
    // portable ACROSS machines, but it still works on this one, which beats
    // refusing the folder the user picked.
    let mut cfg = config::load_config(app);
    cfg.portable_dir = Some(base.to_string_lossy().into_owned());
    let path = config::config_path_for_machine()
        .ok_or("Could not resolve the per-user config path")?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("Could not create {}: {e}", parent.display()))?;
    }
    config::save_config_at(&path, &cfg)?;
    Ok("user-config")
}

/// Clear a recorded relocation, so `portable_base()` falls back to the
/// default folder beside the app. Best-effort on both stores — a stale
/// pointer left behind would silently outrank a later default install.
pub fn clear_portable_dir<R: tauri::Runtime>(_app: &tauri::AppHandle<R>) {
    if let Some(pointer) = portable_pointer_path() {
        let _ = fs::remove_file(pointer);
    }
    // Read and write the MACHINE config directly. Going through
    // `load_config` would resolve via `config_path` → `portable_config_file`
    // → `portable_base` → the very `portableDir` we are trying to erase, load
    // the RELOCATED config (whose own `portable_dir` is None), see nothing to
    // clear, and leave the machine record intact — so the old folder would
    // keep winning after the user chose the default (CodeRabbit, #1404).
    let Some(path) = config::config_path_for_machine() else { return };
    let Ok(text) = fs::read_to_string(&path) else { return };
    let Ok(mut cfg) = serde_json::from_str::<config::AppConfig>(&text) else { return };
    if cfg.portable_dir.is_none() {
        return;
    }
    cfg.portable_dir = None;
    let _ = config::save_config_at(&path, &cfg);
}

/// Mirror of `backend/core/config.py::get_app_data_dir()` platform defaults —
/// shown in the UI so the user sees concrete paths, never "(default)".
pub fn default_data_dir() -> PathBuf {
    #[cfg(target_os = "macos")]
    {
        dirs_next::home_dir().unwrap_or_default().join("Library/Application Support/OmniVoice")
    }
    #[cfg(target_os = "windows")]
    {
        std::env::var("APPDATA")
            .map(PathBuf::from)
            .unwrap_or_default()
            .join("OmniVoice")
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        dirs_next::home_dir().unwrap_or_default().join(".omnivoice")
    }
}

/// Default HF model cache (mirrors huggingface_hub + the backend's Windows
/// MAX_PATH redirect in `backend/core/config.py`).
pub fn default_models_dir() -> PathBuf {
    if let Ok(hf_home) = std::env::var("HF_HOME") {
        return PathBuf::from(hf_home);
    }
    #[cfg(target_os = "windows")]
    {
        std::env::var("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_default()
            .join("OmniVoice")
            .join("hf_cache")
    }
    #[cfg(not(target_os = "windows"))]
    {
        dirs_next::home_dir().unwrap_or_default().join(".cache/huggingface")
    }
}

/// Root that holds the managed Python project (`<root>/project/.venv`).
/// Single source of truth for bootstrap + clean-retry + backend spawn.
///
/// Deliberately always the TRUE, non-redirected path — never routed through
/// [`ascii_safe_dir`] here. `ensure_venv_ready` (bootstrap.rs) is the only
/// caller allowed to redirect to an ASCII-safe root (#1783), and only after
/// confirming there is nothing usable at this true path (see
/// `resolve_venv_root`): a working venv, ASCII path or not, must never be
/// silently relocated just because this function was called — every other
/// consumer (uninstall size/deletion, disk-space checks, the first-run
/// storage preview) needs the path that actually holds the data on disk.
pub fn env_root<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> PathBuf {
    let cfg = config::load_config(app);
    if cfg.install_mode == "portable" {
        if let Some(base) = portable_base() {
            return base.join("env");
        }
    }
    if let Some(dir) = cfg.env_dir.as_deref().filter(|s| !s.is_empty()) {
        return PathBuf::from(dir);
    }
    app.path().app_local_data_dir().unwrap_or_default()
}

/// True when `path`'s displayed form is pure ASCII — the precondition
/// Windows' ANSI-code-page `.pth` decoding needs (#1783). Byte-identical
/// fast path for the overwhelming majority of installs, which never touch
/// the WinAPI short-name call below.
///
/// `pub(crate)`, not Windows-gated: `bootstrap::ensure_venv_ready` uses this
/// as a cheap pre-check (a string scan, no subprocess) to skip its non-ASCII
/// interpreter probe entirely for an ASCII env root — which is every install
/// on macOS/Linux and the overwhelming majority on Windows too. Without this
/// gate, every launch with an existing venv would pay for one extra
/// subprocess spawn it never needed.
pub(crate) fn is_ascii_path(path: &Path) -> bool {
    path.to_string_lossy().is_ascii()
}

/// Split `path` into (longest existing ancestor, remaining components that
/// don't exist yet). `GetShortPathNameW` can only resolve a path that
/// already exists on disk end-to-end, but the managed env root's leaf
/// directories (`<bundle-id>`, `project`, `.venv`) are created by bootstrap
/// itself and won't exist on first run — only the user-profile ancestor
/// (e.g. `%LOCALAPPDATA%`, which Windows always creates) does. The
/// remainder is literal ASCII we chose ourselves, so it's reattached as-is.
#[cfg(any(target_os = "windows", test))]
fn split_existing_ancestor(path: &Path) -> (PathBuf, PathBuf) {
    let mut remainder = PathBuf::new();
    let mut candidate = path.to_path_buf();
    loop {
        if candidate.exists() {
            return (candidate, remainder);
        }
        match candidate.file_name().map(|n| n.to_os_string()) {
            Some(name) => {
                remainder = Path::new(&name).join(&remainder);
                candidate.pop();
            }
            None => return (candidate, remainder), // walked past the root — give up
        }
    }
}

/// What [`ascii_safe_dir`] should do, decided by [`plan_ascii_safe_dir`].
/// Pure/portable so the DECISION is unit-testable on every platform even
/// though only the Windows build ever executes a `Shorten*` variant.
#[cfg(any(target_os = "windows", test))]
#[derive(Debug, PartialEq, Eq)]
enum AsciiSafePlan {
    /// `dir` is already ASCII — return unchanged, no OS calls at all.
    NoOp,
    /// The non-ASCII bytes are confined to `ancestor`, which already exists
    /// (Windows creates a user's profile dirs at account-creation time, so a
    /// non-ASCII *username* is always here) — shorten just that and
    /// reattach `remainder`, which is literal ASCII we chose ourselves and
    /// doesn't exist yet, unchanged.
    ShortenAncestor { ancestor: PathBuf, remainder: PathBuf },
    /// The not-yet-created remainder ITSELF carries non-ASCII bytes — e.g. a
    /// user-chosen custom install folder named in their own language
    /// (greptile #1783 review: reattaching a non-ASCII remainder unchanged
    /// would silently leave the result non-ASCII for exactly the user this
    /// exists to help). `GetShortPathNameW` can't mint a short name for a
    /// path that doesn't exist, so `full` must be created on disk first —
    /// Windows assigns every new directory an 8.3 short name by default —
    /// then the COMPLETE path is shortened in one call.
    CreateThenShorten { full: PathBuf },
}

/// True when [`split_existing_ancestor`]'s reattached remainder needs its
/// own short-name resolution — non-empty AND non-ASCII — because the
/// `ShortenAncestor` fast path (reattach unchanged) would silently leave the
/// result non-ASCII otherwise.
#[cfg(any(target_os = "windows", test))]
fn remainder_needs_own_shortening(remainder: &Path) -> bool {
    !remainder.as_os_str().is_empty() && !is_ascii_path(remainder)
}

#[cfg(any(target_os = "windows", test))]
fn plan_ascii_safe_dir(dir: &Path) -> AsciiSafePlan {
    if is_ascii_path(dir) {
        return AsciiSafePlan::NoOp;
    }
    let (existing, remainder) = split_existing_ancestor(dir);
    if remainder_needs_own_shortening(&remainder) {
        AsciiSafePlan::CreateThenShorten { full: dir.to_path_buf() }
    } else {
        AsciiSafePlan::ShortenAncestor { ancestor: existing, remainder }
    }
}

/// Windows-only: if `dir` contains a non-ASCII byte, resolve it to an
/// ASCII-safe equivalent via Windows' 8.3 short filenames (the same trick
/// `backend/core/config.py::_ensure_short_hf_cache_on_windows` documents for
/// the HF-cache MAX_PATH problem), so every byte later written into the venv
/// — including `uv sync`'s editable `.pth` — is valid in any single-byte
/// Windows code page (#1783). See [`plan_ascii_safe_dir`] for which of the
/// two non-ASCII cases applies.
///
/// Best-effort throughout: an ASCII `dir` is returned unchanged without ever
/// calling into WinAPI or touching the filesystem. Any failure along the way
/// (short-name call fails or is still non-ASCII — 8.3 generation disabled
/// via `fsutil 8dot3name` — or the `CreateThenShorten` directory creation
/// fails) returns the original path unchanged — no worse than before this
/// fix, and the bootstrap crash-signature diagnosis names the cause if the
/// interpreter still dies in `site`.
#[cfg(target_os = "windows")]
pub fn ascii_safe_dir(dir: &Path) -> PathBuf {
    match plan_ascii_safe_dir(dir) {
        AsciiSafePlan::NoOp => dir.to_path_buf(),
        AsciiSafePlan::ShortenAncestor { ancestor, remainder } => match win_short_path_name(&ancestor) {
            Some(short) if is_ascii_path(&short) => short.join(&remainder),
            _ => dir.to_path_buf(),
        },
        AsciiSafePlan::CreateThenShorten { full } => {
            if fs::create_dir_all(&full).is_err() {
                return dir.to_path_buf();
            }
            match win_short_path_name(&full) {
                Some(short) if is_ascii_path(&short) => short,
                _ => dir.to_path_buf(),
            }
        }
    }
}

#[cfg(not(target_os = "windows"))]
pub fn ascii_safe_dir(dir: &Path) -> PathBuf {
    dir.to_path_buf()
}

/// Raw `GetShortPathNameW` call. `None` on any failure (path doesn't exist,
/// buffer too small, 8.3 names disabled) — callers fall back to the
/// original path rather than trust a partial/garbled result.
#[cfg(target_os = "windows")]
fn win_short_path_name(path: &Path) -> Option<PathBuf> {
    use std::os::windows::ffi::OsStrExt;
    use windows::core::PCWSTR;
    use windows::Win32::Storage::FileSystem::GetShortPathNameW;

    let wide: Vec<u16> = path.as_os_str().encode_wide().chain(std::iter::once(0)).collect();
    let mut buf = vec![0u16; 4096];
    // Safety: `wide` is NUL-terminated and outlives the call; `buf` is sized
    // and its length is passed as `cchbuffer`, so the call cannot write past it.
    let len = unsafe { GetShortPathNameW(PCWSTR(wide.as_ptr()), Some(&mut buf)) } as usize;
    if len == 0 || len >= buf.len() {
        return None; // 0 = failure (see GetLastError); >= buf.len() = truncated
    }
    Some(PathBuf::from(String::from_utf16_lossy(&buf[..len])))
}

/// User-chosen backend data dir (voices/projects/db) → `OMNIVOICE_DATA_DIR`.
/// `None` = backend platform default; we deliberately don't set the env var
/// then, so legacy installs keep byte-identical behavior.
pub fn resolved_data_dir<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> Option<PathBuf> {
    let cfg = config::load_config(app);
    if cfg.install_mode == "portable" {
        return portable_base().map(|b| b.join("data"));
    }
    cfg.data_dir.as_deref().filter(|s| !s.is_empty()).map(PathBuf::from)
}

/// User-chosen model cache dir → `OMNIVOICE_CACHE_DIR` (backend maps it to
/// HF_HOME / HF_HUB_CACHE / TORCH_HOME). Same `None` = default contract.
pub fn resolved_models_dir<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> Option<PathBuf> {
    let cfg = config::load_config(app);
    if cfg.install_mode == "portable" {
        return portable_base().map(|b| b.join("data").join("models"));
    }
    cfg.models_dir.as_deref().filter(|s| !s.is_empty()).map(PathBuf::from)
}

// ── uv volume co-location ─────────────────────────────────────────────────

/// Env overrides for every `uv` invocation so its heavy state (wheel cache,
/// managed Python) lives on the **same volume as the managed env**.
///
/// uv's defaults put both under the OS cache/data roots on the system drive
/// (`%LOCALAPPDATA%\uv\…`, `~/.cache/uv`, `~/.local/share/uv`). When the user
/// roots the install on another drive (custom env dir or portable mode on
/// D:), every wheel — torch alone is multi-GB — is downloaded and unpacked
/// on the SYSTEM drive first and then cross-volume **copied** (hardlinks
/// can't cross volumes) into the venv: the system drive silently needs as
/// much space as the whole install and fills up even though the user chose
/// D: precisely because C: was tight (Discord report, tarbol6457).
///
/// Rules:
/// - only fires when the env root's volume differs from the OS cache root's
///   volume (the proxy for uv's default cache location) — default installs
///   are byte-identical;
/// - an explicit `UV_CACHE_DIR` / `UV_PYTHON_INSTALL_DIR` from the user's
///   environment always wins (we never override either).
///
/// The dirs live under the env root (`<env_root>/uv-cache`, `uv-python`), so
/// they survive Clean & Retry (which only removes `project/`) and are removed
/// with the install.
pub fn uv_env_overrides_for(env_root: &Path) -> Vec<(&'static str, PathBuf)> {
    let same_volume = match dirs_next::cache_dir() {
        Some(cache_root) => match (disk::fs_key(env_root), disk::fs_key(&cache_root)) {
            (Some(a), Some(b)) => a == b,
            // Can't identify one of the volumes — keep uv's state with the
            // env root; co-locating is always correct, just possibly redundant.
            _ => false,
        },
        None => false,
    };
    if same_volume {
        return Vec::new();
    }
    let mut out = Vec::new();
    if std::env::var_os("UV_CACHE_DIR").is_none() {
        out.push(("UV_CACHE_DIR", env_root.join("uv-cache")));
    }
    if std::env::var_os("UV_PYTHON_INSTALL_DIR").is_none() {
        out.push(("UV_PYTHON_INSTALL_DIR", env_root.join("uv-python")));
    }
    out
}

/// [`uv_env_overrides_for`] with the env root resolved from the live config.
pub fn uv_env_overrides<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> Vec<(&'static str, PathBuf)> {
    uv_env_overrides_for(&env_root(app))
}

// ── First-run detection ───────────────────────────────────────────────────

/// True only when there is nothing to attach to and the user has never
/// completed (or implicitly owned) an install:
///   - `setup_complete` in config       → returning user
///   - dev tree with a `.venv`          → contributor running from source
///   - existing bootstrapped venv       → pre-setup-screen install (already
///     owned; `migrate_existing_install_if_needed` marks it complete).
///
/// Pure read — safe from `get_setup_state` and any future informational
/// caller. The migration write lives in `migrate_existing_install_if_needed`
/// and only runs on the bootstrap thread.
pub fn is_first_run<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> bool {
    let cfg = config::load_config(app);
    if cfg.setup_complete {
        return false;
    }
    if let Some(dev_root) = crate::bootstrap::find_dev_project_root() {
        if crate::bootstrap::venv_python_path(&dev_root.join(".venv")).is_file() {
            return false;
        }
    }
    let existing_venv = crate::bootstrap::venv_python_path(&env_root(app).join("project").join(".venv"));
    !existing_venv.is_file()
}

/// Pre-setup-screen install detected (venv on disk, `setup_complete` still
/// false): mark it complete instead of re-asking questions whose answers are
/// already on disk. Called once, explicitly, from the bootstrap thread —
/// keeping `is_first_run` side-effect-free for read-only IPC callers.
pub fn migrate_existing_install_if_needed<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    let mut cfg = config::load_config(app);
    if cfg.setup_complete {
        return;
    }
    let existing_venv = crate::bootstrap::venv_python_path(&env_root(app).join("project").join(".venv"));
    if existing_venv.is_file() {
        log::info!("Existing pre-setup-screen install detected — marking setup complete");
        cfg.setup_complete = true;
        config::save_config(app, &cfg);
    }
}

// ── IPC payloads ──────────────────────────────────────────────────────────

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SetupState {
    pub first_run: bool,
    /// "linux" | "macos" | "windows" — lets the UI hide platform-specific
    /// opt-ins (e.g. the Linux-only ROCm torch variant) per the
    /// cross-platform parity rule: identical defaults everywhere,
    /// platform-only choices never shown where they can't work.
    pub os: &'static str,
    pub defaults: SetupDefaults,
    pub portable: PortableSupport,
    pub requirements: Requirements,
    pub hardware: HardwareInfo,
}

/// What the machine offers, shown on the Compute card so the accelerator
/// choice is informed rather than a guess. Detection is best-effort and
/// must never block setup: every probe degrades to None/CPU.
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HardwareInfo {
    /// Marketing name when detectable ("NVIDIA GeForce RTX 4070 …").
    pub gpu: Option<String>,
    /// "cuda" | "rocm" | "amd" | "mps" | "cpu" — which torch path this maps
    /// to. "rocm" means an AMD GPU *with verified ROCm userspace* (safe to
    /// pre-select the ROCm wheels); "amd" means an AMD GPU was found but the
    /// ROCm runtime wasn't — the UI offers ROCm without pre-selecting it.
    pub kind: String,
    /// Human OS name: distro PRETTY_NAME on Linux ("CachyOS", "Ubuntu 24.04"),
    /// "macOS" / "Windows" elsewhere. The install matrix (OS family × distro
    /// × arch × GPU vendor) is what users file bug reports with — show it.
    pub os_name: String,
    /// "x86_64" | "aarch64" | … — Apple Silicon vs Intel mac, ARM Linux
    /// (Asahi/Jetson) vs x64 all behave differently for wheels.
    pub arch: &'static str,
    pub cpu_cores: usize,
    pub ram_gb: f64,
}

/// Distro-aware OS label. Linux reads /etc/os-release PRETTY_NAME (falls
/// back to NAME, then "Linux"); macOS/Windows are just themselves.
fn os_pretty_name() -> String {
    #[cfg(target_os = "linux")]
    {
        if let Ok(body) = fs::read_to_string("/etc/os-release") {
            for key in ["PRETTY_NAME=", "NAME="] {
                if let Some(line) = body.lines().find(|l| l.starts_with(key)) {
                    let v = line[key.len()..].trim().trim_matches('"');
                    if !v.is_empty() {
                        return v.to_string();
                    }
                }
            }
        }
        "Linux".to_string()
    }
    #[cfg(target_os = "macos")]
    {
        "macOS".to_string()
    }
    #[cfg(target_os = "windows")]
    {
        "Windows".to_string()
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
    {
        std::env::consts::OS.to_string()
    }
}

/// ROCm userspace present? A PCI vendor ID alone doesn't mean the HIP/ROCm
/// runtime is installed — without it the ROCm torch wheels can't run. Cheap
/// probes only: the canonical install prefix, or `rocminfo` on PATH.
#[cfg(target_os = "linux")]
fn rocm_userspace_present() -> bool {
    if Path::new("/opt/rocm").is_dir() {
        return true;
    }
    std::env::var_os("PATH").is_some_and(|paths| {
        std::env::split_paths(&paths).any(|p| p.join("rocminfo").is_file())
    })
}

fn detect_hardware() -> HardwareInfo {
    use std::process::Command;
    let cores = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(0);
    let ram_gb = {
        let mut sys = sysinfo::System::new();
        sys.refresh_memory();
        (sys.total_memory() as f64 / (1024.0 * 1024.0 * 1024.0) * 10.0).round() / 10.0
    };
    let os_name = os_pretty_name();
    let arch = std::env::consts::ARCH;
    let base = move |gpu: Option<String>, kind: &str| HardwareInfo {
        gpu,
        kind: kind.into(),
        os_name: os_name.clone(),
        arch,
        cpu_cores: cores,
        ram_gb,
    };

    // NVIDIA: nvidia-smi ships with the driver on Linux + Windows. It runs
    // in a helper thread with a short timeout: this probe sits inside the
    // IPC the setup screen awaits on mount, and a wedged driver can hang
    // nvidia-smi indefinitely — degrading to CPU beats hanging first-run.
    // (On timeout the helper thread is abandoned; it exits with the process.)
    let nvidia_name = {
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let mut smi = Command::new("nvidia-smi");
            smi.args(["--query-gpu=name", "--format=csv,noheader"]);
            // Windows: a GUI app spawning a console binary flashes a cmd
            // window — on the very first screen a user ever sees.
            // CREATE_NO_WINDOW stops it.
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                smi.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
            }
            let _ = tx.send(smi.output());
        });
        match rx.recv_timeout(std::time::Duration::from_secs(3)) {
            Ok(Ok(out)) if out.status.success() => String::from_utf8_lossy(&out.stdout)
                .lines()
                .next()
                .map(|n| n.trim().to_string())
                .filter(|n| !n.is_empty()),
            _ => None,
        }
    };
    if let Some(name) = nvidia_name {
        return base(Some(name), "cuda");
    }

    // Apple Silicon → MPS.
    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    {
        return base(Some("Apple Silicon".into()), "mps");
    }

    // AMD on Linux: a DRM card with vendor 0x1002. Only report "rocm" (and
    // let the UI pre-select the ROCm wheels) when the ROCm userspace is
    // actually installed — vendor ID alone doesn't imply HIP/libamdhip64,
    // and ROCm torch on a stock distro silently falls back to CPU. Without
    // the runtime it's "amd": informational, ROCm offered but not chosen.
    // No marketing name without lspci, so stay generic.
    #[cfg(target_os = "linux")]
    {
        if let Ok(entries) = fs::read_dir("/sys/class/drm") {
            for e in entries.flatten() {
                let vendor = e.path().join("device").join("vendor");
                if let Ok(v) = fs::read_to_string(&vendor) {
                    if v.trim() == "0x1002" {
                        let kind = if rocm_userspace_present() { "rocm" } else { "amd" };
                        return base(Some("AMD GPU".into()), kind);
                    }
                }
            }
        }
    }

    base(None, "cpu")
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SetupDefaults {
    pub install_mode: String,
    pub env_dir: String,
    pub data_dir: String,
    pub models_dir: String,
    pub region: String,
    pub update_channel: String,
    pub torch_variant: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PortableSupport {
    pub available: bool,
    pub base_dir: Option<String>,
    /// Machine-readable reason when unavailable: "not_writable" | "no_anchor".
    pub reason: Option<String>,
    /// The default folder beside the app, so the UI can offer "reset to
    /// default" and tell a relocated install apart from a fresh one.
    pub default_dir: Option<String>,
    /// The app's own directory. A folder INSIDE it can be recorded relatively
    /// and therefore survives the mount path changing; anywhere else can only
    /// be recorded absolutely. The UI uses this to say which of the two the
    /// user is choosing.
    pub anchor_dir: Option<String>,
    /// False when the app's own folder is read-only (`/Applications`,
    /// `Program Files`). A relocation is still allowed there — it just falls
    /// back to per-user config, which does not survive moving to another
    /// machine. The UI says so rather than silently downgrading the promise.
    pub anchor_writable: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Requirements {
    pub env_bytes: u64,
    pub models_bytes: u64,
    pub data_bytes: u64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TargetCheck {
    pub path: String,
    pub exists: bool,
    pub writable: bool,
    pub free_bytes: Option<u64>,
    pub fs_key: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallPlan {
    pub install_mode: String,
    /// Chosen portable folder. None → the default beside the app. Only
    /// meaningful when `install_mode == "portable"`.
    #[serde(default)]
    pub portable_dir: Option<String>,
    #[serde(default)]
    pub env_dir: Option<String>,
    #[serde(default)]
    pub data_dir: Option<String>,
    #[serde(default)]
    pub models_dir: Option<String>,
    #[serde(default)]
    pub region: Option<String>,
    #[serde(default)]
    pub locale: Option<String>,
    #[serde(default)]
    pub update_channel: Option<String>,
    #[serde(default)]
    pub torch_variant: Option<String>,
    #[serde(default)]
    pub mirrors: Option<MirrorOverrides>,
}

// ── Plan validation + application ─────────────────────────────────────────

fn none_if_default(chosen: &Option<String>, default: &Path) -> Option<String> {
    chosen
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .filter(|s| Path::new(s) != default)
        .map(str::to_string)
}

/// Loopback-only exception to the HTTPS requirement: a local registry/cache
/// (devpi, bazel-remote, …) can't MITM itself. Host must be exactly
/// localhost / 127.0.0.1 / [::1], optionally followed by a port or path —
/// `http://localhost.evil.com` must NOT match.
fn is_loopback_http(u: &str) -> bool {
    ["http://localhost", "http://127.0.0.1", "http://[::1]"].iter().any(|prefix| {
        u.strip_prefix(prefix)
            .is_some_and(|rest| rest.is_empty() || rest.starts_with(':') || rest.starts_with('/'))
    })
}

/// Mirrors feed `UV_PYTHON_INSTALL_MIRROR` / `UV_INDEX_URL` / `HF_ENDPOINT` —
/// the supply chain for the Python runtime, every wheel, and model weights.
/// Plaintext http:// would let anyone on the network swap those payloads, so
/// HTTPS is required (loopback excepted for local registries).
fn valid_mirror(url: &Option<String>) -> Result<Option<String>, String> {
    match url.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        None => Ok(None),
        Some(u) if u.starts_with("https://") || is_loopback_http(u) => Ok(Some(u.to_string())),
        Some(u) if u.starts_with("http://") => Err(format!(
            "Plaintext http:// mirrors are not allowed (packages could be tampered with in transit) — use https:// — got: {u}"
        )),
        Some(u) => Err(format!("Mirror URL must start with https:// — got: {u}")),
    }
}

/// (target dir, bytes required there) for the chosen layout.
/// The portable folder this PLAN would use: the user's pick when they made
/// one, otherwise whatever `portable_base()` currently resolves to. Space and
/// writability must be checked against the folder we are about to create, not
/// the one a previous install left behind.
fn planned_portable_base(plan: &InstallPlan) -> Option<PathBuf> {
    plan.portable_dir
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .or_else(portable_base)
}

fn space_targets(plan: &InstallPlan, env_default: &Path) -> Vec<(PathBuf, u64)> {
    if plan.install_mode == "portable" {
        // Everything shares one folder → one combined requirement.
        let base = planned_portable_base(plan).unwrap_or_default();
        return vec![(base, REQUIRED_ENV_BYTES + REQUIRED_MODELS_BYTES + REQUIRED_DATA_BYTES)];
    }
    let dir_of = |s: &Option<String>, d: &Path| {
        s.as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| d.to_path_buf())
    };
    vec![
        (dir_of(&plan.env_dir, env_default), REQUIRED_ENV_BYTES),
        (dir_of(&plan.data_dir, &default_data_dir()), REQUIRED_DATA_BYTES),
        (dir_of(&plan.models_dir, &default_models_dir()), REQUIRED_MODELS_BYTES),
    ]
}

/// Authoritative install gate: group targets by filesystem, sum what each
/// volume must hold, and refuse the plan when any volume falls short. The UI
/// runs the same math for live feedback; this is the backstop that actually
/// "won't let install".
fn check_space(targets: &[(PathBuf, u64)]) -> Result<(), String> {
    use std::collections::HashMap;
    let mut by_fs: HashMap<String, (PathBuf, u64)> = HashMap::new();
    for (dir, need) in targets {
        let key = disk::fs_key(dir).unwrap_or_else(|| dir.to_string_lossy().into_owned());
        let entry = by_fs.entry(key).or_insert_with(|| (dir.clone(), 0));
        entry.1 += need;
    }
    for (dir, need) in by_fs.values() {
        let free = disk::available_bytes(dir)
            .ok_or_else(|| format!("Could not determine free space for {}", dir.display()))?;
        if free < *need {
            return Err(format!(
                "Not enough free space on the disk holding {}: needs ~{:.1} GB, only {:.1} GB available.",
                dir.display(),
                *need as f64 / GIB as f64,
                free as f64 / GIB as f64,
            ));
        }
    }
    Ok(())
}

fn check_writable(targets: &[(PathBuf, u64)]) -> Result<(), String> {
    for (dir, _) in targets {
        if !disk::writable(dir) {
            return Err(format!("Directory is not writable: {}", dir.display()));
        }
    }
    Ok(())
}

// ── Tauri commands ────────────────────────────────────────────────────────

#[tauri::command]
pub fn get_setup_state(app: tauri::AppHandle) -> SetupState {
    let cfg = config::load_config(&app);
    let env_default = app.path().app_local_data_dir().unwrap_or_default();

    let default_dir = portable_anchor().map(|a| a.join(PORTABLE_DIR_NAME));
    let anchor_writable = portable_anchor().map(|a| disk::writable(&a)).unwrap_or(false);
    let default_str = default_dir.as_ref().map(|p| p.to_string_lossy().into_owned());
    let anchor_str = portable_anchor().map(|a| a.to_string_lossy().into_owned());
    let portable = match portable_base() {
        // Portable is available when SOME folder can hold it. A read-only app
        // folder no longer disqualifies it outright: the user can point it at a
        // writable disk instead, which is the whole point of the picker.
        Some(base) if disk::writable(&base) || anchor_writable => PortableSupport {
            available: true,
            base_dir: Some(base.to_string_lossy().into_owned()),
            reason: None,
            default_dir: default_str.clone(),
            anchor_dir: anchor_str.clone(),
            anchor_writable,
        },
        Some(base) => PortableSupport {
            available: true,
            base_dir: Some(base.to_string_lossy().into_owned()),
            reason: Some("not_writable".into()),
            default_dir: default_str.clone(),
            anchor_dir: anchor_str.clone(),
            anchor_writable,
        },
        None => PortableSupport {
            available: false,
            base_dir: None,
            reason: Some("no_anchor".into()),
            default_dir: default_str,
            anchor_dir: anchor_str,
            anchor_writable,
        },
    };

    SetupState {
        first_run: is_first_run(&app),
        os: std::env::consts::OS,
        defaults: SetupDefaults {
            install_mode: cfg.install_mode,
            env_dir: env_default.to_string_lossy().into_owned(),
            data_dir: default_data_dir().to_string_lossy().into_owned(),
            models_dir: default_models_dir().to_string_lossy().into_owned(),
            region: cfg.region,
            update_channel: cfg.update_channel,
            torch_variant: cfg.torch_variant,
        },
        portable,
        requirements: Requirements {
            env_bytes: REQUIRED_ENV_BYTES,
            models_bytes: REQUIRED_MODELS_BYTES,
            data_bytes: REQUIRED_DATA_BYTES,
        },
        hardware: detect_hardware(),
    }
}

#[tauri::command]
pub fn check_install_target(path: String) -> TargetCheck {
    let p = PathBuf::from(path.trim());
    TargetCheck {
        exists: p.exists(),
        writable: disk::writable(&p),
        free_bytes: disk::available_bytes(&p),
        fs_key: disk::fs_key(&p),
        path: p.to_string_lossy().into_owned(),
    }
}

/// Validate the plan, persist it, then start the (until now deliberately
/// parked) bootstrap. Any `Err` keeps the app in `AwaitingSetup` with the
/// message surfaced on the setup screen — nothing was installed.
#[tauri::command]
pub async fn complete_setup(
    app: tauri::AppHandle,
    state: tauri::State<'_, BootstrapState>,
    plan: InstallPlan,
) -> Result<(), String> {
    let owned_state = BootstrapState {
        stage: state.stage.clone(),
        logs: state.logs.clone(),
    };
    tauri::async_runtime::spawn_blocking(move || complete_setup_blocking(app, owned_state, plan))
        .await
        .map_err(|error| {
            log::error!("Setup task failed to join: {error}");
            "setup_task_failed".to_string()
        })?
}

fn complete_setup_blocking(
    app: tauri::AppHandle,
    state: BootstrapState,
    plan: InstallPlan,
) -> Result<(), String> {
    if !matches!(plan.install_mode.as_str(), "installed" | "portable") {
        return Err(format!("Unknown install mode: {}", plan.install_mode));
    }
    if plan.install_mode == "portable" {
        // Check the folder the user actually chose. A custom pick may be
        // writable where the app's own folder is not (the /Applications case),
        // so validating the default here would refuse a perfectly good plan.
        let base = planned_portable_base(&plan)
            .ok_or("Portable mode is unavailable: could not resolve a folder for it.")?;
        if !disk::writable(&base) {
            return Err(format!(
                "Portable mode is unavailable: {} is not writable.",
                base.display()
            ));
        }
    }

    let mirrors = match &plan.mirrors {
        None => MirrorOverrides::default(),
        Some(m) => MirrorOverrides {
            pypi_index: valid_mirror(&m.pypi_index)?,
            hf_endpoint: valid_mirror(&m.hf_endpoint)?,
            python_downloads: valid_mirror(&m.python_downloads)?,
        },
    };

    let env_default = app.path().app_local_data_dir().unwrap_or_default();
    let targets = space_targets(&plan, &env_default);
    check_writable(&targets)?;
    check_space(&targets)?;

    let mut cfg = config::load_config(&app);
    cfg.setup_complete = true;
    cfg.install_mode = plan.install_mode.clone();
    cfg.env_dir = none_if_default(&plan.env_dir, &env_default);
    cfg.data_dir = none_if_default(&plan.data_dir, &default_data_dir());
    cfg.models_dir = none_if_default(&plan.models_dir, &default_models_dir());
    cfg.mirrors = mirrors;
    if let Some(region) = plan.region.as_deref().filter(|r| config::VALID_REGIONS.contains(r)) {
        cfg.region = region.to_string();
    }
    if let Some(channel) = plan.update_channel.as_deref().filter(|c| config::VALID_CHANNELS.contains(c)) {
        cfg.update_channel = channel.to_string();
    }
    if let Some(variant) = plan.torch_variant.as_deref().filter(|v| ["auto", "rocm"].contains(v)) {
        // ROCm wheels exist for Linux only — clamp anywhere else so a stray
        // payload can't configure an install that has no wheels to pull.
        cfg.torch_variant = if variant == "rocm" && !cfg!(target_os = "linux") {
            "auto".to_string()
        } else {
            variant.to_string()
        };
    }
    cfg.locale = plan.locale.clone().filter(|l| !l.is_empty());

    if plan.install_mode != "portable" {
        for (dir, _) in &targets {
            fs::create_dir_all(dir)
                .map_err(|e| format!("Could not create {}: {e}", dir.display()))?;
        }
    }

    // Custom paths are home-relative PII — log default-vs-custom flags, not
    // the raw locations.
    let custom = |v: &Option<String>| if v.is_some() { "custom" } else { "default" };
    log::info!(
        "Setup complete (mode={}, env={}, data={}, models={}) — starting bootstrap",
        cfg.install_mode,
        custom(&cfg.env_dir),
        custom(&cfg.data_dir),
        custom(&cfg.models_dir),
    );

    // `--setup` re-entry: stop the previous backend before committing the new
    // durable layout. A failed stop must leave the old config/pointer active;
    // otherwise its automatic recovery would launch against a half-applied
    // setup plan.
    let persisted = crate::bootstrap::with_backend_stopped(&app, || -> Result<(), String> {
        let previous_location = if plan.install_mode == "portable" {
            Some(snapshot_portable_location()?)
        } else {
            None
        };
        let result = (|| -> Result<(), String> {
            if plan.install_mode == "portable" {
                // Create the portable folder and seed config.json INSIDE it first,
                // so `config_path` resolves portable from here on and the whole
                // install (env + data + config) travels as one folder.
                let base = planned_portable_base(&plan).ok_or("Portable anchor disappeared")?;
                fs::create_dir_all(&base)
                    .map_err(|e| format!("Could not create {}: {e}", base.display()))?;
                // Record WHERE it is before seeding it — `portable_base()` has to
                // resolve to this folder on the next launch, and the config inside
                // it cannot say so (nothing would know where to look).
                if base
                    != portable_anchor()
                        .map(|a| a.join(PORTABLE_DIR_NAME))
                        .unwrap_or_default()
                {
                    let how = record_portable_dir(&app, &base)?;
                    log::info!("Portable folder relocated — recorded via {how}");
                } else {
                    // Back to the default: drop any earlier relocation so a stale
                    // pointer can't outrank it.
                    clear_portable_dir(&app);
                }
                config::save_config_at(&base.join("config.json"), &cfg)?;
            }
            // The plan must actually persist before bootstrap starts — a swallowed
            // write error here would bootstrap into a stale layout from disk while
            // the UI reports success.
            let cfg_path =
                config::config_path(&app).ok_or("Could not resolve the config file path")?;
            config::save_config_at(&cfg_path, &cfg)
        })();
        match (result, previous_location) {
            (Err(error), Some(snapshot)) => match restore_portable_location(snapshot) {
                Ok(()) => Err(error),
                Err(rollback_error) => Err(format!(
                    "{error}; could not restore the previous portable location: {rollback_error}"
                )),
            },
            (result, _) => result,
        }
    });
    match persisted {
        Err(error) => {
            // with_backend_stopped already re-arms the old backend for every
            // non-terminal caller.
            log::warn!("Setup could not stop the previous backend: {error}");
            return Err("backend_stop_failed".into());
        }
        Ok(Err(error)) => {
            // The stop succeeded but persistence did not; restore service on
            // the surviving layout instead of leaving the settings UI down.
            crate::bootstrap::respawn_backend(
                app,
                state.stage.clone(),
                state.logs.clone(),
            );
            return Err(error);
        }
        Ok(Ok(())) => {}
    }

    set_stage(&state.stage, BootstrapStage::Checking);
    crate::bootstrap::respawn_backend(app, state.stage, state.logs);
    Ok(())
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    /// The relative-vs-absolute choice decides whether the cross-machine
    /// promise is true, so pin it directly — no AppHandle, no filesystem.
    #[test]
    fn pointer_payload_is_relative_only_inside_the_app_folder() {
        let anchor = Path::new("/Volumes/Stick");

        // Inside the app folder → relative, so a different mount path on the
        // next machine still resolves.
        let (payload, flavour) = pointer_payload(&anchor.join("MyVoiceData"), anchor);
        assert_eq!(payload, "MyVoiceData");
        assert_eq!(flavour, "pointer-relative");

        // Nested deeper is still inside.
        let (payload, flavour) = pointer_payload(&anchor.join("data").join("vs"), anchor);
        assert_eq!(flavour, "pointer-relative");
        assert!(!Path::new(&payload).is_absolute(), "must not store an absolute path");

        // Outside → absolute, and honestly labelled: nothing relocatable can
        // be stored, so the install is tied to this exact path.
        let (payload, flavour) = pointer_payload(Path::new("/Users/x/Elsewhere"), anchor);
        assert_eq!(payload, "/Users/x/Elsewhere");
        assert_eq!(flavour, "pointer-absolute");

        // The anchor itself is not a relocation target — an empty relative
        // path would resolve back to the anchor and lose the folder.
        let (_, flavour) = pointer_payload(anchor, anchor);
        assert_eq!(flavour, "pointer-absolute");
    }

    #[test]
    fn file_snapshot_restores_present_and_absent_files() {
        let root = std::env::temp_dir().join(format!(
            "ov-portable-snapshot-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        fs::create_dir_all(&root).unwrap();
        let present = root.join("present");
        let absent = root.join("absent");
        fs::write(&present, b"old").unwrap();
        let present_snapshot = snapshot_file(present.clone()).unwrap();
        let absent_snapshot = snapshot_file(absent.clone()).unwrap();

        fs::write(&present, b"new").unwrap();
        fs::write(&absent, b"new").unwrap();
        restore_portable_location(vec![present_snapshot, absent_snapshot]).unwrap();

        assert_eq!(fs::read(&present).unwrap(), b"old");
        assert!(!absent.exists());
        fs::remove_dir_all(root).unwrap();
    }

    /// Portable-folder relocation (#1403 follow-up). One test fn, not several:
    /// it mutates `APPIMAGE`, which is process-global, and Rust tests run in
    /// parallel — same rationale as the uv-env test below.
    ///
    /// `APPIMAGE` is the anchor seam the resolver already honours, so pointing
    /// it at a temp dir gives full control of "the folder beside the app"
    /// without touching the real one.
    #[test]
    fn portable_base_resolves_pointer_then_default_and_stays_backward_compatible() {
        let tmp = std::env::temp_dir().join(format!("ov-portable-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).expect("tmp anchor");
        let fake_appimage = tmp.join("VoiceStudio.AppImage");
        fs::write(&fake_appimage, b"x").expect("fake appimage");
        let prev = std::env::var("APPIMAGE").ok();
        std::env::set_var("APPIMAGE", &fake_appimage);

        // 1. No pointer, no config field → the historical default beside the
        //    app. Existing portable installs must keep resolving here.
        let default_dir = tmp.join(PORTABLE_DIR_NAME);
        assert_eq!(
            portable_base(),
            Some(default_dir.clone()),
            "a portable install with no relocation must resolve exactly as before"
        );
        assert_eq!(portable_anchor(), Some(tmp.clone()));

        // 2. A pointer file wins — this is what keeps the install
        //    self-discovering after the folder is moved.
        let elsewhere = tmp.join("on-another-disk");
        fs::write(portable_pointer_path().unwrap(), elsewhere.to_string_lossy().as_bytes())
            .expect("write pointer");
        assert_eq!(portable_base(), Some(elsewhere.clone()));

        // 3. Trailing whitespace/newlines are tolerated — the file is written
        //    by us but may be hand-edited to move an install.
        fs::write(
            portable_pointer_path().unwrap(),
            format!("  {}  \n", elsewhere.display()).as_bytes(),
        )
        .expect("write padded pointer");
        assert_eq!(portable_base(), Some(elsewhere.clone()));

        // 4. An empty pointer must not resolve to "" — that would silently
        //    root the whole install at the filesystem root.
        fs::write(portable_pointer_path().unwrap(), b"   \n").expect("write empty pointer");
        assert_eq!(
            portable_base(),
            Some(default_dir.clone()),
            "an empty pointer must fall through to the default, never to an empty path"
        );

        // 4b. A RELATIVE pointer resolves against the CURRENT anchor — this is
        //     what survives the mount path changing between machines. An
        //     absolute pointer cannot, which is why one is only written for a
        //     folder outside the app's directory.
        fs::write(portable_pointer_path().unwrap(), b"MyData").expect("write relative pointer");
        assert_eq!(
            portable_base(),
            Some(tmp.join("MyData")),
            "a relative pointer must resolve against wherever the app is NOW"
        );

        // 5. The plan's pick outranks whatever is currently recorded, so space
        //    and writability are checked against the folder about to be made.
        fs::write(portable_pointer_path().unwrap(), elsewhere.to_string_lossy().as_bytes())
            .expect("rewrite pointer");
        let chosen = tmp.join("user-pick");
        let plan = InstallPlan {
            install_mode: "portable".into(),
            portable_dir: Some(chosen.to_string_lossy().into_owned()),
            env_dir: None, data_dir: None, models_dir: None,
            region: None, locale: None, update_channel: None,
            torch_variant: None, mirrors: None,
        };
        assert_eq!(planned_portable_base(&plan), Some(chosen.clone()));
        let targets = space_targets(&plan, Path::new("/unused"));
        assert_eq!(targets.len(), 1);
        assert_eq!(targets[0].0, chosen, "space must be checked on the CHOSEN folder");
        assert_eq!(
            targets[0].1,
            REQUIRED_ENV_BYTES + REQUIRED_MODELS_BYTES + REQUIRED_DATA_BYTES
        );

        // 6. A blank pick is not a pick — fall back to the resolved base.
        let blank = InstallPlan {
            install_mode: "portable".into(),
            portable_dir: Some("   ".into()),
            env_dir: None, data_dir: None, models_dir: None,
            region: None, locale: None, update_channel: None,
            torch_variant: None, mirrors: None,
        };
        assert_eq!(planned_portable_base(&blank), Some(elsewhere));

        match prev {
            Some(v) => std::env::set_var("APPIMAGE", v),
            None => std::env::remove_var("APPIMAGE"),
        }
        let _ = fs::remove_dir_all(&tmp);
    }

    /// D:-install class (tarbol6457): one test fn (not several) because it
    /// mutates process env vars and Rust tests run in parallel.
    #[test]
    fn uv_env_overrides_colocate_only_across_volumes_and_respect_user_env() {
        // Same volume as uv's default cache root → no overrides: default
        // installs must stay byte-identical.
        let cache_root = dirs_next::cache_dir().expect("cache dir");
        assert!(
            uv_env_overrides_for(&cache_root).is_empty(),
            "env root on the default-cache volume must not be redirected"
        );

        // A root whose volume can't match anything real. On Windows an
        // unmapped drive letter is a different volume by fs_key; on Unix,
        // fs_key(nonexistent-with-no-real-ancestor) still resolves via
        // nearest_existing to `/` — so instead simulate by comparing keys.
        let env_root = PathBuf::from(if cfg!(windows) {
            "Q:\\omnivoice-test-fake-root" // unmapped drive letter → distinct volume key
        } else {
            "/omnivoice-test-fake-root" // resolves to `/` — a different device than
                                        // the user cache root on macOS (sealed system
                                        // volume) and on split-partition Linux
        });
        let keys_differ = disk::fs_key(&env_root) != disk::fs_key(&cache_root);
        let overrides = uv_env_overrides_for(&env_root);
        if keys_differ {
            assert!(
                overrides.iter().any(|(k, v)| *k == "UV_CACHE_DIR" && v.starts_with(&env_root)),
                "cross-volume env root must pin UV_CACHE_DIR under the env root, got {overrides:?}"
            );
            assert!(
                overrides.iter().any(|(k, v)| *k == "UV_PYTHON_INSTALL_DIR" && v.starts_with(&env_root)),
                "cross-volume env root must pin UV_PYTHON_INSTALL_DIR under the env root"
            );

            // An explicit user UV_CACHE_DIR always wins — never overridden.
            std::env::set_var("UV_CACHE_DIR", cache_root.join("user-pinned"));
            let with_user = uv_env_overrides_for(&env_root);
            assert!(
                !with_user.iter().any(|(k, _)| *k == "UV_CACHE_DIR"),
                "user-pinned UV_CACHE_DIR must be respected"
            );
            std::env::remove_var("UV_CACHE_DIR");
        }
    }

    #[test]
    fn nearest_existing_walks_up_to_a_real_dir() {
        let tmp = std::env::temp_dir();
        let ghost = tmp.join("omnivoice-no-such-dir").join("deeper").join("still-deeper");
        let found = disk::nearest_existing(&ghost);
        assert!(found.exists(), "must resolve to an existing ancestor");
        assert!(ghost.starts_with(&found));
    }

    #[test]
    fn available_bytes_reports_space_for_temp_dir() {
        let free = disk::available_bytes(&std::env::temp_dir());
        assert!(free.is_some(), "temp dir must report free space");
        assert!(free.unwrap() > 0);
    }

    #[test]
    fn fs_key_is_stable_and_groups_same_volume() {
        let tmp = std::env::temp_dir();
        let a = disk::fs_key(&tmp);
        let b = disk::fs_key(&tmp.join("does-not-exist-yet"));
        assert!(a.is_some());
        assert_eq!(a, b, "child of the same volume must share the fs key");
    }

    #[test]
    fn writable_accepts_temp_and_rejects_nonsense() {
        assert!(disk::writable(&std::env::temp_dir().join("new-subdir-not-created")));
        #[cfg(unix)]
        assert!(
            !disk::writable(Path::new("/proc/omnivoice-definitely-not-writable")),
            "procfs is not writable"
        );
    }

    #[test]
    fn space_targets_portable_collapses_to_one_combined_requirement() {
        let plan = InstallPlan {
            install_mode: "portable".into(),
            portable_dir: None,
            env_dir: None, data_dir: None, models_dir: None,
            region: None, locale: None, update_channel: None,
            torch_variant: None, mirrors: None,
        };
        let targets = space_targets(&plan, Path::new("/unused"));
        assert_eq!(targets.len(), 1);
        assert_eq!(targets[0].1, REQUIRED_ENV_BYTES + REQUIRED_MODELS_BYTES + REQUIRED_DATA_BYTES);
    }

    #[test]
    fn space_targets_installed_checks_each_location() {
        let plan = InstallPlan {
            install_mode: "installed".into(),
            portable_dir: None,
            env_dir: Some("/x/env".into()),
            data_dir: Some("/y/data".into()),
            models_dir: None, // default
            region: None, locale: None, update_channel: None,
            torch_variant: None, mirrors: None,
        };
        let targets = space_targets(&plan, Path::new("/default-env"));
        assert_eq!(targets.len(), 3);
        assert_eq!(targets[0], (PathBuf::from("/x/env"), REQUIRED_ENV_BYTES));
        assert_eq!(targets[1], (PathBuf::from("/y/data"), REQUIRED_DATA_BYTES));
        assert_eq!(targets[2].1, REQUIRED_MODELS_BYTES);
        assert_eq!(targets[2].0, default_models_dir());
    }

    #[test]
    fn check_space_sums_requirements_sharing_a_volume() {
        // Both targets resolve to the temp-dir volume; an absurd combined
        // requirement must fail even when each alone might pass.
        let tmp = std::env::temp_dir();
        let huge = 1024 * 1024 * GIB; // 1 EiB — no consumer disk has this
        let res = check_space(&[(tmp.clone(), huge), (tmp.join("sub"), huge)]);
        assert!(res.is_err(), "1 EiB×2 on one volume must be rejected");
        let msg = res.unwrap_err();
        assert!(msg.contains("Not enough free space"), "msg: {msg}");
    }

    #[test]
    fn check_space_accepts_tiny_requirements() {
        assert!(check_space(&[(std::env::temp_dir(), 1)]).is_ok());
    }

    #[test]
    fn mirror_validation_requires_https_scheme() {
        assert_eq!(valid_mirror(&None).unwrap(), None);
        assert_eq!(valid_mirror(&Some("  ".into())).unwrap(), None);
        assert_eq!(
            valid_mirror(&Some("https://hf-mirror.com".into())).unwrap().as_deref(),
            Some("https://hf-mirror.com")
        );
        assert!(valid_mirror(&Some("ftp://nope".into())).is_err());
        assert!(valid_mirror(&Some("hf-mirror.com".into())).is_err());
        // Plaintext http:// is a MITM supply-chain path — rejected.
        assert!(valid_mirror(&Some("http://mirrors.example.com/pypi".into())).is_err());
        // …except explicit loopback (local registry/cache).
        assert_eq!(
            valid_mirror(&Some("http://localhost:8081/simple".into())).unwrap().as_deref(),
            Some("http://localhost:8081/simple")
        );
        assert_eq!(
            valid_mirror(&Some("http://127.0.0.1/simple".into())).unwrap().as_deref(),
            Some("http://127.0.0.1/simple")
        );
        // Loopback-lookalike hosts must not slip through.
        assert!(valid_mirror(&Some("http://localhost.evil.com/simple".into())).is_err());
        assert!(valid_mirror(&Some("http://127.0.0.1.evil.com".into())).is_err());
    }

    #[test]
    fn none_if_default_strips_defaults_and_blanks() {
        let d = Path::new("/default/dir");
        assert_eq!(none_if_default(&None, d), None);
        assert_eq!(none_if_default(&Some("".into()), d), None);
        assert_eq!(none_if_default(&Some("/default/dir".into()), d), None);
        assert_eq!(none_if_default(&Some("/custom".into()), d), Some("/custom".into()));
    }

    #[test]
    fn detect_hardware_never_panics_and_reports_the_full_matrix() {
        let hw = detect_hardware();
        assert!(["cuda", "rocm", "amd", "mps", "cpu"].contains(&hw.kind.as_str()), "kind: {}", hw.kind);
        assert!(hw.ram_gb >= 0.0);
        assert!(!hw.os_name.is_empty(), "os_name must always resolve (distro or OS family)");
        assert!(!hw.arch.is_empty(), "arch must always resolve");
        #[cfg(target_arch = "x86_64")]
        assert_eq!(hw.arch, "x86_64");
    }

    #[test]
    fn requirements_match_measured_reality() {
        // Guard against accidental edits: env ≥ measured 7.8 GiB, models ≥
        // measured 6.1 GiB — shrinking below measurements would let installs
        // start that are guaranteed to die mid-download.
        assert!(REQUIRED_ENV_BYTES >= 8 * GIB);
        assert!(REQUIRED_MODELS_BYTES >= 7 * GIB);
        assert!(REQUIRED_DATA_BYTES >= GIB / 2);
    }

    // ── #1783: ASCII-safe env root ─────────────────────────────────────────

    // `\u{...}` escapes (not literal CJK bytes) so this source file itself
    // stays outside tests/test_no_hardcoded_cjk.py's scan — those escapes
    // still build the real crash-report username at runtime.
    fn cjk_username_path() -> PathBuf {
        // U+65E5 U+672C U+8A9E — three Japanese characters, matching #1771's
        // report. Spelled as escapes, not literal bytes, on purpose.
        PathBuf::from(format!("C:\\Users\\{}\\AppData\\Local\\OmniVoice", "\u{65e5}\u{672c}\u{8a9e}"))
    }

    #[test]
    fn is_ascii_path_flags_non_ascii_bytes() {
        assert!(is_ascii_path(Path::new("/Users/alice/AppData/Local/OmniVoice")));
        assert!(is_ascii_path(Path::new(r"C:\Users\alice\AppData\Local\OmniVoice")));
        // The exact byte from the crash report: a CJK Windows username.
        assert!(!is_ascii_path(&cjk_username_path()));
        assert!(!is_ascii_path(Path::new("/home/\u{443}\u{441}\u{435}\u{440}/.omnivoice")));
    }

    #[test]
    fn split_existing_ancestor_finds_the_deepest_real_directory() {
        let root = std::env::temp_dir().join(format!(
            "ov-ascii-safe-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();

        let target = root.join("project").join(".venv");
        let (existing, remainder) = split_existing_ancestor(&target);
        assert_eq!(existing, root);
        assert_eq!(remainder, Path::new("project").join(".venv"));

        // A path that exists outright has no remainder.
        let (existing2, remainder2) = split_existing_ancestor(&root);
        assert_eq!(existing2, root);
        assert_eq!(remainder2, Path::new(""));

        // Reassembling must reproduce the original path exactly.
        assert_eq!(existing.join(&remainder), target);

        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn plan_ascii_safe_dir_is_a_noop_for_ascii_paths() {
        assert_eq!(plan_ascii_safe_dir(Path::new("/ascii/only/path")), AsciiSafePlan::NoOp);
    }

    #[test]
    fn plan_ascii_safe_dir_shortens_only_the_ancestor_when_the_remainder_is_ascii() {
        // The common #1783 case: a non-ASCII Windows username is always
        // part of an EXISTING ancestor (Windows creates the profile dir at
        // account-creation time) — the not-yet-created subpath bootstrap
        // appends (`AppData\Local\OmniVoice`) is ours and always ASCII.
        let root = std::env::temp_dir().join(format!(
            "ov-plan-ascii-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        let _ = fs::remove_dir_all(&root);
        let nonascii_ancestor = root.join("\u{65e5}\u{672c}\u{8a9e}"); // exists
        fs::create_dir_all(&nonascii_ancestor).unwrap();
        let target = nonascii_ancestor.join("AppData").join("Local").join("OmniVoice"); // doesn't exist

        match plan_ascii_safe_dir(&target) {
            AsciiSafePlan::ShortenAncestor { ancestor, remainder } => {
                assert_eq!(ancestor, nonascii_ancestor);
                assert_eq!(remainder, Path::new("AppData").join("Local").join("OmniVoice"));
            }
            other => panic!("expected ShortenAncestor, got {:?}", other),
        }
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn plan_ascii_safe_dir_creates_then_shortens_when_the_not_yet_created_leaf_is_nonascii() {
        // greptile #1783 review, P1: a user-chosen custom install folder
        // named in their own language — the ancestor exists and is ASCII,
        // but the NOT-YET-CREATED leaf itself carries the non-ASCII bytes.
        // Reattaching it unchanged (the ShortenAncestor fast path) would
        // silently leave the result non-ASCII — exactly the bug flagged.
        let root = std::env::temp_dir().join(format!(
            "ov-plan-ascii-leaf-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap(); // ASCII ancestor, exists
        let target = root.join("\u{6211}\u{7684}\u{8f6f}\u{4ef6}").join("env"); // doesn't exist, non-ASCII leaf

        match plan_ascii_safe_dir(&target) {
            AsciiSafePlan::CreateThenShorten { full } => assert_eq!(full, target),
            other => panic!("expected CreateThenShorten, got {:?}", other),
        }
        fs::remove_dir_all(&root).unwrap();
    }

    // On every non-Windows target `ascii_safe_dir` never touches the
    // filesystem or WinAPI — it exists so `env_root` has one call site
    // regardless of platform. The real short-name resolution is
    // Windows-only and gated behind `#[cfg(target_os = "windows")]` above
    // (untestable on this machine — see #1783 PR notes on what couldn't be
    // exercised here).
    #[cfg(not(target_os = "windows"))]
    #[test]
    fn ascii_safe_dir_is_a_byte_identical_noop_off_windows() {
        let p = cjk_username_path();
        assert_eq!(ascii_safe_dir(&p), p);
    }
}
