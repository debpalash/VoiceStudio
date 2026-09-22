# Agentic voice: VoiceStudio as a TTS/STT provider

VoiceStudio exposes a **local speech platform**—OpenAI-compatible batch audio,
a versioned transcription WebSocket, native dictation control, and MCP—so any agent framework that
speaks to OpenAI's audio endpoints can use your local VoiceStudio for speech —
in your own cloned voice, with nothing leaving your machine. You bring the
agent runtime; VoiceStudio is the voice.

For dictating directly into Claude Code, Codex, Pi, Antigravity CLI, Herdr, or
another focused prompt, use the [Rust control sidecar](speech-platform.md).

This is "agentic v1": VoiceStudio is a provider, not the orchestrator. You wire
your own agent (a support line, a desk assistant, a Discord persona) and point
its TTS/STT at VoiceStudio.

> **Scope.** This page covers VoiceStudio-as-provider. Outbound phone calls are a
> separate, deferred milestone (they need a paid carrier — there is no
> fully-local path to the PSTN) and ship only behind explicit consent
> guardrails. See the roadmap in `docs/competitive-analysis.md` (§R1).
> Answering **inbound** calls with a spoken greeting is available as an opt-in
> integration: see [Twilio](integrations/twilio.md).

## The endpoints

VoiceStudio's service root is `http://localhost:3900` (or your
[remote backend URL](remote-gpu.md)). OpenAI-compatible clients use
`http://localhost:3900/v1` as their base URL, while discovery stays at the
service root: `http://localhost:3900/.well-known/voicestudio-speech`.

| OpenAI route | VoiceStudio support |
|---|---|
| `POST /v1/audio/speech` | TTS. `model` = engine id, `voice` = a voice-profile id (your clone) or preset, `response_format` incl. `pcm` and `wav`, `speed`. Default output is 24 kHz. |
| `POST /v1/audio/transcriptions` | STT (Whisper-family). |
| `WS /v1/audio/transcriptions/stream` | Live partial/final STT from PCM or WebM. |
| `GET /.well-known/voicestudio-speech` | Machine-readable transport discovery. |
| `GET /v1/audio/voices` | list available voices (VoiceStudio extension). |

A contract test (`tests/test_agentic_provider_contract.py`) pins this request
shape in CI, so the recipes below won't silently break.

## pipecat (recommended)

[pipecat](https://github.com/pipecat-ai/pipecat) (BSD-2) runs as a Python
library inside your own process — no extra server. Point its OpenAI TTS/STT
services at VoiceStudio:

```python
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.services.openai.stt import OpenAISTTService

tts = OpenAITTSService(
    base_url="http://localhost:3900/v1",
    api_key="not-needed-locally",        # any string; VoiceStudio ignores it unless OMNIVOICE_API_KEY is set
    voice="<your-voice-profile-id>",     # from GET /v1/audio/voices, or "default"
    model="omnivoice",                   # or any installed engine id
    sample_rate=24000,                   # matches VoiceStudio's default output
)

stt = OpenAISTTService(
    base_url="http://localhost:3900/v1",
    api_key="not-needed-locally",
)
```

Drop those into any pipecat pipeline (VAD, turn-taking, and LLM stay local
too). A minimal runnable example is in
[`examples/agentic/pipecat_minimal.py`](../examples/agentic/pipecat_minimal.py).

## LiveKit Agents

[LiveKit Agents](https://github.com/livekit/agents) (Apache-2.0) needs a
LiveKit media server alongside, but its OpenAI plugin takes the same
`base_url`:

```python
from livekit.plugins import openai

tts = openai.TTS(base_url="http://localhost:3900/v1", api_key="x", voice="<profile-id>")
stt = openai.STT(base_url="http://localhost:3900/v1", api_key="x")
```

Choose LiveKit over pipecat only when you need its WebRTC/SIP scale; for a
single local agent, pipecat is lighter.

## Remote backend

Running VoiceStudio on a [remote GPU box](remote-gpu.md)? Append `/v1` to that
backend's service-root URL for the OpenAI client's `base_url`, and pass its
`OMNIVOICE_API_KEY` as the `api_key` — the same bearer the rest of the app uses.
Keep the unmodified service root for `/.well-known/voicestudio-speech`
discovery, and keep the backend on your tailnet, not the open internet.

## Use your own voice responsibly

When an agent speaks in a cloned voice, prefer a profile you've marked
**verified own voice** (Settings → a voice profile → Voice ownership). That
consent lock is what gates the heavier agentic features as they land, and
it's the honest default for "an AI is speaking as me."
