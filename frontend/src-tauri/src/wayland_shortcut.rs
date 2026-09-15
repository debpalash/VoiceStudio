//! Tauri adapter for the shared compositor-owned shortcut implementation.
pub use crate::wayland_shortcut_core::is_wayland_session;
use std::sync::Arc;
use tauri::Manager;
#[derive(Default)]
pub struct PortalShortcutState(crate::wayland_shortcut_core::PortalShortcutState);
impl PortalShortcutState {
    pub fn reserve(&self) -> u64 {
        self.0.reserve()
    }
    pub fn replace(&self, app: tauri::AppHandle, accelerator: String) -> Result<String, String> {
        self.0.replace(
            Arc::new(move |pressed| {
                crate::dispatch_dictation_capture(&app, if pressed { "start" } else { "stop" })
            }),
            accelerator,
        )
    }
    pub fn replace_reserved(
        &self,
        app: tauri::AppHandle,
        accelerator: String,
        revision: u64,
    ) -> Result<String, String> {
        self.0.replace_reserved(
            Arc::new(move |pressed| {
                crate::dispatch_dictation_capture(&app, if pressed { "start" } else { "stop" })
            }),
            accelerator,
            revision,
        )
    }
}

pub fn register_initial(app: tauri::AppHandle, accelerator: String, revision: u64) {
    let worker_app = app.clone();
    if let Err(error) = std::thread::Builder::new()
        .name("wayland-global-shortcut-setup".into())
        .spawn(move || {
            let manager = worker_app.state::<crate::dictation_shortcut::DictationShortcutManager>();
            if let Err(error) = manager.register_portal_initial(&worker_app, accelerator, revision)
            {
                log::error!("Wayland dictation shortcut unavailable: {error}");
            }
        })
    {
        log::error!("Failed to start Wayland shortcut setup: {error}");
    }
}
