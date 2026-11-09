//! Native press/release service. Registration and platform event pumping share
//! the main thread (required by macOS Carbon and Windows message-only windows).
use global_hotkey::{hotkey::HotKey, GlobalHotKeyEvent, GlobalHotKeyManager, HotKeyState};
use std::{str::FromStr, sync::mpsc, time::Duration};

enum Command {
    Replace(Option<String>, mpsc::SyncSender<Result<(), String>>),
    Stop,
}
#[derive(Clone)]
pub struct Control(mpsc::Sender<Command>);
pub struct Events(mpsc::Receiver<Command>);
pub fn channel() -> (Control, Events) {
    let (tx, rx) = mpsc::channel();
    (Control(tx), Events(rx))
}
impl Control {
    pub fn replace(&self, accelerator: Option<String>) -> Result<(), String> {
        let (tx, rx) = mpsc::sync_channel(1);
        self.0
            .send(Command::Replace(accelerator, tx))
            .map_err(|_| "Shortcut service stopped")?;
        rx.recv_timeout(Duration::from_secs(300))
            .map_err(|_| "Shortcut registration timed out")?
    }
    pub fn stop(&self) {
        let _ = self.0.send(Command::Stop);
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

pub fn run(events: Events, mut emit: impl FnMut(bool)) {
    let mut manager: Option<GlobalHotKeyManager> = None;
    let mut current: Option<HotKey> = None;
    let mut down = false;
    #[cfg(target_os = "linux")]
    let portal = crate::wayland_shortcut_core::PortalShortcutState::for_desktop(
        "com.voicestudio.desktop",
        std::env::var_os("VOICESTUDIO_DESKTOP_EXE")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| std::env::current_exe().unwrap_or_default()),
    );
    #[cfg(target_os = "linux")]
    let (portal_tx, portal_rx) = mpsc::channel::<(u64, bool)>();
    #[cfg(target_os = "linux")]
    let mut portal_generation = 0u64;
    loop {
        pump();
        #[cfg(target_os = "linux")]
        while let Ok((generation, pressed)) = portal_rx.try_recv() {
            if generation == portal_generation && pressed != down {
                down = pressed;
                emit(pressed);
            }
        }
        while let Ok(event) = GlobalHotKeyEvent::receiver().try_recv() {
            if current.as_ref().map(|key| key.id()) != Some(event.id) {
                continue;
            }
            let pressed = event.state == HotKeyState::Pressed;
            if pressed != down {
                down = pressed;
                emit(pressed);
            }
        }
        match events
            .0
            .recv_timeout(Duration::from_millis(if current.is_some() {
                8
            } else {
                250
            })) {
            Ok(Command::Stop) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Ok(Command::Replace(raw, reply)) => {
                let result = (|| {
                    let next = raw.as_deref().map(parse).transpose()?;
                    if next == current {
                        return Ok(());
                    }
                    #[cfg(target_os = "linux")]
                    if crate::wayland_shortcut_core::is_wayland_session() {
                        let next_generation = portal_generation.wrapping_add(1);
                        if let Some(raw) = raw {
                            let sender = portal_tx.clone();
                            portal.replace(
                                std::sync::Arc::new(move |pressed| {
                                    let _ = sender.send((next_generation, pressed));
                                }),
                                raw,
                            )?;
                        } else {
                            portal.close();
                        }
                        if down {
                            down = false;
                            emit(false);
                        }
                        portal_generation = next_generation;
                        current = next;
                        return Ok(());
                    }
                    if manager.is_none() && next.is_some() {
                        manager =
                            Some(GlobalHotKeyManager::new().map_err(|error| error.to_string())?);
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
                    if down {
                        down = false;
                        emit(false);
                    }
                    current = next;
                    Ok(())
                })();
                let _ = reply.send(result);
            }
        }
    }
    #[cfg(target_os = "linux")]
    portal.close();
    if down {
        emit(false);
    }
    if let (Some(manager), Some(current)) = (manager, current) {
        let _ = manager.unregister(current);
    }
}

#[cfg(target_os = "windows")]
fn pump() {
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
}
#[cfg(target_os = "macos")]
fn pump() {
    crate::macos_event_pump::pump();
}
#[cfg(target_os = "linux")]
fn pump() {} // global-hotkey owns the X11 connection's event thread.

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
}
