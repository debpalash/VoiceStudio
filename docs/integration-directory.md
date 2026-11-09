# Integration directory

Directory entries are not paid sponsors or endorsements. The app lists only entries with a completed built-in setup block or setup panel. Each carries the **Works with VoiceStudio** badge and capability chips (MCP server, Speech API, Transcription API, Workflow template, Self-hosted, Local language model, Phone calls). Catalog metadata alone never creates a card or a routable detail page. Setup blocks and panels live in one registry keyed by the catalog slug (`electron/src/renderer/src/features/integrations/setup-registry.ts`), so a connector becomes visible only when its wiring is added there. The provider's website opens from the detail page's **Website** card. Icons are bundled locally so viewing the directory sends no logo requests to providers. Brand marks belong to their respective owners. Every visible entry must ship a real brand mark; a test fails if one falls back to the generic placeholder.

The sidebar's Integrations entry opens the directory. Each visible detail page offers the provider's website as a separate action. Featured sponsors without completed integration wiring do not receive placeholder detail pages.

| Company | Official source | Icon source |
|---|---|---|
| Twilio | [Website](https://www.twilio.com) | Bundled site icon |
| n8n | [Website](https://n8n.io) | Bundled site icon |
| GitHub Container Registry | [Website](https://ghcr.io) | Bundled GitHub icon |
| Docker | [Website](https://www.docker.com) | Simple Icons 15.0.0 mark (CC0), Docker blue |
| Model Context Protocol | [Website](https://modelcontextprotocol.io) | Bundled site icon |
| OpenAI Agents | [Guide](https://platform.openai.com/docs/guides/agents) | Simple Icons 15.0.0 OpenAI mark (CC0) on a black tile |
| Claude Code | [Guide](https://docs.anthropic.com/en/docs/claude-code) | Simple Icons 15.0.0 Claude mark (CC0), Claude orange |
| Cursor | [Website](https://cursor.com) | Official site icon (`cursor.com/marketing-static/favicon.svg`) |
| Codex CLI | [Repository](https://github.com/openai/codex) | Simple Icons 15.0.0 OpenAI mark (CC0) on a black tile |
| VoiceStudio API | [Repository](https://github.com/debpalash/VoiceStudio) | VoiceStudio app icon (`voicestudio.svg`, a copy of `electron/public/favicon.svg`) |

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
Visible detail pages add a single **Learn more** link to the integration guide
and link to the provider's website. On wide windows the
setup sits beside a side panel with status, capabilities and the website; on
narrow windows the panel's status comes first and its details follow the setup.

## Call the API or run the container

The **VoiceStudio API** page shows the current backend's OpenAI-compatible base
URL with copyable `curl` and OpenAI Python SDK snippets for
`/v1/audio/speech` and `/v1/audio/transcriptions`. Loopback requests need no
key; remote ones need the backend's API key (see [API authentication](api-auth.md)).

The **Docker** and **GitHub Container Registry** pages give a `docker run` (POSIX shell and Windows PowerShell) and a
Compose snippet for `palashdeb/omnivoice-studio:stable` and
`ghcr.io/debpalash/voicestudio:stable`. `:stable` is the latest tagged
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
disclosure and records nothing unless you turn recording on.
