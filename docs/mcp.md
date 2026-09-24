# MCP server — let agents speak in your voice

VoiceStudio ships an [MCP](https://modelcontextprotocol.io/) server so AI agents
(Claude Code, Cursor, …) can synthesize speech, clone voices, transcribe audio,
and list your voices — locally, in a voice you choose per agent. The server is
**mounted on the running backend** at `/mcp`, so there's nothing extra to
start once VoiceStudio is open.

## Tools

| Tool | What it does |
|---|---|
| `generate_speech` | text → WAV by default. Uses the agent's bound voice unless a `profile_id` is passed. In `files`/`both` mode, pass `format="opus"` or `format="ogg"` for Ogg/Opus audio at both `audio_url` and `output_path`; requires an installed ffmpeg. |
| `clone_voice` | reference audio (base64, or a `ref_audio_path` under the base path) → new voice profile. Returns a `profile_id` for use with `generate_speech`. |
| `transcribe` | audio (base64, or an `audio_path` under the base path) → text (646 languages). |
| `list_voices` / `list_personalities` / `list_languages` | enumerate what's available. |
| `check_health` | backend status + active GPU device. |

## Output mode and file inputs

An LLM agent pays for every byte it receives in context, and a WAV as base64
is a lot of bytes. Two environment variables move the audio out of the
conversation and onto disk, where an agent can hand it to a player or another
tool by path:

| Variable | Values | Effect |
|---|---|---|
| `OMNIVOICE_MCP_OUTPUT_MODE` | `resources` (default) · `files` · `both` | `resources` returns `wav_base64` inline (the original contract). `files` returns `audio_url` (the render served at `/audio/<audio_id>.<format>`) and, when a base path is set, `output_path` — the requested format written into that directory. `both` returns everything (the inline data is always WAV). |
| `OMNIVOICE_MCP_TIMEOUT_S` | seconds (default: follows the backend) | How long a tool waits on the backend. Unset, each tool waits as long as the backend itself would, plus 30 s, and never less than 120 s. `transcribe` follows `OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S` (300 s by default, queue time included). `generate_speech` follows the backend's queue wait (`OMNIVOICE_GPU_QUEUE_TIMEOUT_S`, 1,800 s by default) plus the larger of `OMNIVOICE_GENERATE_TIMEOUT_S` and `OMNIVOICE_CPU_GENERATE_TIMEOUT_S`, plus 1 s per 40 characters past 1,200. The agent then gets the backend's own timeout error instead of an empty one. Set this to use one fixed wait for every tool. On a machine that hasn't downloaded the OmniVoice model yet, the first `generate_speech` also downloads it (about 2.3 GB) inside the backend's budget: install the model first (first-run setup, or Model Catalogue) or raise `OMNIVOICE_GENERATE_TIMEOUT_S` before the first call. |
| `OMNIVOICE_MCP_BASE_PATH` | a directory | The **security boundary** for file-shaped traffic. `transcribe(audio_path=…)` and `clone_voice(ref_audio_path=…)` read only from inside it (relative paths resolve against it, absolute paths must already lie within it, symlinks are resolved before the check), and files mode writes only into it. With no base path configured, path arguments are refused with a reason. |

`generate_speech(format="wav")` remains the default; `format="ogg"` and
`format="opus"` both encode **Opus inside Ogg**, with `.ogg` and `.opus`
extensions respectively and `audio/ogg` HTTP media type. The backend converts
the saved WAV on demand for the URL, even if no base path is configured;
repeated URL fetches reuse a bounded in-memory Opus cache rather than loading
and transcoding the WAV again. Changing the WAV invalidates that cache;
deleting it makes the URL return 404.
With a base path the MCP process also transcodes the file. These compressed
formats require ffmpeg on the backend (and on the MCP host if it writes a file).
The tool checks the backend URL before reporting success; missing encoders or
conversion failures return an error instead of labelling WAV bytes as Opus.
`format` must be one of `wav`, `ogg`, `opus` and compressed output requires
`files` or `both` mode. In `both`, `wav_base64` is the original WAV even if
the URL and file are Opus.

Input files are opened through confined, no-follow descriptors after path
validation, so replacing a checked file or parent directory cannot redirect a
read outside the base path.

Set them on the **backend's** environment for the mounted `/mcp` endpoint
(the launcher, a service file, Docker `-e`), or on the server entry's `env`
when running `python -m backend.mcp_server` standalone. The base path must be
visible to both the backend and the agent. If they run in different containers
or filesystem namespaces, mount one shared directory at the same path in both;
`output_path` is reported in the backend's namespace. The agent's working
directory is suitable only when that shared mount exists. With this setup,
`OMNIVOICE_MCP_OUTPUT_MODE=files` keeps every render out of agent context while
still returning a path the agent can use.

## Connecting

### Streamable HTTP (modern clients)

Point your client at the mounted endpoint:

```
http://localhost:3900/mcp/
```

Keep the trailing slash: `/mcp/` works on every VoiceStudio version. Current
backends also answer bare `/mcp` directly (every Streamable HTTP method —
`POST`, `GET` for the event stream, `DELETE` — with no redirect, whether or not
the backend also serves the web UI); older Docker and source builds returned
HTTP 405 for bare `/mcp`, so existing configs using it work once you update. Use the backend's real port if you moved it with
`OMNIVOICE_PORT`.

The desktop app exports ready-made client configurations for the current
backend address, all using `/mcp/`, under **Integrations**: Claude Code (`.mcp.json`), Cursor
(`.cursor/mcp.json`), Codex CLI (`~/.codex/config.toml`), and a generic
Streamable HTTP + stdio card under **Model Context Protocol**. For Codex CLI
the exported table is:

```toml
[mcp_servers.voicestudio]
url = "http://127.0.0.1:3900/mcp/"
http_headers = { "X-OmniVoice-Client-Id" = "codex-cli" }
```

This follows the [Codex MCP configuration](https://developers.openai.com/codex/mcp):
a `url` key selects Streamable HTTP and `http_headers` adds static headers.

To bind this agent to a specific voice, send an
`X-OmniVoice-Client-Id` header (e.g. `claude-code`). See
[per-agent voices](#per-agent-voices).

**Agents in Docker or on another machine:** the MCP SDK rejects non-localhost
Host headers by default (DNS-rebinding guard). Set
`OMNIVOICE_MCP_ALLOWED_HOSTS` to a comma-separated list of host patterns the
agent connects from (e.g. `host.containers.internal:*,192.168.1.50:*`).
Keep this on a trusted LAN or behind TLS (Tailscale Serve, a reverse proxy
with HTTPS) — the MCP transport is not authenticated, so don't expose it on
the open internet.

### stdio (clients that only speak stdio)

Use the bundled shim — it proxies stdio ↔ the mounted HTTP endpoint. Drop
this into your client's MCP config (`docs/mcp.json` is a template):

```json
{
  "mcpServers": {
    "omnivoice": {
      "command": "python",
      "args": ["-m", "backend.mcp_shim"],
      "cwd": "/path/to/VoiceStudio",
      "env": { "OMNIVOICE_PORT": "3900", "OMNIVOICE_CLIENT_ID": "claude-code" }
    }
  }
}
```

For a backend elsewhere, set `OMNIVOICE_HOST` (and `OMNIVOICE_PORT`), or
`OMNIVOICE_URL` with the full base URL when it uses https or a reverse-proxy
path prefix (e.g. `https://gpu-box/voicestudio`). Set `OMNIVOICE_API_KEY` when
that backend requires an [API key](api-auth.md); the shim sends it as a Bearer
token, and refuses to start if that would send it over plain http to another
host. The shim
needs a VoiceStudio source checkout (it runs with that checkout's Python
environment, e.g. `uv run python -m backend.mcp_shim`).

The shim forwards `OMNIVOICE_CLIENT_ID` as the `X-OmniVoice-Client-Id` header,
so the per-agent voice binding works the same as the HTTP path. It waits for
the backend to be up, relays JSON-RPC, and exits cleanly when the client
closes.

## Per-agent voices

Each agent identifies itself with a **client id**. Bind a client id to a voice
profile so different agents speak differently — "Claude Code in Morgan, Cursor
in Scarlett". Voice resolution precedence on every `generate_speech` call:

1. an explicit `profile_id` argument, else
2. the calling agent's binding, else
3. the global default voice, else
4. VoiceStudio's default voice.

Manage bindings over the loopback REST API (the Settings UI uses these):

```bash
# list
curl localhost:3900/api/mcp/bindings
# bind claude-code → a voice profile
curl -X PUT localhost:3900/api/mcp/bindings \
  -H 'Content-Type: application/json' \
  -d '{"client_id":"claude-code","label":"Claude Code","profile_id":"<voice-profile-id>"}'
# remove
curl -X DELETE localhost:3900/api/mcp/bindings/claude-code
```

Prefer a [consent-verified](../docs/competitive-analysis.md) voice profile for
any agent that speaks as you.

## How tools reach the backend

The mounted `/mcp` tools call VoiceStudio's API in-process, as a local
caller: they work whatever host and port the backend binds, and the share PIN
and API key never block them. A standalone `python -m backend.mcp_server`
calls the backend over HTTP at the address it binds (`OMNIVOICE_BIND_HOST`,
`OMNIVOICE_PORT`). Set `OMNIVOICE_API_URL` only to send tool calls somewhere
else, such as a reverse proxy.

## Disabling

Set `OMNIVOICE_MCP_DISABLE=1` to skip mounting `/mcp` entirely.
