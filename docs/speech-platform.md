# Local speech platform

VoiceStudio is both a desktop dictation app and a headless local speech
service. The desktop remains one app: its bundled Rust control sidecar owns
microphone activation, focused-target capture, clipboard safety, and native
insertion; the Python backend keeps ASR models warm and exposes the audio data
plane.

This split lets an integration choose how much it owns:

```text
Herdr / terminal / desktop app ── start, stop, toggle ──> Rust control :3902
                                                          │
                                                          ├─ captures target
                                                          ├─ opens VoiceStudio mic
                                                          └─ inserts final text

VS Code / custom GUI / remote mic ── PCM or WebM ───────> WS/HTTP :3900
                                                          │
                                                          └─ partial/final text
                          reserve target / insert final ─> Rust control :3902

Claude Code / Codex / Pi / agents ── MCP HTTP/stdio ───> MCP :3900
```

The Rust sidecar is part of the VoiceStudio process, not a second application.
It starts with the desktop app and binds only to `127.0.0.1`.

## Discover capabilities

Desktop/native discovery:

```bash
curl http://127.0.0.1:3902/.well-known/voicestudio-speech
```

Engine/data-plane discovery:

```bash
curl http://127.0.0.1:3900/.well-known/voicestudio-speech
```

Both return `voicestudio.speech.v1`. The desktop document includes absolute
control, batch, streaming, output-session, and MCP endpoints. The backend
document uses relative URLs so it also works behind Tailscale or a reverse
proxy; it advertises native control only when launched by the desktop app.

## Use VoiceStudio capture from any app

These calls use VoiceStudio's existing microphone, model selection, pill,
refinement, and session-bound insertion. The app under the cursor remains the
destination.

```bash
curl -X POST http://127.0.0.1:3902/v1/dictation/start
curl -X POST http://127.0.0.1:3902/v1/dictation/stop
curl -X POST http://127.0.0.1:3902/v1/dictation/toggle
```

JSON-RPC clients use the same actions:

```json
{"jsonrpc":"2.0","id":1,"method":"dictation.toggle"}
```

Send that object to `POST http://127.0.0.1:3902/rpc`. The installed
VoiceStudio executable also accepts `--dictate-start`, `--dictate-stop`, and
`--dictate-toggle`; the single-instance bridge forwards them to the running
app without opening the Studio window.

The dependency-free Python bridge is convenient for hooks and TUIs:

```bash
python -m backend.speech_client status
python -m backend.speech_client toggle
python -m backend.speech_client transcribe recording.wav
python -m backend.speech_client transcribe recording.wav --insert
```

`--insert` captures the focused destination before transcription starts and
uses the same clipboard-preserving native delivery as the global shortcut.

## Bring your own capture interface

An editor extension or GUI can own the microphone and consume live text.
Connect to:

```text
ws://127.0.0.1:3900/v1/audio/transcriptions/stream
```

Send binary WebM/Opus frames by default. For raw signed 16-bit mono PCM, use
`?pcm=1&sr=16000`. Finish without closing the socket by sending:

```json
{"type":"input_audio.end"}
```

Every response carries `protocol` and `session_id`:

```json
{"type":"session.started","protocol":"voicestudio.speech.v1","session_id":"..."}
{"type":"partial","text":"hello wor...","session_id":"..."}
{"type":"final","final_kind":"summary","text":"Hello world.","session_id":"..."}
```

Streaming Sherpa models can also emit `final_kind: "utterance"` before the
authoritative whole-session `summary`. Existing `/ws/transcribe` clients keep
their unchanged legacy frames and `EOF` control.

To reuse native insertion with a custom capture client:

1. `POST /v1/output/sessions` on port 3902 before opening the microphone.
2. Stream audio and receive the final text on port 3900.
3. `POST /v1/output/sessions/{id}/insert` with `{"text":"..."}`.
4. If capture is cancelled, `DELETE /v1/output/sessions/{id}`.

Only one output session can own a focused destination at a time. Stale IDs are
rejected instead of inserting into a newer target.

## Batch and agent protocols

| Transport | Endpoint | Use |
|---|---|---|
| OpenAI-compatible HTTP | `POST :3900/v1/audio/transcriptions` | Files, scripts, existing SDKs |
| WebSocket | `:3900/v1/audio/transcriptions/stream` | Partial and final live text |
| MCP Streamable HTTP | `POST :3900/mcp/` (bare `/mcp` on current backends) | Modern agent clients |
| MCP stdio | `python -m backend.mcp_shim` | Claude Code, Codex, and stdio-only clients |
| JSON-RPC | `POST :3902/rpc` | Native dictation control |
| Native CLI | VoiceStudio `--dictate-*` flags | Hooks and plugin actions |

## Integration map

| Interface | Recommended connection |
|---|---|
| Any desktop text field | Existing global shortcut or Rust `dictation.toggle` |
| Herdr | Merge [the example command bindings](../examples/speech-platform/herdr-config.toml) into Herdr's config; detached commands call the Rust API while the pane stays focused |
| Pi, Claude Code, Codex, Antigravity CLI | Dictate into the focused prompt through Rust; add MCP when the agent also needs file transcription or speech tools |
| VS Code | Call Rust HTTP from the extension host for app-wide dictation, or stream editor-owned mic audio over the versioned WebSocket |
| TUI or shell script | `python -m backend.speech_client` or HTTP/JSON-RPC |
| Browser/WebView UI | Stream audio to the Python data plane; browser pages cannot silently call native control |
| Remote microphone + local/remote GPU | Capture at the client edge and use the authenticated WebSocket/OpenAI endpoint |

Loopback clients need no credential. Remote native WebSocket clients can send
the configured bearer key. Browser clients should exchange that key for a
short-lived session, mint a path-bound ticket at `/api/auth/ws-ticket`, and
connect with `?ws_ticket=...`; see [API authentication](api-auth.md).
Keep remote endpoints restricted to a trusted network; an API key authenticates
a client but does not provide network isolation. Beyond a fully trusted LAN,
use HTTPS/WSS and never send bearer credentials or ticket exchanges over
plaintext HTTP/WebSocket.

## Security and privacy

- The native control sidecar binds only to IPv4 loopback and rejects untrusted
  browser `Origin` headers, blocking ordinary websites from turning on the mic.
- Native control never accepts audio and is never exposed through Network
  Sharing. Remote ASR stays on the existing API-key boundary.
- Microphones stay at the interface edge. A remote GPU backend never assumes
  it owns the user's input device.
- No protocol adds a required network call, account, analytics event, or cloud
  provider.

## Research basis

The design survey covered five pages of GitHub's
[`speech-to-text` topic](https://github.com/topics/speech-to-text):
[1](https://github.com/topics/speech-to-text?page=1),
[2](https://github.com/topics/speech-to-text?page=2),
[3](https://github.com/topics/speech-to-text?page=3),
[4](https://github.com/topics/speech-to-text?page=4), and
[5](https://github.com/topics/speech-to-text?page=5).

The platform keeps the strongest reusable ideas without copying their UI
boundaries:

| Source | Adopted idea |
|---|---|
| [Handy](https://github.com/cjpais/Handy) | Cross-platform offline dictation, external toggle control, VAD-oriented capture |
| [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit) | Live local transcription and compatibility-oriented serving |
| [RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) | Low-latency partials, endpointing, and warm recognizers |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | Portable CPU streaming models and WebSocket-friendly audio framing |
| [FunASR](https://github.com/modelscope/FunASR) | OpenAI-compatible and MCP-facing serving |
| [Vexa](https://github.com/Vexa-ai/vexa) | WebSocket transcripts plus agent access |
| [Voquill](https://github.com/voquill/voquill) | Provider independence, refinement, and personal-vocabulary direction |
| [Muesli](https://github.com/Muesli-HQ/muesli) | Machine-readable CLI contracts and session-safe automation |
| [Herdr](https://github.com/motionharvest/herdr) | One local control surface behind CLI, socket, hooks, and plugin integrations |

The differentiator is the connection layer: one bundled app offers native
capture/output control and a protocol-neutral ASR service, so every interface
does not rebuild model loading, desktop permissions, and insertion safety.
