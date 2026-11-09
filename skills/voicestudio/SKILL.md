---
name: voicestudio
description: Generate local speech with saved or designed voices, transcribe audio, and automate dubbing or narration workflows through VoiceStudio's REST API or MCP server. Use when the user wants to work with VoiceStudio audio or connect it to an agent or automation.
---

# VoiceStudio

Use the user's running [VoiceStudio](https://github.com/debpalash/VoiceStudio) backend. Prefer local processing; cloud translation, model downloads, remote-backend connections, and external integrations require the user's choice. Installed models can run offline; do not promise that every configured workflow is offline.

## Connect and discover

The default backend is `http://localhost:3900`; honor the user's configured address. Electron's renderer and development proxy are separate services, not the public backend API.

1. Check `GET /health`.
2. Discover the running version's contracts with `GET /openapi.json`, and voices/engines with `GET /v1/audio/voices`. Do not invent profile IDs or infer installed models from a catalog listing.
3. If unavailable, launch the installed app. For an existing source checkout, follow its Electron README (`bun install`, `bun run setup:api`, then `bun run dev` from the repository root). Do not install a second backend or overwrite an existing checkout.
4. Local configurations may permit unauthenticated calls; protected deployments require the configured credentials. Treat 401/403 as authentication failures, not permission to disable auth. Never print tokens or use a placeholder key as if it were a real credential.

## Generate speech

Save this JSON to `speech-request.json`, replacing `voice` with a discovered profile ID when the user selected a voice:

```json
{"model":"tts-1","voice":"alloy","input":"Every voice has a story. Let's tell yours.","response_format":"wav"}
```

```sh
curl --fail-with-body --show-error http://localhost:3900/v1/audio/speech \
  -H 'Content-Type: application/json' --data-binary @speech-request.json \
  --output speech.wav
```

`tts-1` and `tts-1-hd` are aliases, not quality guarantees. OpenAI voice names are compatibility aliases, not those providers' actual voices. Prefer a discovered saved voice for repeatable narration. Check the installed engine's language and voice-design capabilities before promising a result.

Check the HTTP status and decode/probe the output before calling it audio: an error response can be written to the output path. Report the saved path and actual duration/format. Never fabricate a successful generation, transcript, or job completion.

## Transcribe

```sh
curl --fail-with-body --show-error http://localhost:3900/v1/audio/transcriptions \
  -F file=@clip.wav -F model=whisper-1 -F response_format=verbose_json
```

Use `json` or `text` for plain transcripts, `verbose_json` for timestamps, and `srt`/`vtt` for subtitles when supported by the running schema. Check transcription-model readiness before long recordings; explain a missing model and obtain download authorization rather than silently installing it.

## Cloning, design, dubbing, and longer workflows

Use the installed OpenAPI schema to discover profile creation, reference uploads, generation, dubbing, and long-form job operations. Their native endpoints are broader than the OpenAI compatibility API and evolve independently.

- Clone only voices the user is authorized to use. Preserve reference files and existing profiles.
- For design, capture the requested tone, delivery, language, and sample text; check engine support.
- For dubbing, retain original media, speaker assignments, segment timing, and background-audio intent. Translation completion is not rendered-dub completion.
- For asynchronous work, use returned job IDs and documented progress/status endpoints. Inspect terminal errors; avoid blindly resubmitting timed-out generation.
- Return usable files and honest limitations, including missing models or unavailable capabilities.

## MCP and automation

The running backend mounts an MCP endpoint at `http://localhost:3900/mcp`. Use the client's supported HTTP transport and discover its tools at runtime. Reuse an existing connection rather than spawning another service. For stdio-only clients, consult the project's [MCP guide](https://github.com/debpalash/VoiceStudio/blob/main/docs/mcp.md) for its shim.

For n8n, calling agents, containers, or other hosts, make the backend address reachable from that environment: container `localhost` refers to the container. Keep authentication and explicit remote-routing choices intact. An integration-directory listing does not mean the integration is connected.

On errors, read the response body, distinguish unavailable backend, missing model, unsupported capability, authentication, and busy hardware. Fix the reported condition; do not switch to a hosted provider or download a model without authorization.

## Desktop workflows and readiness

The Electron app includes voice cloning and design, saved profiles and gallery,
stories/audiobooks, dubbing projects, transcription, tools, and integrations.
Prefer its existing project and voice identifiers over creating duplicates.
For desktop automation, inspect the actual UI and current Settings shortcuts;
do not assume shortcut bindings or microphone permissions are the same on every host.

Before dictation, verify an installed speech-to-text model and a working input
device. Distinguish recording, paused recording, transcription processing, and
completed text. Copying a transcript and inserting it into another app are different
operations; confirm the intended target before typing and preserve clipboard content
where supported. Never report text delivered just because transcription completed.

For dubbing, inspect missing/failed segments and timing overflow before export.
Preserve music and non-speech audio when requested; do not silently replace the
whole soundtrack. Preserve the source and make the output a separate artifact.
Translation style prompts belong to the selected translation operation, and remote
agent translation requires the user's configured provider choice.

Use the integrations directory as discovery and setup guidance, not proof of a
working connector. Verify actual credentials, endpoint reachability, and supported
operations before describing a connection as ready. Container deployments need
persistent data volumes and a backend URL reachable by the calling client.
