# Electron network and credentials

Settings now exposes Network and Credentials in the shared settings shell. Sidebar search includes proxy, Hugging Face, DeepL and Microsoft labels.

Network reads the configured proxy from `/system/info`. Save and Clear update all six upper/lowercase HTTP, HTTPS and ALL proxy variables using the same helper as Tauri. Writes run sequentially; failures stop the sequence and do not show success. A partial failure can be retried or cleared. Saving does not modify the audio-tool executable setting; its link opens Audio tools.

Credentials shows the Hugging Face resolver sources (App, environment, CLI), masked values, active source and validation status. Ordinary reads use local state only. Test now explicitly requests fresh validation. Save clears the password input after success. Clearing asks inline and clears only the app token by default; CLI-file removal requires its separate opt-in switch. Environment credentials remain managed outside the app. Secret inputs never enter draft persistence or browser storage.

DeepL and Microsoft keys/base URLs use the existing persisted `/system/set-env` contract. Field definitions are shared with Tauri. Blank inputs cannot overwrite stored credentials; successful saves clear the input. Provider connectivity is not inferred from a successful settings write.

`electron/tests/connection-settings-smoke.mjs` checks proxy save/reload/clear, partial failure, local token reads, explicit validation, duplicate submission, app-only/CLI clearing, and provider-key submission against mocked routes. The live backend token-state read returned the expected source schema. No real proxy or credential values were changed for verification. Tauri Network and Translation regression tests pass after helper extraction.

Model settings now include Hugging Face mirror selection: automatic routing,
backend-advertised presets and a custom URL. Settings reads use cached endpoint
status; the network test runs only on explicit click. Writes use the existing
backend and honor its restart-required response. Browser fixtures verify saves,
reload, failed writes and Auto reset. The live read-only schema was verified;
no real mirror preference was changed or probe triggered during verification.

Model downloads follow the compute target selected when the request starts. The
control plane retains authenticated remote-worker progress so Models can recover
it after navigation or renderer reconnect. Jobs are keyed by both repository and
target, preventing a local download of the same repository from appearing on a
remote model card. Cancel sends only the worker's advertised opaque model ID,
keeps polling while the worker drains the install, and completes after the worker
returns a terminal progress event.

Engine Ready resolves TTS against the selected execution target. For a remote
device it shows that worker's advertised engine/model, install and download
readiness, execution backend, and heartbeat-confirmed residency; ASR,
translation, dictation and diarisation remain attributed to the local device
because those operations are not remotely routed. Clone, Design, Stories,
Audiobook, profile preview and comparison actions use the same
operation-scoped readiness, including cloning-capability filtering for Clone,
so a model available only on the selected worker is usable without a duplicate
local installation. If the selected worker is
offline or an operation is local-only, readiness follows the backend's real
local fallback instead of blocking on the remote choice. Batch validates the
selected target before files or watched-folder items enter the queue, sends one
coarse segment bundle per target language to a capable worker, and keeps ASR,
translation, timeline assembly and muxing local. Dubbing and Batch preflight the
selected worker and load local TTS lazily only if dispatch falls back, so a
remote-only installation does not require duplicate weights on the control
device.

Sharing exposes the backend only after an inline confirmation. It shows the active PIN and LAN addresses, generates QR links locally, supports a configurable share port, and controls the backend's Tailscale serve integration. These actions remain explicit and do not run during settings reads.

Compare voices reports missing speech when a generated preview contains only
part of the requested phrase. Its warning includes the omitted text when
available, or the number of omitted parts. Surviving audio remains playable,
and comparison continues with the other voice. Healthy previews stay quiet;
leaving the comparison suppresses notices from cancelled results.

Remote backend configuration is owned by Electron main. Connection tests validate a VoiceStudio health response and exchange an optional server master key once for a scoped, expiring session. The renderer clears the key immediately; only the URL is persisted. Main injects the session into production and development HTTP proxy traffic, native watch-folder uploads, and path-bound dictation WebSocket tickets. Switching back to the local backend is always available.

Remote-worker routing was exercised against an Ubuntu 26.04 WSL worker with an RTX 4090. A real profile-backed TTS request returned a WAV with `X-OmniVoice-Routing: remote`; stopping the worker changed the same selected target to an explicit local fallback, and a second request returned `X-OmniVoice-Routing: local_fallback`. Restarting the worker restored remote readiness without re-enrollment. Dubbing and Batch also completed real multi-segment worker tasks: the Batch proof returned two exact indexed, non-silent mono WAVs at 24 kHz in one committed bundle. The selected target was returned to Local after verification.
