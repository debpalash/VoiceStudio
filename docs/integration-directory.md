# Integration directory

Directory entries are illustrative, not paid sponsors, endorsements, or verified VoiceStudio integrations. Icons are bundled locally so viewing the catalog sends no logo requests to providers. Brand marks belong to their respective owners.

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

The Claude Code and Cursor detail pages include a copyable MCP configuration for
VoiceStudio's current backend address and port. Merge the entry into `.mcp.json`
(Claude Code) or `.cursor/mcp.json` (Cursor), preserving your other servers. Keep
VoiceStudio running, then enable the server in your client. Use Settings → Sharing
→ MCP voice bindings to bind `claude-code` or `cursor` to a saved voice.

These configurations use Streamable HTTP and the client-ID header; they do not
install another backend or read/write agent configuration files. For a remote
backend, configure its required credentials in the client. Exported configurations
never contain stored credentials. Copying configuration does not prove the client
is connected; use its MCP tools/status view to confirm the connection.

The schemas follow the official [Claude Code MCP guide](https://code.claude.com/docs/en/mcp)
and [Cursor MCP guide](https://cursor.com/docs/mcp). Other than the n8n workflow and
OpenAI Agents snippet described below, remaining directory entries are
capability references, not implemented connectors. The catalog has one card per
route, retaining bundled logos and the correct category when entries overlap.

## Automate speech with n8n

The n8n detail page exports an inactive, manual workflow that calls the current
backend's OpenAI-compatible speech endpoint and returns WAV audio. Edit the text
and voice in n8n, then run it yourself. See [n8n setup](integrations/n8n.md) for
container networking, credentials and validation. No credentials or automatic
background requests are exported.

## Voice for OpenAI Agents

The OpenAI Agents detail page shows a copyable Python snippet that points the
OpenAI Agents SDK voice pipeline at the current backend's OpenAI-compatible API
(`<backend>/v1`) for both speech recognition and speech. The API key is read
from `OMNIVOICE_API_KEY` when the script runs and is never written into the
snippet; tracing is switched off so nothing is uploaded. The agent's language
model must be set explicitly (`AGENT_LLM_BASE_URL`, `AGENT_LLM_MODEL`, for a
local OpenAI-compatible server); the snippet never falls back to a hosted model. See
[Agentic voice → OpenAI Agents SDK](agentic-voice.md#openai-agents-sdk).
