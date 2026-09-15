//! Tauri adapter for shared capability-scoped batch watch folders.
pub use crate::watch_folder_core::{WatchEntry, WatchFolderSelection, WatchFolderUploadReply};
use tauri_plugin_dialog::DialogExt;

#[tauri::command]
pub async fn watch_folder_pick(
    app: tauri::AppHandle,
) -> Result<Option<WatchFolderSelection>, String> {
    app.dialog()
        .file()
        .blocking_pick_folder()
        .and_then(|value| value.into_path().ok())
        .map(|dir| crate::watch_folder_core::register(&dir))
        .transpose()
}

#[tauri::command]
pub fn watch_folder_scan(token: String) -> Result<Vec<WatchEntry>, String> {
    crate::watch_folder_core::scan(token)
}

#[tauri::command]
pub async fn watch_folder_enqueue(
    token: String,
    name: String,
    expected_size: u64,
    expected_mtime: u64,
    langs: Vec<String>,
    voice_id: Option<String>,
    preserve_bg: bool,
) -> Result<WatchFolderUploadReply, String> {
    let port = crate::backend_port();
    tauri::async_runtime::spawn_blocking(move || {
        crate::watch_folder_core::enqueue(
            port,
            token,
            name,
            expected_size,
            expected_mtime,
            langs,
            voice_id,
            preserve_bg,
        )
    })
    .await
    .map_err(|_| "Watch-folder upload task failed".to_string())?
}

#[tauri::command]
pub fn watch_folder_forget(token: String) {
    crate::watch_folder_core::forget(token);
}
