//! Dispatch Carbon hotkeys as well as Core Foundation sources on the main thread.
use std::ffi::c_void;
use std::ptr;

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

pub fn pump() {
    // CF sources deliver media keys, but Carbon hotkeys also need their queued
    // events explicitly dispatched. Never wait for an event here: registration
    // commands and EOF/Stop still need a turn when no shortcut is pressed.
    unsafe {
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.001, true);
        // A flood of events must not starve the helper's command channel.
        for _ in 0..64 {
            let mut event = ptr::null_mut();
            if ReceiveNextEvent(0, ptr::null(), 0.0, 1, &mut event) != 0 || event.is_null() {
                break;
            }
            SendEventToEventTarget(event, GetEventDispatcherTarget());
            ReleaseEvent(event);
        }
    }
}
