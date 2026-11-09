//! Dispatch Carbon hotkeys as well as Core Foundation sources on the main thread.
use std::ffi::c_void;
use std::ptr;
use std::time::Duration;

#[link(name = "CoreFoundation", kind = "framework")]
unsafe extern "C" {
    static kCFRunLoopDefaultMode: *const c_void;
    fn CFRunLoopRunInMode(mode: *const c_void, seconds: f64, return_after_source: bool) -> i32;
}

#[link(name = "Carbon", kind = "framework")]
unsafe extern "C" {
    fn ReceiveNextEvent(
        count: u32,
        types: *const c_void,
        timeout: f64,
        remove: u8,
        event: *mut *mut c_void,
    ) -> i32;
    fn GetEventDispatcherTarget() -> *mut c_void;
    fn SendEventToEventTarget(event: *mut c_void, target: *mut c_void) -> i32;
    fn ReleaseEvent(event: *mut c_void);
}

/// A flood of events must not starve the helper's command channel.
const MAX_EVENTS_PER_PUMP: usize = 64;
/// CFRunLoopRunInMode needs a finite limit; this one is effectively "until woken".
const FOREVER_SECONDS: f64 = 1.0e9;

/// Sleep in the main run loop until one source fires (a window-server event
/// such as a hotkey, a main-queue block, a media-key tap) or `timeout` passes.
pub fn wait(timeout: Option<Duration>) {
    let seconds = timeout.map_or(FOREVER_SECONDS, |timeout| timeout.as_secs_f64());
    unsafe {
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, seconds, true);
    }
}

/// Dispatch what is already queued without waiting. Returns `true` when the
/// per-call cap left Carbon events behind, so the caller must not sleep.
pub fn pump() -> bool {
    // CF sources deliver media keys, but Carbon hotkeys also need their queued
    // events explicitly dispatched. Never wait for an event here: registration
    // commands and EOF/Stop still need a turn when no shortcut is pressed.
    unsafe {
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.0, true);
        for _ in 0..MAX_EVENTS_PER_PUMP {
            let mut event = ptr::null_mut();
            if ReceiveNextEvent(0, ptr::null(), 0.0, 1, &mut event) != 0 || event.is_null() {
                return false;
            }
            SendEventToEventTarget(event, GetEventDispatcherTarget());
            ReleaseEvent(event);
        }
    }
    true
}
