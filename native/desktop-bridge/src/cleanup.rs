use serde::Deserialize;
use std::collections::HashSet;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

const MAX_PLAN_BYTES: u64 = 64 * 1024;
const MAX_TARGETS: usize = 16;
const OWNER_EXIT_TIMEOUT: Duration = Duration::from_secs(120);

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CleanupPlan {
    paths: Vec<CleanupTarget>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CleanupTarget {
    path: PathBuf,
    kind: CleanupKind,
}

#[derive(Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
enum CleanupKind {
    App,
    Models,
}

fn home_dir() -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    let key = "USERPROFILE";
    #[cfg(not(target_os = "windows"))]
    let key = "HOME";
    std::env::var_os(key)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

fn has_signature(path: &Path, allow_model_cache: bool) -> bool {
    ["omnivoice.db", "prefs.json", "voices", "outputs", "engines"]
        .iter()
        .any(|name| path.join(name).exists())
        || (allow_model_cache
            && (path.join("hub").is_dir()
                || fs::read_dir(path).is_ok_and(|entries| {
                    entries
                        .filter_map(Result::ok)
                        .any(|entry| entry.file_name().to_string_lossy().starts_with("models--"))
                })))
        || has_runtime_project_signature(path)
        || has_runtime_project_signature(&path.join("project"))
        || has_runtime_project_signature(&path.join("runtime/project"))
}

fn has_runtime_project_signature(project: &Path) -> bool {
    let is_dir = |path: &Path| fs::symlink_metadata(path).is_ok_and(|info| info.is_dir());
    let is_file = |path: &Path| fs::symlink_metadata(path).is_ok_and(|info| info.is_file());
    if !is_dir(project)
        || !is_dir(&project.join("backend"))
        || !is_file(&project.join("backend/main.py"))
        || !is_file(&project.join("uv.lock"))
    {
        return false;
    }
    let read_bounded = |name: &str, limit: u64| {
        let path = project.join(name);
        let info = fs::symlink_metadata(&path).ok()?;
        (info.is_file() && info.len() <= limit)
            .then(|| fs::read_to_string(path).ok())
            .flatten()
    };
    let Some(ready) = read_bounded(".runtime-ready", 128) else {
        return false;
    };
    let ready = ready.trim();
    if ready.len() != 64
        || !ready
            .bytes()
            .all(|value| value.is_ascii_hexdigit() && !value.is_ascii_uppercase())
    {
        return false;
    }
    let Some(metadata) = read_bounded("pyproject.toml", 65536) else {
        return false;
    };
    let mut in_project = false;
    for raw in metadata.lines() {
        let line = raw.split('#').next().unwrap_or_default().trim();
        if line.starts_with('[') {
            in_project = line == "[project]";
        } else if in_project {
            if let Some((key, value)) = line.split_once('=') {
                if key.trim() == "name" && ["\"omnivoice\"", "'omnivoice'"].contains(&value.trim())
                {
                    return true;
                }
            }
        }
    }
    false
}

// Folders the OS (or the app's installer) puts per-app directories in. A bare app-named
// directory anywhere else, such as ~/Documents/VoiceStudio, is the user's own unless it
// carries the app's signature.
const APP_PARENTS: [&str; 10] = [
    "Application Support",
    "Caches",
    "Logs",
    "Roaming",
    "Local",
    "Programs",
    ".config",
    ".cache",
    "share",
    "state",
];

fn app_parent(path: &Path, home: Option<&Path>) -> bool {
    let Some(parent) = path.parent() else {
        return false;
    };
    if home == Some(parent) {
        return true;
    }
    let Some(name) = parent.file_name().and_then(|part| part.to_str()) else {
        return false;
    };
    let local = parent
        .parent()
        .and_then(Path::file_name)
        .and_then(|part| part.to_str());
    APP_PARENTS
        .iter()
        .any(|candidate| name.eq_ignore_ascii_case(candidate))
        && (!matches!(name.to_ascii_lowercase().as_str(), "share" | "state")
            || local.is_some_and(|local| local.eq_ignore_ascii_case(".local")))
}

fn exact_managed_layout(path: &Path, home: Option<&Path>) -> bool {
    const APP_NAMES: [&str; 4] = [
        "OmniVoice",
        ".omnivoice",
        "VoiceStudio",
        "com.debpalash.omnivoice-studio",
    ];
    const CHILDREN: [&str; 9] = [
        "data",
        "logs",
        "runtime",
        "shell",
        "project",
        ".uv-cache",
        ".python",
        ".tmp",
        "tmp",
    ];
    let parts: Vec<_> = path.components().map(|part| part.as_os_str()).collect();
    let Some(leaf) = parts.last().and_then(|part| part.to_str()) else {
        return false;
    };
    if CHILDREN.iter().any(|name| leaf.eq_ignore_ascii_case(name))
        && path
            .parent()
            .is_some_and(|parent| has_runtime_project_signature(&parent.join("project")))
    {
        return true;
    }
    if APP_NAMES.iter().any(|name| leaf.eq_ignore_ascii_case(name)) {
        return app_parent(path, home);
    }
    let parent_is_app = parts
        .get(parts.len().saturating_sub(2))
        .and_then(|part| part.to_str())
        .is_some_and(|parent| {
            APP_NAMES
                .iter()
                .any(|name| parent.eq_ignore_ascii_case(name))
        });
    let runtime_grandparent_is_app = parts
        .get(parts.len().saturating_sub(3))
        .and_then(|part| part.to_str())
        .is_some_and(|parent| {
            APP_NAMES
                .iter()
                .any(|name| parent.eq_ignore_ascii_case(name))
        })
        && parts
            .get(parts.len().saturating_sub(2))
            .and_then(|part| part.to_str())
            .is_some_and(|parent| parent.eq_ignore_ascii_case("runtime"));
    // A known runtime child is specific enough on its own: setup can place the whole
    // app folder on any drive (#2971).
    (parent_is_app || runtime_grandparent_is_app)
        && CHILDREN.iter().any(|name| leaf.eq_ignore_ascii_case(name))
}

fn exact_model_layout(path: &Path) -> bool {
    let leaf = path.file_name().and_then(|part| part.to_str());
    let parent = path
        .parent()
        .and_then(Path::file_name)
        .and_then(|part| part.to_str());
    matches!((leaf, parent), (Some(leaf), Some(parent))
        if (leaf.eq_ignore_ascii_case("huggingface") && parent.eq_ignore_ascii_case(".cache"))
            || (leaf.eq_ignore_ascii_case("hub") && parent.eq_ignore_ascii_case("huggingface")))
}

fn private_model_layout(path: &Path) -> bool {
    let root = if path
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.eq_ignore_ascii_case("hub"))
    {
        let Some(parent) = path.parent() else {
            return false;
        };
        parent
    } else {
        path
    };
    let Some(name) = root.file_name().and_then(|name| name.to_str()) else {
        return false;
    };
    let Some(parent) = root.parent() else {
        return false;
    };
    ["models", "hf_cache"]
        .iter()
        .any(|item| name.eq_ignore_ascii_case(item))
        && (parent
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| {
                [
                    "OmniVoice",
                    ".omnivoice",
                    "VoiceStudio",
                    "com.debpalash.omnivoice-studio",
                ]
                .iter()
                .any(|item| name.eq_ignore_ascii_case(item))
            })
            || exact_managed_layout(parent, None)
            || has_runtime_project_signature(&parent.join("project")))
}

fn recognizably_owned(path: &Path, home: Option<&Path>, kind: &CleanupKind) -> bool {
    if !path.is_absolute()
        || path.parent().is_none()
        || home == Some(path)
        || !fs::symlink_metadata(path).is_ok_and(|info| info.is_dir())
    {
        return false;
    }
    exact_managed_layout(path, home)
        || (*kind == CleanupKind::Models
            && (exact_model_layout(path) || private_model_layout(path)))
        || has_signature(path, *kind == CleanupKind::Models)
}

#[cfg(target_os = "windows")]
fn wait_for_owner(owner_pid: u32) -> bool {
    use windows::Win32::Foundation::{CloseHandle, WAIT_TIMEOUT};
    use windows::Win32::System::Threading::{
        OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE,
    };
    let Ok(handle) = (unsafe { OpenProcess(PROCESS_SYNCHRONIZE, false, owner_pid) }) else {
        return true;
    };
    let deadline = Instant::now() + OWNER_EXIT_TIMEOUT;
    let mut exited = false;
    while Instant::now() < deadline {
        if unsafe { WaitForSingleObject(handle, 250) } != WAIT_TIMEOUT {
            exited = true;
            break;
        }
    }
    let _ = unsafe { CloseHandle(handle) };
    exited
}

#[cfg(not(target_os = "windows"))]
fn wait_for_owner(owner_pid: u32) -> bool {
    if owner_pid > i32::MAX as u32 {
        return true;
    }
    let deadline = Instant::now() + OWNER_EXIT_TIMEOUT;
    while Instant::now() < deadline {
        let result = unsafe { libc::kill(owner_pid as i32, 0) };
        if result != 0 && io::Error::last_os_error().raw_os_error() != Some(libc::EPERM) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    false
}

fn remove_target(path: &Path) -> io::Result<()> {
    let info = match fs::symlink_metadata(path) {
        Ok(info) => info,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    if info.is_dir() && !info.file_type().is_symlink() {
        fs::remove_dir_all(path)
    } else {
        fs::remove_file(path)
    }
}

pub fn run(owner_pid: u32, plan_path: &Path) -> io::Result<()> {
    let info = fs::metadata(plan_path)?;
    if info.len() > MAX_PLAN_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "Cleanup plan is too large",
        ));
    }
    let plan: CleanupPlan = serde_json::from_slice(&fs::read(plan_path)?)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    fs::remove_file(plan_path)?;
    if plan.paths.is_empty() || plan.paths.len() > MAX_TARGETS {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "Invalid cleanup target count",
        ));
    }
    let home = home_dir().and_then(|path| fs::canonicalize(path).ok());
    let mut seen = HashSet::new();
    let mut paths = Vec::new();
    for target in plan.paths {
        let canonical = fs::canonicalize(&target.path)?;
        if !recognizably_owned(&canonical, home.as_deref(), &target.kind)
            || !seen.insert(canonical.clone())
        {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Unsafe cleanup target",
            ));
        }
        paths.push(canonical);
    }
    if !wait_for_owner(owner_pid) {
        return Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "Owner process did not exit",
        ));
    }
    let mut failures = Vec::new();
    for path in paths {
        if let Err(error) = remove_target(&path) {
            failures.push(format!("{}: {error}", path.display()));
        }
    }
    if failures.is_empty() {
        Ok(())
    } else {
        Err(io::Error::other(failures.join("; ")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fixture(PathBuf);

    impl Fixture {
        fn new(name: &str) -> Self {
            let root = std::env::temp_dir().join(format!("cleanup-{name}-{}", std::process::id()));
            fs::create_dir_all(&root).unwrap();
            Self(root)
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn ownership_does_not_extend_to_arbitrary_descendants() {
        let fixture = Fixture::new("ancestry");
        let app = fixture.0.join("VoiceStudio");
        fs::create_dir_all(&app).unwrap();
        fs::write(app.join("omnivoice.db"), b"db").unwrap();
        for suffix in [
            "Documents",
            "personal/Documents",
            "personal/project",
            "models/other",
        ] {
            assert!(
                !recognizably_owned(&app.join(suffix), None, &CleanupKind::App),
                "{suffix}"
            );
        }
        assert!(!recognizably_owned(
            &fixture.0.join("VoiceStudio-copy/project"),
            None,
            &CleanupKind::App
        ));
    }

    #[test]
    fn ownership_accepts_exact_managed_layouts() {
        let fixture = Fixture::new("layouts");
        for suffix in [
            "VoiceStudio",
            ".omnivoice",
            "com.debpalash.omnivoice-studio",
            "OmniVoiceStudio-Data",
            "OmniVoice/data",
            "OmniVoice/logs",
            "OmniVoice/hf_cache",
            "OmniVoice/hf_cache/hub",
            "VoiceStudio/data",
            "VoiceStudio/models",
            "VoiceStudio/models/hub",
            "VoiceStudio/runtime",
            "VoiceStudio/runtime/project",
            "VoiceStudio/project",
            "VoiceStudio/.uv-cache",
            "VoiceStudio/.python",
            "VoiceStudio/.tmp",
            "VoiceStudio/tmp",
            ".cache/huggingface",
            ".cache/huggingface/hub",
        ] {
            let path = fixture.0.join(suffix);
            fs::create_dir_all(&path).unwrap();
            if !suffix.contains('/') {
                fs::write(path.join("omnivoice.db"), b"synthetic app identity").unwrap();
            }
            let kind = if suffix
                .split('/')
                .any(|part| ["models", "hf_cache", "huggingface", "hub"].contains(&part))
            {
                CleanupKind::Models
            } else {
                CleanupKind::App
            };
            assert!(recognizably_owned(&path, None, &kind), "{suffix}");
            assert!(
                !recognizably_owned(&path, Some(&path), &kind),
                "home: {suffix}"
            );
        }
    }

    #[test]
    fn cleanup_refuses_unrelated_tree_before_removing_any_target() {
        let fixture = Fixture::new("refusal");
        let target = fixture.0.join("VoiceStudio/personal/Documents");
        let owned = fixture.0.join("custom-data");
        fs::create_dir_all(&target).unwrap();
        fs::create_dir_all(&owned).unwrap();
        fs::write(target.join("unrelated.txt"), b"keep").unwrap();
        fs::write(owned.join("omnivoice.db"), b"db").unwrap();
        let plan = fixture.0.join("plan.json");
        fs::write(
            &plan,
            serde_json::to_vec(&serde_json::json!({"paths": [
                {"path": &owned, "kind": "app"},
                {"path": &target, "kind": "app"}
            ]}))
            .unwrap(),
        )
        .unwrap();
        let result = run(u32::MAX, &plan);
        assert!(target.join("unrelated.txt").exists());
        assert!(owned.join("omnivoice.db").exists());
        assert_eq!(result.unwrap_err().kind(), io::ErrorKind::PermissionDenied);
    }

    #[test]
    fn cleanup_accepts_custom_runtime_entries_only_with_staged_app_identity() {
        let fixture = Fixture::new("custom-runtime");
        let runtime = fixture.0.join("Votest/VoiceStudio-AMD-20261025/runtime");
        let project = runtime.join("project");
        fs::create_dir_all(project.join("backend")).unwrap();
        fs::write(project.join(".runtime-ready"), "a".repeat(64)).unwrap();
        fs::write(
            project.join("pyproject.toml"),
            "[project]\nname = \"unrelated\"\n",
        )
        .unwrap();
        fs::write(project.join("uv.lock"), b"version = 1").unwrap();
        fs::write(project.join("backend/main.py"), b"app = None").unwrap();
        assert!(!recognizably_owned(&runtime, None, &CleanupKind::App));
        assert!(!recognizably_owned(
            &runtime.join(".uv-cache"),
            None,
            &CleanupKind::App
        ));
        fs::write(
            project.join("pyproject.toml"),
            "[project]\nname = \"omnivoice\"\n",
        )
        .unwrap();
        let entries: Vec<_> = ["project", ".uv-cache", ".python", ".tmp", "tmp"]
            .iter()
            .map(|name| runtime.join(name))
            .collect();
        for entry in &entries {
            fs::create_dir_all(entry).unwrap();
            assert!(recognizably_owned(entry, None, &CleanupKind::App));
        }
        assert!(recognizably_owned(&runtime, None, &CleanupKind::App));
        let sentinel = runtime.join("Documents/unrelated.txt");
        fs::create_dir_all(sentinel.parent().unwrap()).unwrap();
        fs::write(&sentinel, b"keep").unwrap();
        let plan = fixture.0.join("plan.json");
        fs::write(
            &plan,
            serde_json::to_vec(&serde_json::json!({"paths": entries.iter().map(|path|
                serde_json::json!({"path": path, "kind": "app"})
            ).collect::<Vec<_>>()}))
            .unwrap(),
        )
        .unwrap();
        run(u32::MAX, &plan).unwrap();
        assert!(entries.iter().all(|entry| !entry.exists()));
        assert!(sentinel.exists());
    }

    #[test]
    fn ownership_guard_rejects_roots_and_accepts_app_signatures() {
        let root = std::env::temp_dir().join(format!("desktop-cleanup-{}", std::process::id()));
        let signed = root.join("custom-data");
        fs::create_dir_all(&signed).unwrap();
        fs::write(signed.join("omnivoice.db"), b"db").unwrap();
        assert!(recognizably_owned(
            &signed,
            home_dir().as_deref(),
            &CleanupKind::App
        ));
        assert!(!recognizably_owned(
            Path::new("."),
            home_dir().as_deref(),
            &CleanupKind::App
        ));
        if let Some(home) = home_dir() {
            assert!(!recognizably_owned(&home, Some(&home), &CleanupKind::App));
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn ownership_requires_local_cache_markers_and_rejects_a_bare_runtime_folder() {
        let fixture = Fixture::new("markers");
        let bare = fixture.0.join("custom-profile");
        fs::create_dir_all(bare.join("runtime")).unwrap();
        assert!(!recognizably_owned(&bare, None, &CleanupKind::App));
        for marker in ["hub", "models--fixture"] {
            let cache = fixture.0.join(marker).join("custom-cache");
            fs::create_dir_all(cache.join(marker)).unwrap();
            assert!(recognizably_owned(&cache, None, &CleanupKind::Models));
            assert!(!recognizably_owned(&cache, None, &CleanupKind::App));
            assert!(!recognizably_owned(
                &cache.join("Documents"),
                None,
                &CleanupKind::App
            ));
        }
    }

    #[cfg(unix)]
    #[test]
    fn cleanup_canonicalization_rejects_an_app_named_link_to_an_unrelated_tree() {
        let fixture = Fixture::new("canonical");
        let target = fixture.0.join("Documents");
        let alias = fixture.0.join("VoiceStudio");
        fs::create_dir_all(&target).unwrap();
        fs::write(target.join("unrelated.txt"), b"keep").unwrap();
        std::os::unix::fs::symlink(&target, &alias).unwrap();
        let plan = fixture.0.join("plan.json");
        fs::write(
            &plan,
            serde_json::to_vec(&serde_json::json!({"paths": [{"path": &alias, "kind": "app"}]}))
                .unwrap(),
        )
        .unwrap();
        assert_eq!(
            run(u32::MAX, &plan).unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        assert!(target.join("unrelated.txt").exists());
    }

    #[test]
    fn cleanup_reads_then_removes_only_the_authorized_tree() {
        let root = std::env::temp_dir().join(format!("native-cleanup-{}", std::process::id()));
        let target = root.join("custom-data");
        let plan = root.join("plan.json");
        fs::create_dir_all(&target).unwrap();
        fs::write(target.join("omnivoice.db"), b"db").unwrap();
        fs::write(
            &plan,
            serde_json::to_vec(&serde_json::json!({
                "paths": [{ "path": &target, "kind": "app" }]
            }))
            .unwrap(),
        )
        .unwrap();
        run(u32::MAX, &plan).unwrap();
        assert!(!target.exists());
        assert!(!plan.exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn validation_rejects_lookalike_ancestors_and_model_markers_for_app_targets() {
        let root = std::env::temp_dir().join(format!("native-validation-{}", std::process::id()));
        let lookalike = root.join("VoiceStudio").join("personal").join("documents");
        let cache = root.join("external-cache");
        fs::create_dir_all(&lookalike).unwrap();
        fs::create_dir_all(cache.join("models--owner--model")).unwrap();
        assert!(!recognizably_owned(&lookalike, None, &CleanupKind::App));
        assert!(!recognizably_owned(&cache, None, &CleanupKind::App));
        assert!(recognizably_owned(&cache, None, &CleanupKind::Models));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn validation_does_not_authorize_a_marker_file_as_a_cleanup_root() {
        let root = std::env::temp_dir().join(format!("native-marker-{}", std::process::id()));
        let signed = root.join("custom-data");
        let marker = signed.join("omnivoice.db");
        fs::create_dir_all(&signed).unwrap();
        fs::write(&marker, b"db").unwrap();
        assert!(recognizably_owned(&signed, None, &CleanupKind::App));
        assert!(!recognizably_owned(&marker, None, &CleanupKind::App));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn bare_app_named_directory_needs_an_app_parent_or_a_signature() {
        let root = std::env::temp_dir().join(format!("native-app-parent-{}", std::process::id()));
        let personal = root.join("Documents").join("VoiceStudio");
        let installed = root.join(".config").join("VoiceStudio");
        let shared = root.join(".local").join("share").join("OmniVoice");
        let stray_share = root.join("share").join("OmniVoice");
        for dir in [&personal, &installed, &shared, &stray_share] {
            fs::create_dir_all(dir.join("data")).unwrap();
        }
        let app = CleanupKind::App;
        assert!(!recognizably_owned(&personal, None, &app));
        assert!(!recognizably_owned(&stray_share, None, &app));
        assert!(recognizably_owned(&installed, None, &app));
        assert!(recognizably_owned(&installed.join("data"), None, &app));
        assert!(recognizably_owned(&shared, None, &app));
        let home_app = root.join(".omnivoice");
        fs::create_dir_all(&home_app).unwrap();
        assert!(recognizably_owned(&home_app, Some(&root), &app));
        fs::write(personal.join("omnivoice.db"), b"db").unwrap();
        assert!(recognizably_owned(&personal, None, &app));
        fs::remove_dir_all(root).unwrap();
    }
}
