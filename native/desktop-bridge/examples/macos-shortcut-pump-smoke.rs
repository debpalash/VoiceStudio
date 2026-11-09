//! Post an application hotkey event without injecting keys or asking for permissions.
#[cfg(target_os = "macos")]
#[path = "../src/macos_event_pump.rs"]
mod macos_event_pump;

#[cfg(target_os = "macos")]
fn main() {
    use std::ffi::c_void;
    use std::ptr;
    use std::sync::atomic::{AtomicBool, Ordering};

    static RECEIVED: AtomicBool = AtomicBool::new(false);
    #[repr(C)]
    struct EventType {
        class: u32,
        kind: u32,
    }
    type Handler = unsafe extern "C" fn(*mut c_void, *mut c_void, *mut c_void) -> i32;
    #[link(name = "Carbon", kind = "framework")]
    unsafe extern "C" {
        fn GetApplicationEventTarget() -> *mut c_void;
        fn GetMainEventQueue() -> *mut c_void;
        fn InstallEventHandler(
            target: *mut c_void,
            handler: Option<Handler>,
            count: u32,
            types: *const EventType,
            user_data: *mut c_void,
            installed: *mut *mut c_void,
        ) -> i32;
        fn RemoveEventHandler(handler: *mut c_void) -> i32;
        fn CreateEvent(
            allocator: *const c_void,
            class: u32,
            kind: u32,
            time: f64,
            attributes: u32,
            event: *mut *mut c_void,
        ) -> i32;
        fn PostEventToQueue(queue: *mut c_void, event: *mut c_void, priority: i16) -> i32;
        fn ReleaseEvent(event: *mut c_void);
    }
    unsafe extern "C" fn receive(
        _call: *mut c_void,
        _event: *mut c_void,
        _data: *mut c_void,
    ) -> i32 {
        RECEIVED.store(true, Ordering::SeqCst);
        0
    }

    // The same application target and event class/kind global-hotkey uses.
    let event_type = EventType {
        class: u32::from_be_bytes(*b"keyb"),
        kind: 6, // kEventHotKeyPressed
    };
    unsafe {
        let mut handler = ptr::null_mut();
        assert_eq!(
            InstallEventHandler(
                GetApplicationEventTarget(),
                Some(receive),
                1,
                &event_type,
                ptr::null_mut(),
                &mut handler,
            ),
            0
        );
        let mut event = ptr::null_mut();
        assert_eq!(
            CreateEvent(ptr::null(), event_type.class, event_type.kind, 0.0, 0, &mut event),
            0
        );
        assert_eq!(PostEventToQueue(GetMainEventQueue(), event, 1), 0);
        ReleaseEvent(event); // the queue retains its own reference
        for _ in 0..10 {
            macos_event_pump::pump();
            if RECEIVED.load(Ordering::SeqCst) {
                break;
            }
        }
        RemoveEventHandler(handler);
    }
    assert!(
        RECEIVED.load(Ordering::SeqCst),
        "queued Carbon hotkey was never dispatched"
    );
    // Empty queues must also return promptly so the helper can process Stop.
    assert!(!macos_event_pump::pump(), "idle pump reported a backlog");
    // The helper sleeps in the run loop between events (#3924): a bounded wait
    // must return, and the cross-thread wake (an empty main-queue block) must
    // end a sleep long before its limit.
    use std::time::{Duration, Instant};
    let started = Instant::now();
    macos_event_pump::wait(Some(Duration::from_millis(50)));
    assert!(
        started.elapsed() < Duration::from_secs(2),
        "bounded wait overran"
    );
    std::thread::spawn(|| {
        std::thread::sleep(Duration::from_millis(100));
        dispatch2::DispatchQueue::main().exec_async(|| {});
    });
    let started = Instant::now();
    macos_event_pump::wait(Some(Duration::from_secs(20)));
    assert!(
        started.elapsed() < Duration::from_secs(5),
        "a main-queue wake did not end the run-loop sleep"
    );
    println!("PASS: Carbon application hotkey dispatch and idle pump");
    println!("PASS: run-loop sleep ends on the cross-thread wake");
}

#[cfg(not(target_os = "macos"))]
fn main() {
    println!("SKIP: macOS Carbon event pump");
}
