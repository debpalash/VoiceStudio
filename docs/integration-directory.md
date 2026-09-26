# Integration directory

Directory entries are not paid sponsors or endorsements. Only entries with a built-in setup block or setup panel carry the **Works with VoiceStudio** badge and capability chips (MCP server, Speech API, Transcription API, Workflow template, Self-hosted, Local language model, Phone calls); every other card is marked **External link**. Every card and featured logo opens the integration's page in VoiceStudio; the provider's website opens only from that page's **Website** card. Setup blocks and panels live in one registry keyed by the catalog slug (`electron/src/renderer/src/features/integrations/setup-registry.ts`), so a connector is added in one place. Icons are bundled locally so viewing the catalog sends no logo requests to providers. Brand marks belong to their respective owners.

The compact footer’s Integrations button opens the directory. Each detail page offers the provider's website as a separate action; featured sponsors also have a detail page when they are not in the directory.

| Company | Official source | Icon source |
|---|---|---|
| Twilio | [Website](https://www.twilio.com) | Bundled site icon |
| Plivo | [Website](https://www.plivo.com) | Bundled site icon |
| Telnyx | [Website](https://telnyx.com) | Bundled site icon |
| n8n | [Website](https://n8n.io) | Bundled site icon |
| Zapier | [Website](https://zapier.com) | Bundled site icon |
| Make | [Website](https://www.make.com) | Bundled generic mark |
| GitHub | [Website](https://github.com) | Bundled site icon |
| GitHub Container Registry | [Website](https://ghcr.io) | Bundled GitHub icon |
| Docker | [Website](https://www.docker.com) | Bundled site icon |
| Model Context Protocol | [Website](https://modelcontextprotocol.io) | Bundled site icon |
| OpenAI Agents | [Guide](https://platform.openai.com/docs/guides/agents) | Bundled local mark |
| Claude Code | [Guide](https://docs.anthropic.com/en/docs/claude-code) | Bundled site icon |
| Codex CLI | [Repository](https://github.com/openai/codex) | Bundled local mark |
| VoiceStudio API | [Repository](https://github.com/debpalash/VoiceStudio) | Bundled local mark |

## Connect coding agents

The Claude Code, Cursor and Codex CLI detail pages include a copyable MCP
configuration for VoiceStudio's current backend address and port. Merge the
entry into `.mcp.json` (Claude Code), `.cursor/mcp.json` (Cursor) or the
`[mcp_servers.voicestudio]` table into `~/.codex/config.toml` (Codex CLI),
preserving your other servers. Keep VoiceStudio running, then enable the
server in your client. Use Settings → Sharing → MCP voice bindings to bind
`claude-code`, `cursor` or `codex-cli` to a saved voice.

The **Model Context Protocol** page gives the generic Streamable HTTP URL and
client-ID header for any other MCP client, plus a stdio configuration for the
bundled `python -m backend.mcp_shim` proxy (needs a source checkout; see
[MCP](mcp.md)).

These configurations use Streamable HTTP and the client-ID header; they do not
install another backend or read/write agent configuration files. Exported
configurations never contain a key: for a remote **https** backend they reference
`OMNIVOICE_API_KEY` from your environment (`${OMNIVOICE_API_KEY}` in Claude Code,
`${env:OMNIVOICE_API_KEY}` in Cursor, `bearer_token_env_var` in Codex, and
`$OMNIVOICE_API_KEY` in the `curl`/Python snippets). A remote plain-http backend
gets no key at all, because it would cross the network in clear text; put it
behind https first (see [API authentication](api-auth.md)). Copying configuration does not prove the client
is connected; use its MCP tools/status view to confirm the connection.

The schemas follow the official [Claude Code MCP guide](https://code.claude.com/docs/en/mcp),
[Cursor MCP guide](https://cursor.com/docs/mcp) and
[Codex MCP guide](https://developers.openai.com/codex/mcp). The catalog has one
card per route, retaining bundled logos and the correct category when entries
overlap.

Each detail page leads with the integration's category and a one-line summary.
Pages for integrations that work with VoiceStudio add a single **Learn more**
link to their guide; external entries link only to the provider's website. On wide windows the
setup sits beside a side panel with status, capabilities and the website; on
narrow windows the panel's status comes first and its details follow the setup.

## Call the API or run the container

The **VoiceStudio API** page shows the current backend's OpenAI-compatible base
URL with copyable `curl` and OpenAI Python SDK snippets for
`/v1/audio/speech` and `/v1/audio/transcriptions`. Loopback requests need no
key; remote ones need the backend's API key (see [API authentication](api-auth.md)).

The **Docker** and **GitHub Container Registry** pages give a `docker run` (POSIX shell and Windows PowerShell) and a
Compose snippet for `palashdeb/omnivoice-studio:stable` and
`ghcr.io/debpalash/omnivoice-studio:stable`. `:stable` is the latest tagged
release; `:latest` is the rolling preview built from `main`. See the
[Docker install guide](install/docker.md) for GPU flags and the ROCm tags.

## Automate speech with n8n

The n8n detail page exports an inactive, manual workflow that calls the current
backend's OpenAI-compatible speech endpoint and returns WAV audio. Edit the text
and voice in n8n, then run it yourself. See [n8n setup](integrations/n8n.md) for
container networking, credentials and validation. No credentials or automatic
background requests are exported.

## Voice for OpenAI Agents

The OpenAI Agents detail page shows a copyable Python snippet that points the
OpenAI Agents SDK voice pipeline at the current backend's OpenAI-compatible API
(`<backend>/v1`) for both speech recognition and speech. For a remote https
backend the API key is read from `OMNIVOICE_API_KEY` when the script runs and is
never written into the snippet; loopback needs no key, and a remote plain-http
backend is never given one (put it behind https first); tracing is switched off so nothing is uploaded. The agent's language
model must be set explicitly (`AGENT_LLM_BASE_URL`, `AGENT_LLM_MODEL`, for a
local OpenAI-compatible server); the snippet never falls back to a hosted model. See
[Agentic voice → OpenAI Agents SDK](agentic-voice.md#openai-agents-sdk).

## Answer phone calls with Twilio

The Twilio detail page is a guided setup (account, tunnel, phone number, voice)
with a live readiness checklist; it answers calls to your Twilio number with a
saved voice: VoiceStudio speaks a greeting over a Twilio Media Stream, then
hangs up. It is off by default. When enabled, a separate loopback listener that your own HTTPS
tunnel (cloudflared, ngrok) forwards to serves only Twilio's endpoints: the
voice and status webhooks, which must carry a valid Twilio signature, and the
Media Stream, which must present a single-use per-call token. The main API is
never exposed. **Play phone-quality preview** plays the greeting as a caller
hears it, without Twilio. See [Twilio setup](integrations/twilio.md) for the
tunnel, Twilio Console configuration, security model and limits.

The [call agent](integrations/calls.md) uses the same setup to place a call from
your request (for example, booking a table) or answer one, and holds the
conversation in your verified or designed voice. It opens with an editable AI
disclosure and records nothing unless you turn recording on. Twilio is an
implemented connector; the other calling entries (Plivo, Telnyx) remain
capability references.
