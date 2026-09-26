//! Enigo's macOS keyboard-layout lookup calls main-thread-only HIToolbox APIs.
//! Keep only keyboard operations on that thread, not session/clipboard locks.

pub(crate) fn on_keyboard_thread<F, R>(work: F) -> R
where
    F: FnOnce() -> R + Send,
    R: Send,
{
    #[cfg(target_os = "macos")]
    if objc2::MainThreadMarker::new().is_none() {
        let mut result = None;
        dispatch2::DispatchQueue::main().exec_sync(|| result = Some(work()));
        return result.expect("synchronous keyboard dispatch did not complete");
    }
    work()
}
