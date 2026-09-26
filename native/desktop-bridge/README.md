# VoiceStudio native desktop bridge

This internal helper compiles Tauri's existing `dictation_output.rs` directly. It shares session acceptance/rejection, focus targeting, clipboard ownership/restoration, live corrections and platform fallbacks. Its crate version is an internal placeholder, not an app release version.

The first argument is the owning Electron process ID. The only IPC is newline-delimited JSON on inherited stdin/stdout. Requests carry a numeric `id` and a tagged `method`; replies carry the same ID and a `result` or `error`. The helper has no listener or network endpoint. Requests and transcripts are bounded. Dispatch remains concurrent so shortcut target capture can precede the shared delivery lock. EOF completes accepted operations and releases the current session.

Build/test from the repository root:

```
cargo test --locked --manifest-path native/desktop-bridge/Cargo.toml
cargo build --locked --manifest-path native/desktop-bridge/Cargo.toml
bun electron/tests/native-output-smoke.ts
```

Electron's `DictationOutputClient` owns the child and its private pipes. Failed or timed-out requests are never replayed. A timeout terminates the helper/process group to prevent a delayed second delivery.

Current evidence: Windows native build, all 19 shared/protocol tests, three transport tests, real ping/stale-session smoke, and the Tauri module's 16 tests pass. No synthetic keystroke or clipboard write is performed by the smoke test.

Electron distribution builds now compile the matching Rust release target and copy it into `resources/native` before application signing. Windows helper builds use static CRT linkage and the configured Windows signing hook. The packaged Electron smoke launches the helper from its actual resource path with Electron?s process ID. Windows packaging and protocol startup have passed; macOS/Linux packaging still requires native-runner verification.

Electron now integrates trusted recorder-only IPC, a tray recorder and session-bound output. A Windows integration smoke (`node electron/tests/native-insertion-smoke.mjs`, after building Electron and the debug helper) verifies insertion into a separate disposable process, multiple utterances and clipboard restoration. It asserts the target owns the Windows foreground window before capture and snapshots/restores the original clipboard atomically using Electron's current ClipboardItem API. ASR is deterministic in this test; native paste is real.

The smoke exposed a shared delivery race: input injection enqueues a paste but does not wait for the application to consume it. The shared operation lock now stays held through the existing 300 ms clipboard-consumption window, preventing another utterance from replacing the staged text or resetting Windows modifier state prematurely. The smoke failed before this change and passed twice after it; all 19 helper/protocol and 16 Tauri output tests also pass.

Remaining verification: native non-Windows recorder/portal/insertion runs, actual microphone/device checks. Tauri intentionally keeps active transcription exclusive; Electron preserves that behavior and permits immediate restart from completed/error states.

Shortcut registration now uses the same `global-hotkey` 0.8.0 backend as Tauri,
with main-thread Windows/macOS event pumping, distinct press/release events and
registration rollback. The helper captures a session target on key-down before
sending the event to Electron. Windows native registration/key injection verifies
one press/release pair, matching session IDs and preservation of the prior binding
when replacement fails (`bun electron/tests/native-shortcut-smoke.ts`).

Wayland portal logic lives in `src/wayland_shortcut_core.rs`;
Tauri keeps a thin adapter and its existing desktop identity. Electron supplies
its own desktop identity and executable. Portal consent, request ordering and
existing tests remain shared. Linux helper plus tests and the macOS ARM helper
cross-check successfully; actual portal/macOS interaction still needs native runners.
Electron now wires these events through saved shortcut settings, shared backend
hold/toggle preferences and native session adoption. Windows full-flow smoke checks
cover shortcut recording/save, early hold release, toggle and disabling registration.
