# Local speech platform

VoiceStudio is both a desktop dictation app and a headless local speech
service. The desktop app owns microphone capture, the dictation shortcut and
native insertion; the Python backend keeps ASR models warm and exposes the
audio data plane to any other interface.

```text
VS Code / custom GUI / remote mic ── PCM or WebM ───────> WS/HTTP :3900
                                                          │
                                                          └─ partial/final text

Scripts / TUIs / hooks ── audio files ──────────────────> HTTP :3900

Claude Code / Codex / Pi / agents ── MCP HTTP/stdio ───> MCP :3900
```

> **Native dictation control is retired.** The Tauri desktop app bundled a
> loopback control server on port 3902 (`/v1/dictation/*`, JSON-RPC,
> `--dictate-*` flags, output sessions). The Electron app does not provide it,
> and 3902 is now its development server's port. Use the in-app dictation
> shortcut, or capture audio yourself and stream it as described below.

## Discover capabilities

```bash
curl http://127.0.0.1:3900/.well-known/voicestudio-speech
```

The document returns `voicestudio.speech.v1` with relative URLs, so it also
works behind Tailscale or a reverse proxy. `features.native_dictation_control`
remains in the schema and is always `false`.

The dependency-free Python bridge is convenient for hooks and TUIs:

```bash
python -m backend.speech_client capabilities
python -m backend.speech_client transcribe recording.wav
```

It targets `VOICESTUDIO_URL`, else the loopback backend on `OMNIVOICE_PORT`
(default 3900), and sends `OMNIVOICE_API_KEY` as a bearer key when set. The
former `status`, `start`, `stop`, `toggle` and `transcribe --insert` commands
exit with an explanation.

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

## Batch and agent protocols

| Transport | Endpoint | Use |
|---|---|---|
| OpenAI-compatible HTTP | `POST :3900/v1/audio/transcriptions` | Files, scripts, existing SDKs |
| WebSocket | `:3900/v1/audio/transcriptions/stream` | Partial and final live text |
| MCP Streamable HTTP | `POST :3900/mcp/` (bare `/mcp` on current backends) | Modern agent clients |
| MCP stdio | `python -m backend.mcp_shim` | Claude Code, Codex, and stdio-only clients |

## Integration map

| Interface | Recommended connection |
|---|---|
| Any desktop text field | The app's global dictation shortcut |
| Pi, Claude Code, Codex, Antigravity CLI | Dictate into the focused prompt with the shortcut; add MCP when the agent also needs file transcription or speech tools |
| VS Code | Stream editor-owned mic audio over the versioned WebSocket |
| TUI or shell script | `python -m backend.speech_client` or the OpenAI-compatible HTTP endpoint |
| Browser/WebView UI | Stream audio to the Python data plane |
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

- No interface can turn on the desktop app's microphone remotely; capture
  starts only from the app itself. Remote ASR stays on the existing API-key
  boundary.
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
capture and insertion plus a protocol-neutral ASR service, so every interface
does not rebuild model loading.
