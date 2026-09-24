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
| `POST /v1/audio/speech` | TTS. `model` = an installed engine id, or an OpenAI model id (`tts-1`, `tts-1-hd`, `gpt-4o-mini-tts` and its dated snapshots) for the active engine. `voice` = a voice-profile id (your clone), an engine preset, or an OpenAI voice name (`alloy`, `ash`, `coral`, … — the engine's default voice). `instructions` becomes the engine's style instruction; OmniVoice keeps only its voice-design tags (such as `female, whisper`) and ignores other prose, and VoiceStudio's own `instruct` wins when both are sent. `speed`, and `stream_format` `audio` (chunked bytes) or `sse` (`speech.audio.delta` events). |
| `POST /v1/audio/transcriptions` | STT with the active speech-recognition engine; any OpenAI model id works, while a VoiceStudio engine id must name the active engine (400 `model_not_active` otherwise); a file with no audio stream returns 400 `no_audio_track`. `language`, `prompt` and `temperature` reach engines that support them (the Whisper family). `response_format` `json`, `text`, `verbose_json` (OpenAI segments, plus `words` with `timestamp_granularities[]=word`), `srt`, `vtt`. `stream=true` is not supported — use the WebSocket below. |
| `POST /v1/audio/translations` | Speech → English text. Needs a Whisper-family engine (faster-whisper, WhisperX, MLX Whisper, PyTorch Whisper) running a multilingual checkpoint such as `large-v3`. Turbo, Distil-Whisper and English-only (`.en`) checkpoints are transcription-only, and like other engines they return a clear 400 instead of untranslated text. |
| `WS /v1/audio/transcriptions/stream` | Live partial/final STT from PCM or WebM. |
| `GET /v1/models`, `GET /v1/models/{id}` | OpenAI's model list: the OpenAI aliases above, every installed TTS engine, and the active STT engine. |
| `GET /.well-known/voicestudio-speech` | Machine-readable transport discovery. |
| `GET /v1/audio/voices` | list available voices (VoiceStudio extension). |

Speech `response_format` returns exactly the format asked for:

| Format | Body | `Content-Type` |
|---|---|---|
| `mp3` (default) | MP3 | `audio/mpeg` |
| `opus` | Opus in Ogg, 48 kHz | `audio/ogg` |
| `aac` | AAC (ADTS) | `audio/aac` |
| `flac` / `wav` | lossless, at the engine's sample rate | `audio/flac` / `audio/wav` |
| `pcm` | raw 24 kHz 16-bit little-endian mono, as OpenAI specifies — resampled from the engine's rate | `audio/pcm` |

`mp3`, `opus` and `aac` are encoded with ffmpeg (bundled with VoiceStudio). If no
ffmpeg is found, the request fails with a 400 naming the fix before any audio is
generated; `wav`, `flac` and `pcm` need no encoder.

Errors on these routes use OpenAI's shape — `{"error": {"message", "type",
"param", "code"}}` — so the SDK raises a typed exception with a readable
message. Invalid requests are `400`, as with OpenAI. The body also keeps the
`detail` field existing VoiceStudio clients read.

Contract tests pin this surface in CI: `tests/test_agentic_provider_contract.py`
(the pipecat/LiveKit request shape) and `tests/test_openai_sdk_contract.py`
(drives every route through the official `openai` SDK).

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

## OpenAI Agents SDK

The [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/voice/quickstart/)
voice pipeline takes an OpenAI client, so hand it one pointed at VoiceStudio.
Its default models (`gpt-4o-transcribe`, `gpt-4o-mini-tts`), default voice and
24 kHz PCM output all work unchanged. The **OpenAI Agents** page under
Integrations shows this snippet with your backend's address filled in:

```python
import os

from agents import Agent, OpenAIChatCompletionsModel, set_tracing_disabled
from agents.voice import (
    OpenAIVoiceModelProvider, SingleAgentVoiceWorkflow, STTModelSettings,
    TTSModelSettings, VoicePipeline, VoicePipelineConfig,
)
from openai import AsyncOpenAI

set_tracing_disabled(True)  # the SDK uploads traces to OpenAI by default

voicestudio = AsyncOpenAI(
    base_url="http://localhost:3900/v1",
    api_key=os.environ.get("OMNIVOICE_API_KEY", "not-needed-locally"),
)
# The agent's language model: a local OpenAI-compatible server you choose.
llm = AsyncOpenAI(
    base_url=os.environ["AGENT_LLM_BASE_URL"],  # e.g. Ollama: http://localhost:11434/v1
    api_key=os.environ.get("AGENT_LLM_API_KEY", "not-needed-locally"),
)
agent = Agent(
    name="Assistant",
    instructions="Be brief.",
    model=OpenAIChatCompletionsModel(model=os.environ["AGENT_LLM_MODEL"], openai_client=llm),
)

pipeline = VoicePipeline(
    workflow=SingleAgentVoiceWorkflow(agent),
    stt_model="gpt-4o-transcribe",   # VoiceStudio's active speech-recognition engine
    tts_model="gpt-4o-mini-tts",     # VoiceStudio's active voice engine
    config=VoicePipelineConfig(
        model_provider=OpenAIVoiceModelProvider(openai_client=voicestudio),
        stt_settings=STTModelSettings(language="en"),
        tts_settings=TTSModelSettings(voice="alloy"),  # or a voice-profile id
    ),
)
```

The SDK sends a prose default for `TTSModelSettings.instructions`; engines with
free-text instructions follow it, while OmniVoice ignores it. Set
`instructions="female, whisper"`-style tags to steer OmniVoice.

The agent's **language model** is explicit: set `AGENT_LLM_BASE_URL` and
`AGENT_LLM_MODEL` to a local OpenAI-compatible server (Ollama, LM Studio,
llama.cpp, vLLM). The snippet fails fast when they are unset instead of falling
back to OpenAI's hosted models. **Input mode** stays yours too — use `AudioInput` (a
recorded turn). `StreamedAudioInput` needs OpenAI's Realtime transcription
WebSocket, which VoiceStudio does not implement.

## Remote backend

Running VoiceStudio on a [remote GPU box](remote-gpu.md)? Append `/v1` to that
backend's service-root URL for the OpenAI client's `base_url`, and pass its
`OMNIVOICE_API_KEY` as the `api_key` — the same bearer the rest of the app uses.
Only send the key over https (for example Tailscale Serve); over plain http it
crosses the network in clear text.
Keep the unmodified service root for `/.well-known/voicestudio-speech`
discovery, and keep the backend on your tailnet, not the open internet.

## Use your own voice responsibly

When an agent speaks in a cloned voice, prefer a profile you've marked
**verified own voice** (Settings → a voice profile → Voice ownership). That
consent lock is what gates the heavier agentic features as they land, and
it's the honest default for "an AI is speaking as me."
