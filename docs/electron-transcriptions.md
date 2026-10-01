# Electron transcriptions

Open Transcriptions above the engine list, or through command search. Upload an
audio file or start dictation, then stop recording to transcribe. The workspace
checks dictation readiness before recording/upload and again before transcription.
When no model is active, the workspace offers an installed model first or the
backend-recommended model to download after an explicit click. It shows install
progress, supports cancellation, and unlocks recording/upload when ready. If the
dictation engine is unavailable, use Settings > Models > Dictation to recover it.

Results use the existing Tauri `omni_transcriptions` history format, shared reader
and writer, with the same newest-first 200-entry bound. Electron and Tauri have
separate browser storage origins; sharing the format does not migrate old data
between applications. You can search results, inspect available segment timings,
copy or export text, delete an entry, and send text to the cloning script.
The secondary pane is labeled **History** so its saved-result list stays distinct
from the Transcribe workspace and its recording actions.

The empty workspace now leads with Start dictation and Upload audio. History-only
search/export/clear controls stay hidden until a transcription exists, and backend
failures appear once with direct Retry and Dismiss actions.

Recordings use the existing capture/cleanup helpers. Playback uses Vidstack.
Pending HTTP requests abort when leaving the workspace. Refinement settings,
native dictation and target-app delivery are described below. Cross-application
history migration remains separate parity work.

Electron installs Chromium permission-check and request handlers before either
the main window or recorder loads. Only the trusted VoiceStudio top-level origin
receives audio capture; camera, embedded-frame and foreign-origin requests are
denied. The operating system's microphone permission remains authoritative.

Dictation model settings now expose optional LLM cleanup, filler removal, self-corrections and technical-term preservation. Upload/record transcription reads the saved master setting; if settings are unavailable, it safely keeps raw transcription. Raw text remains in history, with any refined text stored separately and available in an expandable section with Copy. Reference-audio ASR continues to use raw transcription. Cleanup failure notes reuse the shared Tauri status mapping.

Transcriptions now exposes microphone and mono/stereo selection using the exact
same RecordingInputs component as reference recording. Controls are disabled
during capture/processing and feed the existing recorder constraints. Browser
checks cover device/channel choice with a simulated device list; physical device
recording remains a native macOS/Linux verification gate. Windows shortcut and
target-insertion coverage is described below.

Recorder lifecycle guards now release microphone access granted after navigation
and cancel pending cleanup on unmount. A late cleanup result cannot start a
transcription or load a reference in a closed view. Two fail-before/pass-after
regressions cover delayed permission and delayed cleanup.

Transcription timing labels and text-export formatting are shared with Tauri.
Exports retain date/language context, use the same dated filename and confirm a
successful save; segment labels show both timing bounds
when available without inventing missing values. Shared Tauri tests and browser
checks of the downloaded file verify this behavior.

History supports Clear all with inline confirmation and cancellation. A failed
storage write preserves visible and stored history and leaves confirmation open
for retry. Individual deletion reads the latest stored entries before writing.
Browser checks cover cancel, failed storage and successful confirmed clearing.

Live Dictation now streams mono 16 kHz PCM through the shared Tauri AudioWorklet
and anti-alias capture graph. Record remains the separate clip-preview workflow.
Dictation checks the selected model is enabled and installed before requesting
a microphone; pause, stop and cancel preserve explicit session boundaries.
Live utterances enter history once, including repeated speech, and EOF summaries
avoid duplicate entries. Legacy fallback finals also finish normally. Failed
history writes retain visible text for copying. Leaving the page releases capture.

Verification includes eight controller tests, mocked browser controls and a real
installed Parakeet session using Chromium's prerecorded test input (no physical
microphone). The backend returned live speech and final history successfully.
The real Faster-Whisper upload check also verifies timed segments, text export,
Clone handoff, persisted history and deletion against the running local backend.
The production build emits the exact shared worklet asset. The recorder widget,
global shortcuts and target delivery are implemented; native Windows acceptance
passes, while physical microphone and macOS/Linux interaction remain release gates.

The tray now offers localized Start dictation and Stop recording actions backed
by a dedicated, non-activating recorder window. It captures the output target
before revealing the recorder, queues startup/stop events until registration,
and uses the shared native delivery helper. The main app frame cannot invoke
recorder-only output IPC. Cancellation/navigation/crash invalidate pending work;
sequence numbers reject duplicate deliveries. A late WebSocket URL or remote
ticket from a cancelled start cannot reconnect or replace the next dictation
session. Clipboard fallback retains the
complete transcript and is labeled as copied, not inserted.

Native Windows smoke checks cover helper acceptance, pause/resume, no-speech,
cancel/reopen, main-frame output denial and saved transcript history. A separate
disposable target-app smoke verifies actual ordered insertion and clipboard
restoration. Native macOS recorder/insertion and Linux portal shortcut behavior
with a physical microphone remain unverified; WSLg verifies tray/watch behavior
and clipboard fallback where no desktop portal is available.

Windows cross-application insertion is now verified against a disposable second
Electron process: two utterances arrive in order and the clipboard is restored.
The test uses deterministic ASR and a fake microphone, but the real native target,
activation and paste path. It found a shared Tauri/Electron race between queued
paste consumption and the next utterance. The native operation now holds its lock
through the existing 300 ms clipboard-consumption window. The integration failed
before the fix and passed twice afterward; 19 helper and 16 Tauri tests pass.
macOS/Linux insertion and global shortcut/hold/portal behavior remain native
acceptance gates.

Global dictation now follows the saved shared backend mode: Hold stops on key
release (including release before microphone startup), while Toggle stops on the
next press. Settings > Models > Dictation exposes mode, key recording, save and
reset. Key recording reuses Tauri's parser and modifier/cancel behavior. The
accelerator is stored atomically in Electron userData; a failed save restores the
previous native binding. Disabled dictation unregisters the shortcut. Repeated
preferences refreshes do not reopen a declined portal permission request.

Windows native integration verifies the settings recording/save path, persisted
accelerator, real global key presses, early hold release, toggle and disable. It
uses a fake microphone and a no-speech response, so it does not type into arbitrary
apps. Native session and settings regressions total 19; Tauri's seven existing
key-recording tests still pass. Native macOS/Wayland interaction and restarting
capture while a previous session is still transcribing remain native verification
work. Windows behavior parity is verified with deterministic ASR and a fake mic.

Capture restart now matches Tauri's actual state rules: transcription remains
exclusive, while a visible completed/no-speech/error result accepts the next
shortcut immediately. A session-tagged phase report enables that decision in
main. Replacement retains an early hold release while old output cleanup finishes;
stale cancellation cannot dismiss the new session. The Windows restart integration
failed before this change and passes after it, alongside 17 session regressions.

Electron development now uses the renderer's same-origin WebSocket proxy even
when the preload supplies a backend URL; the packaged app scheme still uses the
trusted backend URL directly. Both paths have targeted coverage. A real native
Electron development run, with prerecorded microphone input and installed Parakeet,
returned live text and saved the transcript through the proxy successfully.
