//! Shared capability-scoped batch watch-folder scan and streaming upload.
//!
//! The watcher lives entirely on the client side of the app: the webview asks
//! this module (over Tauri IPC) for directory listings and asks Rust to stream
//! a settled file through the existing `POST /batch/enqueue` multipart route.
//! The Python backend only ever sees uploaded bytes — filesystem paths never
//! ride an HTTP request (same posture as `commands::authorize_host_path`).
//!
//! Access model: the folder is picked in a native dialog inside this process
//! and registered under a random session token, together with a `cap_std`
//! directory HANDLE opened at pick time. Scan/read commands resolve entries
//! relative to that handle — the pathname is never re-resolved for *access*,
//! so swapping the directory (or any component of its path) for a
//! symlink/junction later cannot redirect the watcher, on any OS. The stored
//! pathname is re-resolved only by the liveness/identity check, which stops
//! the watcher loudly when the folder is deleted, moved, or replaced. The
//! webview cannot point the commands at an arbitrary path; reads are confined
//! to files sitting directly in the folder the user explicitly picked this
//! session (non-recursive by design).
//!
//! Holding the handle must not lock the user's folder: `cap_std` opens
//! directories on Windows WITHOUT `FILE_SHARE_DELETE` (it pins the pathname
//! for its own path-based helpers), which would make Explorer refuse to
//! rename or delete a watched folder until the watch is stopped — a
//! Windows-only behaviour the other two platforms don't have. The handle is
//! therefore opened here with the full share mode (`open_dir_handle`), so
//! replacing the folder behaves identically everywhere: the OS allows it, the
//! next poll's identity check fails, and the UI stops the watcher.

use std::collections::HashMap;
use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::UNIX_EPOCH;

#[cfg(not(windows))]
use cap_std::ambient_authority;
use cap_std::fs::Dir;
use serde::Serialize;

/// An authorized watch folder: the directory handle everything resolves
/// against, plus the identity the folder had when the user picked it. The
/// handle is the security boundary (operations can never leave it); the
/// identity check is the LIVENESS signal — when the folder is deleted, moved,
/// or replaced, token resolution fails loudly and the UI stops the watcher
/// instead of polling silently forever.
struct WatchedDir {
    /// Fully-resolved directory path captured at pick time.
    canonical: PathBuf,
    /// Filesystem identity (device, inode) captured at pick time.
    #[cfg(unix)]
    identity: (u64, u64),
    /// Filesystem identity (volume serial, file index) captured at pick time.
    #[cfg(windows)]
    identity: (u32, u64),
    /// Directory handle captured at pick time — all scans/reads go through it.
    handle: Dir,
}

#[cfg(unix)]
fn dir_identity(meta: &fs::Metadata) -> (u64, u64) {
    use std::os::unix::fs::MetadataExt;
    (meta.dev(), meta.ino())
}

#[cfg(windows)]
fn dir_identity(dir: &Dir) -> Result<(u32, u64), String> {
    use std::os::windows::io::AsRawHandle;
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
    };

    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    // SAFETY: `dir` owns a live directory handle for the duration of this
    // call, and `info` is a valid writable output buffer.
    unsafe { GetFileInformationByHandle(HANDLE(dir.as_raw_handle()), &mut info) }
        .map_err(|_| "Selected watch folder identity could not be read".to_string())?;
    Ok((
        info.dwVolumeSerialNumber,
        ((info.nFileIndexHigh as u64) << 32) | info.nFileIndexLow as u64,
    ))
}

/// Open a directory handle for capability-scoped access.
///
/// Unix: `cap_std`'s own ambient open. Windows: the same
/// `FILE_FLAG_BACKUP_SEMANTICS` directory open `cap_std` performs, but with
/// `FILE_SHARE_DELETE` included so the user can still rename/delete the folder
/// while it is watched (see the module docs). Child opens stay handle-relative
/// (`CreateFileAtW` / `NtCreateFile` with a root directory) so confinement is
/// unaffected; only the liveness check observes the rename, which is the
/// intended signal.
#[cfg(not(windows))]
fn open_dir_handle(dir: &Path) -> io::Result<Dir> {
    Dir::open_ambient_dir(dir, ambient_authority())
}

#[cfg(windows)]
fn open_dir_handle(dir: &Path) -> io::Result<Dir> {
    use std::os::windows::fs::OpenOptionsExt;
    use windows::Win32::Storage::FileSystem::{
        FILE_FLAG_BACKUP_SEMANTICS, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE,
    };

    let file = fs::OpenOptions::new()
        .read(true)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS.0)
        .share_mode((FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE).0)
        .open(dir)?;
    if !file.metadata()?.is_dir() {
        return Err(io::Error::other("not a directory"));
    }
    Ok(Dir::from_std_file(file))
}

fn authorize_watched_dir(dir: &Path) -> Result<WatchedDir, String> {
    let canonical = fs::canonicalize(dir)
        .map_err(|e| format!("Selected watch folder could not be resolved: {e}"))?;
    if !canonical.is_dir() {
        return Err("Selected watch folder is not a directory".into());
    }
    let handle = open_dir_handle(&canonical)
        .map_err(|e| format!("Selected watch folder could not be opened: {e}"))?;
    #[cfg(unix)]
    let identity = dir_identity(
        &fs::metadata(&canonical)
            .map_err(|e| format!("Selected watch folder could not be inspected: {e}"))?,
    );
    #[cfg(windows)]
    let identity = dir_identity(&handle)?;
    Ok(WatchedDir {
        canonical,
        #[cfg(unix)]
        identity,
        #[cfg(windows)]
        identity,
        handle,
    })
}

/// Re-verify a watched folder's identity: the stored path must still resolve
/// to the same canonical target (and, on unix, the same device+inode). A
/// deleted, moved, replaced, or recreated directory fails here, which is what
/// stops the watcher loudly in the UI. Reads never depend on this check for
/// confinement — they go through the pinned handle regardless.
fn verify_watched_dir(watched: &WatchedDir) -> Result<(), String> {
    let canonical_now = fs::canonicalize(&watched.canonical)
        .map_err(|_| "Watched folder is no longer accessible".to_string())?;
    if canonical_now != watched.canonical {
        return Err("Watched folder changed identity".into());
    }
    #[cfg(unix)]
    {
        let meta = fs::metadata(&canonical_now)
            .map_err(|_| "Watched folder is no longer accessible".to_string())?;
        if dir_identity(&meta) != watched.identity {
            return Err("Watched folder changed identity".into());
        }
    }
    #[cfg(windows)]
    {
        let current = open_dir_handle(&canonical_now)
            .map_err(|_| "Watched folder is no longer accessible".to_string())?;
        if dir_identity(&current)? != watched.identity {
            return Err("Watched folder changed identity".into());
        }
    }
    Ok(())
}

fn registry() -> &'static Mutex<HashMap<String, WatchedDir>> {
    static WATCHED: OnceLock<Mutex<HashMap<String, WatchedDir>>> = OnceLock::new();
    WATCHED.get_or_init(|| Mutex::new(HashMap::new()))
}

#[derive(Serialize)]
pub struct WatchFolderSelection {
    token: String,
    path: String,
}

#[derive(Serialize)]
pub struct WatchEntry {
    name: String,
    size: u64,
    /// Modification time in ms since the Unix epoch (0 when unavailable).
    mtime: u64,
}

fn new_token() -> Result<String, String> {
    let mut random = [0_u8; 32];
    getrandom::fill(&mut random).map_err(|e| format!("Secure randomness unavailable: {e}"))?;
    Ok(random.iter().map(|b| format!("{b:02x}")).collect())
}

/// Resolve a session token to a clone of its pinned directory handle,
/// re-verifying the folder's liveness/identity on every access.
fn registered_dir(token: &str) -> Result<Dir, String> {
    let map = registry()
        .lock()
        .map_err(|_| "Watch-folder registry poisoned".to_string())?;
    let watched = map
        .get(token)
        .ok_or_else(|| "Watch folder is not authorized".to_string())?;
    verify_watched_dir(watched)?;
    watched
        .handle
        .try_clone()
        .map_err(|e| format!("Watched folder handle could not be reused: {e}"))
}

/// A directory entry name must be a single plain path component — anything
/// that could climb out of the watched folder is rejected. (The `cap_std`
/// handle would also refuse an escape; this keeps the error crisp and the
/// contract explicit.)
fn validate_entry_name(name: &str) -> Result<(), String> {
    if name.is_empty()
        || name == "."
        || name == ".."
        || name.contains('/')
        || name.contains('\\')
        || name.chars().any(|c| c.is_control())
    {
        return Err("Invalid watch-folder entry name".into());
    }
    Ok(())
}

fn mtime_ms(meta: &fs::Metadata) -> u64 {
    meta.modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn cap_mtime_ms(meta: &cap_std::fs::Metadata) -> u64 {
    meta.modified()
        .ok()
        .and_then(|t| t.into_std().duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// Non-recursive listing of the regular files in the watched folder (name,
/// size, mtime), resolved through the pinned handle. Symlinks are skipped
/// outright — the read path cannot follow them out of the folder anyway, so
/// listing them would only produce entries that can never be ingested.
fn scan_dir(dir: &Dir) -> Result<Vec<WatchEntry>, String> {
    let mut entries = Vec::new();
    let read = dir
        .entries()
        .map_err(|e| format!("Watched folder is unreadable: {e}"))?;
    for item in read.flatten() {
        let Ok(file_type) = item.file_type() else {
            continue;
        };
        if !file_type.is_file() {
            continue;
        }
        let Ok(meta) = item.metadata() else { continue };
        let Ok(name) = item.file_name().into_string() else {
            continue; // non-UTF-8 names can't round-trip through IPC; skip
        };
        entries.push(WatchEntry {
            name,
            size: meta.len(),
            mtime: cap_mtime_ms(&meta),
        });
    }
    Ok(entries)
}

/// Open a settled watched file through the pinned directory handle. A symlink
/// outside the folder cannot be opened, and the returned reader revalidates
/// size+mtime around every network read so a mutation aborts the upload.
fn open_watched_reader(
    dir: &Dir,
    name: &str,
    expected_size: u64,
    expected_mtime: u64,
) -> Result<SnapshotReader, String> {
    validate_entry_name(name)?;
    let file = dir
        .open(name)
        .map_err(|e| format!("Watched file could not be opened: {e}"))?
        .into_std();
    let meta = file
        .metadata()
        .map_err(|e| format!("Watched file could not be inspected: {e}"))?;
    if !meta.is_file() {
        return Err("Watched entry is not a regular file".into());
    }
    if meta.len() != expected_size || mtime_ms(&meta) != expected_mtime {
        return Err("Watched file changed after it was scanned".into());
    }
    Ok(SnapshotReader {
        file,
        expected_size,
        expected_mtime,
    })
}

#[derive(Debug)]
struct SnapshotReader {
    file: fs::File,
    expected_size: u64,
    expected_mtime: u64,
}

impl SnapshotReader {
    fn validate(&self) -> io::Result<()> {
        let meta = self.file.metadata()?;
        if !meta.is_file()
            || meta.len() != self.expected_size
            || mtime_ms(&meta) != self.expected_mtime
        {
            return Err(io::Error::other("watched file changed during upload"));
        }
        Ok(())
    }
}

impl Read for SnapshotReader {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        self.validate()?;
        let read = self.file.read(buf)?;
        self.validate()?;
        Ok(read)
    }
}

/// Register a folder selected by the owning desktop shell's native dialog.
/// This function is not exposed to renderer IPC with an arbitrary path.
pub fn register(dir: &Path) -> Result<WatchFolderSelection, String> {
    if !dir.is_absolute() || !dir.is_dir() {
        return Err("Selected watch folder is not a directory".into());
    }
    let watched = authorize_watched_dir(dir)?;
    let display = watched.canonical.to_string_lossy().into_owned();
    let token = new_token()?;
    registry()
        .lock()
        .map_err(|_| "Watch-folder registry poisoned".to_string())?
        .insert(token.clone(), watched);
    Ok(WatchFolderSelection {
        token,
        path: display,
    })
}

/// List the files currently sitting in the watched folder (non-recursive).
pub fn scan(token: String) -> Result<Vec<WatchEntry>, String> {
    scan_dir(&registered_dir(&token)?)
}

#[derive(Serialize)]
pub struct WatchFolderUploadReply {
    status: u16,
    body: serde_json::Value,
}

fn video_mime(name: &str) -> &'static str {
    match Path::new(name)
        .extension()
        .and_then(|ext| ext.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
        .as_str()
    {
        "mp4" | "m4v" => "video/mp4",
        "mov" => "video/quicktime",
        "mkv" => "video/x-matroska",
        "webm" => "video/webm",
        "avi" => "video/x-msvideo",
        "mpg" | "mpeg" => "video/mpeg",
        "wmv" => "video/x-ms-wmv",
        _ => "application/octet-stream",
    }
}

fn batch_endpoint(backend_url: &str) -> Result<reqwest::Url, String> {
    let mut url = reqwest::Url::parse(backend_url)
        .map_err(|_| "Invalid watch-folder backend URL".to_string())?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || !matches!(url.path(), "" | "/")
    {
        return Err("Invalid watch-folder backend URL".into());
    }
    url.set_path("/batch/enqueue");
    Ok(url)
}

fn validate_upload_authorization(url: &reqwest::Url, authorization: Option<&str>) -> Result<(), String> {
    let host = url.host_str().unwrap_or("").trim_start_matches('[').trim_end_matches(']');
    let loopback = host.eq_ignore_ascii_case("localhost")
        || host.parse::<std::net::IpAddr>().is_ok_and(|ip| ip.is_loopback());
    if url.scheme() == "http" && !loopback && authorization.is_some_and(|v| !v.is_empty()) {
        return Err("Credentialed watch-folder uploads require HTTPS outside loopback".into());
    }
    Ok(())
}

/// Stream one settled watched file directly from its pinned OS handle to the
/// selected backend. Keeping bytes out of WebView IPC avoids an O(file size)
/// renderer allocation for multi-gigabyte videos.
pub fn enqueue_to(
    backend_url: String,
    authorization: Option<String>,
    token: String,
    name: String,
    expected_size: u64,
    expected_mtime: u64,
    langs: Vec<String>,
    voice_id: Option<String>,
    preserve_bg: bool,
) -> Result<WatchFolderUploadReply, String> {
    let dir = registered_dir(&token)?;
    let reader = open_watched_reader(&dir, &name, expected_size, expected_mtime)?;
    let mime = video_mime(&name);
    let url = batch_endpoint(&backend_url)?;
    validate_upload_authorization(&url, authorization.as_deref())?;

    let part = reqwest::blocking::multipart::Part::reader_with_length(reader, expected_size)
        .file_name(name)
        .mime_str(mime)
        .map_err(|_| "Watched file type could not be prepared".to_string())?;
    let mut form = reqwest::blocking::multipart::Form::new()
        .part("video", part)
        .text("langs", langs.join(","))
        .text("preserve_bg", preserve_bg.to_string());
    if let Some(voice_id) = voice_id.filter(|value| !value.is_empty()) {
        form = form.text("voice_id", voice_id);
    }
    let client = reqwest::blocking::Client::builder()
        .no_proxy()
        .connect_timeout(std::time::Duration::from_secs(5))
        .build()
        .map_err(|_| "Watch-folder upload client could not start".to_string())?;
    let mut request = client.post(url).multipart(form);
    if let Some(value) = authorization.filter(|value| !value.is_empty()) {
        request = request.header(reqwest::header::AUTHORIZATION, value);
    }
    let response = request
        .send()
        .map_err(|_| "Watch-folder upload failed".to_string())?;
    let status = response.status().as_u16();
    let body = response
        .json::<serde_json::Value>()
        .map_err(|_| "Watch-folder backend returned an invalid response".to_string())?;
    Ok(WatchFolderUploadReply { status, body })
}

#[allow(dead_code)] // Used by the Tauri adapter; the Electron helper calls enqueue_to.
pub fn enqueue(
    backend_port: u16,
    token: String,
    name: String,
    expected_size: u64,
    expected_mtime: u64,
    langs: Vec<String>,
    voice_id: Option<String>,
    preserve_bg: bool,
) -> Result<WatchFolderUploadReply, String> {
    enqueue_to(
        format!("http://127.0.0.1:{backend_port}"),
        None,
        token,
        name,
        expected_size,
        expected_mtime,
        langs,
        voice_id,
        preserve_bg,
    )
}

/// Drop a watch-folder authorization (watcher stopped or component unmounted).
pub fn forget(token: String) {
    if let Ok(mut map) = registry().lock() {
        map.remove(&token);
    }
}

#[cfg(test)]
mod tests {
    use super::{
        authorize_watched_dir, batch_endpoint, mtime_ms, open_dir_handle, open_watched_reader,
        scan_dir, validate_entry_name, verify_watched_dir, Dir,
    };
    use std::fs;
    use std::io::Read;
    use std::path::PathBuf;

    fn temp_watch_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("vs-watch-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn open_handle(dir: &std::path::Path) -> Dir {
        open_dir_handle(dir).unwrap()
    }

    fn snapshot(path: &std::path::Path) -> (u64, u64) {
        let meta = fs::metadata(path).unwrap();
        (meta.len(), mtime_ms(&meta))
    }

    #[test]
    fn entry_names_must_be_single_components() {
        assert!(validate_entry_name("clip.mp4").is_ok());
        assert!(validate_entry_name("weird name (1).MOV").is_ok());
        for bad in [
            "",
            ".",
            "..",
            "a/b.mp4",
            "a\\b.mp4",
            "..\\up.mp4",
            "x\n.mp4",
        ] {
            assert!(validate_entry_name(bad).is_err(), "accepted {bad:?}");
        }
    }

    #[test]
    fn backend_endpoint_accepts_remote_https_without_credential_injection() {
        assert_eq!(
            batch_endpoint("https://gpu-box.example:3900")
                .unwrap()
                .as_str(),
            "https://gpu-box.example:3900/batch/enqueue"
        );
        for bad in [
            "file:///tmp/backend",
            "https://user:secret@gpu-box.example:3900",
            "https://gpu-box.example:3900/other",
            "https://gpu-box.example:3900?token=secret",
        ] {
            assert!(batch_endpoint(bad).is_err(), "accepted {bad:?}");
        }
    }

    #[test]
    fn scan_lists_regular_files_with_size_and_mtime_and_skips_dirs() {
        let dir = temp_watch_dir("scan");
        fs::create_dir_all(dir.join("nested")).unwrap();
        fs::write(dir.join("a.mp4"), b"12345").unwrap();
        fs::write(dir.join("notes.txt"), b"x").unwrap();

        let mut entries = scan_dir(&open_handle(&dir)).unwrap();
        entries.sort_by(|a, b| a.name.cmp(&b.name));
        let names: Vec<&str> = entries.iter().map(|e| e.name.as_str()).collect();
        // Directories are skipped; filtering to *videos* is the frontend's job.
        assert_eq!(names, ["a.mp4", "notes.txt"]);
        assert_eq!(entries[0].size, 5);
        assert!(entries[0].mtime > 0);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn snapshot_reader_streams_the_exact_bytes() {
        let dir = temp_watch_dir("stream");
        fs::write(dir.join("clip.mp4"), b"0123456789").unwrap();
        let (size, mtime) = snapshot(&dir.join("clip.mp4"));
        let handle = open_handle(&dir);

        let mut reader = open_watched_reader(&handle, "clip.mp4", size, mtime).unwrap();
        let mut whole = Vec::new();
        reader.read_to_end(&mut whole).unwrap();
        assert_eq!(whole, b"0123456789");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn reads_are_bound_to_the_settled_snapshot() {
        let dir = temp_watch_dir("snapshot");
        fs::write(dir.join("clip.mp4"), b"settled bytes").unwrap();
        let (size, mtime) = snapshot(&dir.join("clip.mp4"));
        let handle = open_handle(&dir);

        // The file is replaced after the scan settled → the read must refuse
        // rather than upload bytes the tracker never saw stabilize.
        fs::write(dir.join("clip.mp4"), b"replaced with something longer").unwrap();
        let err = open_watched_reader(&handle, "clip.mp4", size, mtime).unwrap_err();
        assert!(err.contains("changed"), "unexpected error: {err}");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn snapshot_reader_aborts_when_file_changes_during_stream() {
        let dir = temp_watch_dir("mid-stream-change");
        fs::write(dir.join("clip.mp4"), b"settled bytes").unwrap();
        let (size, mtime) = snapshot(&dir.join("clip.mp4"));
        let handle = open_handle(&dir);
        let mut reader = open_watched_reader(&handle, "clip.mp4", size, mtime).unwrap();

        fs::write(dir.join("clip.mp4"), b"different-length bytes").unwrap();
        let mut byte = [0_u8; 1];
        assert!(reader.read(&mut byte).is_err());

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn authorization_pins_the_directory_identity() {
        let dir = temp_watch_dir("identity");
        let watched = authorize_watched_dir(&dir).unwrap();
        // Untouched directory verifies fine…
        assert!(verify_watched_dir(&watched).is_ok());
        // …and a directory that disappears after authorization is refused.
        // The removal itself must succeed WHILE the handle is held: a watch
        // that locked the user's folder against deletion (Windows sharing
        // violation, OS error 32) would be a Windows-only behaviour.
        fs::remove_dir_all(&dir).unwrap();
        assert!(verify_watched_dir(&watched).is_err());
    }

    #[test]
    fn a_watched_folder_can_be_renamed_by_the_user_while_watched() {
        // Cross-platform contract: holding the pinned handle never blocks the
        // user from moving the folder (Explorer/Finder/mv). The liveness
        // check is what notices — it must refuse, not the OS.
        let dir = temp_watch_dir("rename-while-watched");
        let moved = dir.with_extension("moved");
        let _ = fs::remove_dir_all(&moved);
        let watched = authorize_watched_dir(&dir).unwrap();
        fs::rename(&dir, &moved).unwrap();
        assert!(verify_watched_dir(&watched).is_err());
        // The pinned handle still points at the ORIGINAL directory object.
        fs::write(moved.join("clip.mp4"), b"x").unwrap();
        let names: Vec<String> = scan_dir(&watched.handle)
            .unwrap()
            .into_iter()
            .map(|e| e.name)
            .collect();
        assert_eq!(names, ["clip.mp4"]);
        drop(watched);
        let _ = fs::remove_dir_all(&moved);
    }

    #[cfg(unix)]
    #[test]
    fn a_directory_swapped_for_a_symlink_is_refused_and_never_followed() {
        let dir = temp_watch_dir("dir-swap");
        let elsewhere = temp_watch_dir("dir-swap-target");
        fs::write(elsewhere.join("clip.mp4"), b"outside").unwrap();
        let (size, mtime) = snapshot(&elsewhere.join("clip.mp4"));

        let watched = authorize_watched_dir(&dir).unwrap();
        assert!(verify_watched_dir(&watched).is_ok());

        // Replace the authorized directory itself with a symlink pointing
        // somewhere else. Token resolution refuses (identity check)…
        fs::remove_dir_all(&dir).unwrap();
        std::os::unix::fs::symlink(&elsewhere, &dir).unwrap();
        let err = verify_watched_dir(&watched).unwrap_err();
        assert!(err.contains("identity"), "unexpected error: {err}");
        // …and even the pinned handle cannot reach the swap target: it still
        // points at the ORIGINAL (now unlinked) directory, which is empty.
        assert!(scan_dir(&watched.handle).unwrap().is_empty());
        assert!(open_watched_reader(&watched.handle, "clip.mp4", size, mtime).is_err());

        let _ = fs::remove_file(&dir);
        let _ = fs::remove_dir_all(&elsewhere);
    }

    #[cfg(unix)]
    #[test]
    fn a_recreated_directory_at_the_same_path_is_refused() {
        let dir = temp_watch_dir("dir-recreate");
        let watched = authorize_watched_dir(&dir).unwrap();
        fs::remove_dir_all(&dir).unwrap();
        fs::create_dir_all(&dir).unwrap(); // same path, different inode
        assert!(verify_watched_dir(&watched).is_err());
        let _ = fs::remove_dir_all(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn a_replaced_directory_at_the_same_windows_path_is_refused() {
        // Same pathname, different directory object (volume serial + file
        // index): the pathname check alone would pass, the identity must not.
        let dir = temp_watch_dir("windows-dir-replace");
        let moved = dir.with_extension("moved");
        let _ = fs::remove_dir_all(&moved);
        let watched = authorize_watched_dir(&dir).unwrap();
        fs::rename(&dir, &moved).unwrap();
        fs::create_dir_all(&dir).unwrap();
        let err = verify_watched_dir(&watched).unwrap_err();
        assert!(err.contains("identity"), "unexpected error: {err}");
        drop(watched);
        let _ = fs::remove_dir_all(&dir);
        let _ = fs::remove_dir_all(&moved);
    }

    #[cfg(unix)]
    #[test]
    fn symlinks_are_never_followed_out_of_the_folder() {
        let dir = temp_watch_dir("symlink");
        let secret = std::env::temp_dir().join(format!("vs-secret-{}", std::process::id()));
        fs::write(&secret, b"outside the folder").unwrap();
        std::os::unix::fs::symlink(&secret, dir.join("evil.mp4")).unwrap();
        let meta = fs::metadata(dir.join("evil.mp4")).unwrap();
        let handle = open_handle(&dir);

        // Even with a "correct" snapshot of the symlink target, opening it
        // through the capability handle refuses: resolution may not escape
        // the watched folder.
        let err =
            open_watched_reader(&handle, "evil.mp4", meta.len(), mtime_ms(&meta)).unwrap_err();
        assert!(
            err.contains("could not be opened"),
            "unexpected error: {err}"
        );
        // And the scanner never lists it in the first place.
        assert!(scan_dir(&handle).unwrap().is_empty());

        let _ = fs::remove_dir_all(&dir);
        let _ = fs::remove_file(&secret);
    }
}

#[cfg(test)]
mod transport_tests {
    #[test]
    fn credentials_require_tls_except_loopback() {
        for base in ["http://127.0.0.1:3900", "http://[::1]:3900", "http://localhost:3900", "https://gpu.example"] {
            assert!(super::validate_upload_authorization(&super::batch_endpoint(base).unwrap(), Some("Bearer secret")).is_ok());
        }
        for base in ["http://gpu.example", "http://192.168.1.2:3900", "http://localhost.evil.test"] {
            let url = super::batch_endpoint(base).unwrap();
            assert!(super::validate_upload_authorization(&url, Some("Bearer secret")).is_err());
            assert!(super::validate_upload_authorization(&url, None).is_ok());
        }
    }
}
