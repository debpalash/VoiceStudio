# Ask VoiceStudio Agent

The Electron launcher opens a resizable chat workspace inside the app. Select an installed
Codex, Claude Code, OpenCode or Pi harness, then describe a task. Feature chips supply focused
instructions for setup, repairs, voice cloning, voice design, dubbing, transcription, stories,
audiobooks, workflows and audio tools. Starter cards prefill a request without executing it.
Use **Send** (Enter) to run it; Shift+Enter adds a line.

**Autopilot** performs the requested supported app actions and verifies their results.
**Plan** inspects app state through a read-only API capability. Ordinary chat uses a temporary
app workspace even in a source build: no source checkout is required for app setup, generation,
or diagnosis. **Features > Allow source-code repair** explicitly opts into the attached
writable checkout and the selected harness's source-editing permissions.

Replies stream as chat messages. Follow-up turns receive the last twelve nonempty messages,
limited to 4,000 characters each. Conversation and draft state survive workspace navigation
and dock collapse for this app session; **New chat** clears them. They are not persisted to disk.
The selected harness receives the current request, recent conversation, current route, recent
renderer/backend logs and diagnostics. The harness may use its configured provider account.
No harness starts merely by opening the panel or selecting a preset.

Each run also receives a temporary connection file for an app-owned loopback API bridge. The bridge
targets the backend currently attached to VoiceStudio, injects remote authentication inside the
Electron main process, and gives the agent only a random per-run capability. Diagnose bridges reject
mutating methods. Authentication routes are blocked in both modes, and the bridge, capability and
connection file are removed when the run stops. This lets an authorized Fix select engines, install
models and verify live state without putting the real backend address or bearer token in the prompt,
agent output or child environment.

The connection file also lists capability-scoped Electron controls for reading supervisor status,
restarting the backend, and resuming runtime setup. These controls remain reachable when Python is
down, so the backend failure screen keeps the repair dock mounted and its **Fix** action can restore
the runtime before using normal backend APIs. Clean reinstall is intentionally absent because it
removes the owned runtime and remains an explicit **Clean Retry** decision.

Setup blockers use supported app actions before invoking an agent. For example, **Fix** on a
missing text-to-speech notice selects an installed compatible engine or starts the device-aware
required-model installer, follows that local or remote job to completion, refreshes every mounted
workflow, and activates the engine. An installer or activation failure becomes an `ACTION_REQUEST`
for the user's opted-in default agent with the current route and logs already attached. Clicking
**Fix** is the explicit authorization for that recovery; merely viewing the notice changes nothing.
Unresolved ASR setup in Dubbing, Dictation and Voice Conversion exposes the same agent action while
keeping prepared media, selected voices and retry state intact. Voice Design, voice comparison and
saved-profile previews use the same compact one-click TTS recovery instead of sending the user to a
Settings page.
Reference-audio ASR, accurate file transcription and live-dictation model failures expose the same
checkout-free action while preserving the selected media, editable transcript and retry state.

For checkout-free app operations, the agent discovers supported actions from the bridge OpenAPI
document, chooses ordinary model and engine settings from current hardware and recommendations, and
verifies the final state. A setup request sent in Autopilot authorizes its required model downloads and engine selection. Licenses, credentials, privacy or telemetry consent, data deletion and remote-device
connections remain explicit user decisions; the agent stops at that one decision instead of filling
it in silently.

Chat requests with `workspace: app` and legacy `ACTION_REQUEST` recovery runs operate a packaged app without a source checkout. The
agent starts in an isolated temporary directory, can reach only the session bridge, cannot use
authentication routes, and is instructed to inspect, perform and verify the requested app operation
without editing user files. Codex keeps its workspace sandbox for these sessions and enables network
access so it can reach the loopback bridge. Its automatic-review mode owns that workspace-write
sandbox; VoiceStudio does not also pass Codex's mutually exclusive explicit sandbox flag. Claude
Code and OpenCode receive a generated per-run MCP configuration whose default permission is deny;
their checkout-free sessions can call only VoiceStudio's scoped API tool and cannot invoke shell or
filesystem tools. The MCP process reads the capability from the protected connection file, so the
token never appears in agent arguments, configuration or output.

Explicit source diagnosis and code repair require a writable VoiceStudio source checkout and must follow its
`AGENTS.md`, `CLAUDE.md`, `CONTEXT.md`, and installed skills. Source builds select the current
checkout automatically. Packaged builds ask the user to choose a checkout only when source work is
actually required and remember that location. The picker rejects read-only folders and unrelated
repositories.

The repair prompt prohibits opening an issue, pushing, publishing, merging, or creating a pull
request. A successful fix ends with a review prompt so the user can inspect the diff and targeted
tests before deciding whether to propose a PR. An app-only recovery instead reports the verified
application state and any remaining user input; it never presents branch or pull-request guidance.

A renderer crash opens the dock with its sanitized error and stack. If no previously chosen harness is available, the prefilled chat waits for **Send**. Starting a run stores the local harness choice; later renderer
crashes start the available default agent automatically. If a code repair lacks a source checkout or
the selected agent is missing, the dock remains open and asks for that prerequisite instead of
discarding the failure. App-only recovery does not show or require the source picker.
Transient dynamic-module fetch failures perform one controlled renderer reload so the module loader
actually refetches the chunk. A session marker survives that reload, so only the same repeated
failure invokes agent recovery and no reload loop is possible.

Only one agent process runs at a time, including preparation and translation sessions. **Stop** cancels preparation or terminates a running process tree. Output is retained for
the current app session and streamed into the dock; JSON event formats from supported CLIs are
reduced to readable agent and tool output.

Verification: `electron/src/main/repair-api-bridge.test.ts` proves that the real remote credential
never enters the agent connection file, forged credentials are replaced inside main, Diagnose stays
read-only, authentication routes stay blocked, Electron restart/setup controls work without Python,
clean reinstall stays unavailable, and teardown removes the capability. The
`electron/tests/native-repair-agent-smoke.mjs` smoke launches an isolated Electron shell,
checks the native bridge and source checkout, verifies detected-agent controls, asserts that the
closed launcher stays centered on the right edge, and confirms that localized update-channel labels
render without raw keys. `electron/tests/error-recovery-smoke.mjs` verifies in-place render recovery,
the one-time chunk reload, scrubbed repeated-failure evidence and automatic repair handoff. Neither
smoke launches an agent or modifies the checkout.
`electron/src/shared/repair-request.test.ts` pins the boundary between checkout-free app operations
and source-gated diagnosis or code repair.
`node electron/tests/packaged-repair-agent-smoke.mjs` launches the packaged application with a clean
profile, confirms no source checkout is present, keeps app-operation Send/Plan enabled,
and verifies Electron main still rejects an ordinary source-repair request.
`node electron/tests/packaged-repair-operation-acceptance.mjs <agent>` is the opt-in live acceptance
for an installed CLI and an already-running packaged backend. It performs a read-only TTS readiness
check through the scoped bridge and starts no download. Windows packaged runs pass for Codex, Claude
Code and OpenCode; Pi was not installed on the verification host. The harness selects the native
package and backend port per platform. A packaged Ubuntu 26.04 run also proves that a Windows
OpenCode npm shim injected into WSL's PATH is rejected instead of being exposed as a usable Linux
agent; acceptance with a native Linux CLI remains required.

Bug reports include the scrubbed backend exception class when available, from
streaming error frames or parsed API error responses. This distinguishes otherwise
identical generic engine failures; absent or non-text class values are omitted.


## Short audiobook test

Use `samples/audiobook-agent-smoke.txt`, an original four-paragraph narration fixture with clean
paragraph breaks. Choose **Audiobook**, paste the text, and request a short preview with an
existing saved voice. Verify the returned project and audio instead of starting a full book.
This sample does not contain text from the copyrighted book supplied as a URL.

Automatic renderer-crash repair uses the source workspace. Without an attached
checkout, it opens the source-folder controls and preserves the request; choose
a checkout and press Send to continue. Explicit app action requests continue to
use the app workspace without a checkout.

On macOS, the development launcher rebuilds an incomplete branded Electron cache before launching. A missing executable no longer leaves the cache key permanently occupied; a complete cache still reuses its existing bundle. Launchers coordinate each cache key with an atomic `.lock` directory through validation, rebuilding and publication, so a repair cannot delete another launcher's complete bundle. Waiting for custody is bounded to 60 seconds. If a launcher is forcibly terminated, stop all development launchers and remove only the stale `<cache-key>.lock` directory under `electron/node_modules/.cache/voicestudio-electron-dev/` before retrying; unknown or live locks are never removed automatically.
