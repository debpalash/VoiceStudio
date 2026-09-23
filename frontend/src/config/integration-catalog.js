// Local-first integration directory. These are links and capability references,
// not bundled SDKs, endorsements, or connected accounts.
import genericLogo from '../assets/integrations/integration-generic.svg';
import { VOICE_AI_DIRECTORY } from './voice-ai-directory';

const groups = [
  [
    'automation',
    [
      'n8n|https://n8n.io',
      'Zapier|https://zapier.com',
      'Make|https://www.make.com',
      'Pipedream|https://pipedream.com',
      'IFTTT|https://ifttt.com',
      'Activepieces|https://www.activepieces.com',
      'Windmill|https://www.windmill.dev',
      'Temporal|https://temporal.io',
      'Prefect|https://www.prefect.io',
      'Dagster|https://dagster.io',
      'Airflow|https://airflow.apache.org',
      'Node-RED|https://nodered.org',
      'Huginn|https://github.com/huginn/huginn',
      'Trigger.dev|https://trigger.dev',
      'Inngest|https://www.inngest.com',
      'Pipedream Connect|https://pipedream.com/connect',
      'Workato|https://www.workato.com',
      'Tray.ai|https://tray.ai',
      'Retool Workflows|https://retool.com/workflows',
      'Bardeen|https://www.bardeen.ai',
    ],
  ],
  [
    'comms',
    [
      'Twilio|https://www.twilio.com',
      'Plivo|https://www.plivo.com',
      'Telnyx|https://telnyx.com',
      'SignalWire|https://signalwire.com',
      'Vonage|https://www.vonage.com/communications-apis',
      'Bandwidth|https://www.bandwidth.com',
      'Sinch|https://sinch.com',
      'MessageBird|https://bird.com',
      'Agora|https://www.agora.io',
      'Daily|https://www.daily.co',
      'LiveKit|https://livekit.io',
      'Stream|https://getstream.io',
      'Retell AI|https://www.retellai.com',
      'Vapi|https://vapi.ai',
      'Bland AI|https://www.bland.ai',
      'ElevenLabs Conversational AI|https://elevenlabs.io/conversational-ai',
      'Deepgram Voice Agent|https://deepgram.com/voice-agents',
      'SIP.js|https://sipjs.com',
      'Asterisk|https://www.asterisk.org',
      'FreeSWITCH|https://signalwire.com/freeswitch',
    ],
  ],
  [
    'agents',
    [
      'OpenAI Agents|https://platform.openai.com/docs/guides/agents',
      'Anthropic Claude|https://www.anthropic.com',
      'Claude Code|https://docs.anthropic.com/en/docs/claude-code',
      'Codex CLI|https://github.com/openai/codex',
      'GitHub Copilot|https://github.com/features/copilot',
      'Cursor|https://www.cursor.com',
      'Windsurf|https://windsurf.com',
      'Cline|https://github.com/cline/cline',
      'Roo Code|https://github.com/RooCodeInc/Roo-Code',
      'Aider|https://aider.chat',
      'OpenHands|https://github.com/All-Hands-AI/OpenHands',
      'Goose|https://block.github.io/goose',
      'OpenCode|https://opencode.ai',
      'Continue|https://www.continue.dev',
      'Amazon Q Developer|https://aws.amazon.com/q/developer',
      'Google Jules|https://jules.google',
      'Devin|https://devin.ai',
      'Replit Agent|https://replit.com/ai',
      'Warp AI|https://www.warp.dev/ai',
      'Pi|https://pi.dev',
    ],
  ],
  [
    'mcp',
    [
      'Model Context Protocol|https://modelcontextprotocol.io',
      'MCP Registry|https://registry.modelcontextprotocol.io',
      'GitHub MCP Server|https://github.com/github/github-mcp-server',
      'Filesystem MCP|https://github.com/modelcontextprotocol/servers',
      'Postgres MCP|https://github.com/modelcontextprotocol/servers',
      'Brave Search MCP|https://github.com/modelcontextprotocol/servers',
      'Slack MCP|https://github.com/modelcontextprotocol/servers',
      'Notion MCP|https://github.com/makenotion/notion-mcp-server',
      'Linear MCP|https://github.com/jerhadf/linear-mcp-server',
      'Figma MCP|https://github.com/GLips/Figma-Context-MCP',
      'Playwright MCP|https://github.com/microsoft/playwright-mcp',
      'Browserbase MCP|https://github.com/browserbase/mcp-server-browserbase',
      'Sentry MCP|https://github.com/getsentry/sentry-mcp',
      'Cloudflare MCP|https://github.com/cloudflare/mcp-server-cloudflare',
      'AWS MCP|https://github.com/awslabs/mcp',
      'Google Cloud MCP|https://github.com/GoogleCloudPlatform/mcp',
      'Docker MCP Catalog|https://www.docker.com/products/mcp-catalog',
      'MCPJam|https://www.mcpjam.com',
      'Smithery|https://smithery.ai',
      'PulseMCP|https://www.pulsemcp.com',
    ],
  ],
  [
    'developer',
    [
      'GitHub|https://github.com',
      'GitLab|https://gitlab.com',
      'Bitbucket|https://bitbucket.org',
      'GitHub Actions|https://github.com/features/actions',
      'GitHub Container Registry|https://ghcr.io',
      'Docker|https://www.docker.com',
      'Kubernetes|https://kubernetes.io',
      'Vercel|https://vercel.com',
      'Netlify|https://www.netlify.com',
      'Cloudflare Workers|https://workers.cloudflare.com',
      'Fly.io|https://fly.io',
      'Railway|https://railway.app',
      'Render|https://render.com',
      'Supabase|https://supabase.com',
      'Neon|https://neon.tech',
      'Turso|https://turso.tech',
      'PlanetScale|https://planetscale.com',
      'Sentry|https://sentry.io',
      'PostHog|https://posthog.com',
      'Grafana|https://grafana.com',
    ],
  ],
  [
    'data',
    [
      'OpenAI API|https://platform.openai.com',
      'Hugging Face|https://huggingface.co',
      'Ollama|https://ollama.com',
      'LM Studio|https://lmstudio.ai',
      'vLLM|https://vllm.ai',
      'llama.cpp|https://github.com/ggml-org/llama.cpp',
      'Replicate|https://replicate.com',
      'Together AI|https://www.together.ai',
      'Groq|https://groq.com',
      'Cerebras|https://www.cerebras.ai',
      'Fireworks AI|https://fireworks.ai',
      'Cohere|https://cohere.com',
      'Mistral AI|https://mistral.ai',
      'Google Gemini API|https://ai.google.dev',
      'Perplexity API|https://www.perplexity.ai',
      'AssemblyAI|https://www.assemblyai.com',
      'Deepgram|https://deepgram.com',
      'Qdrant|https://qdrant.tech',
      'Weaviate|https://weaviate.io',
      'Pinecone|https://www.pinecone.io',
    ],
  ],
  [
    'productivity',
    [
      'Slack|https://slack.com',
      'Discord|https://discord.com',
      'Microsoft Teams|https://www.microsoft.com/microsoft-teams',
      'Telegram|https://telegram.org',
      'Notion|https://www.notion.so',
      'Linear|https://linear.app',
      'Jira|https://www.atlassian.com/software/jira',
      'Trello|https://trello.com',
      'Asana|https://asana.com',
      'ClickUp|https://clickup.com',
      'Google Drive|https://drive.google.com',
      'Dropbox|https://www.dropbox.com',
      'OneDrive|https://www.microsoft.com/microsoft-365/onedrive',
      'Gmail|https://gmail.com',
      'Google Calendar|https://calendar.google.com',
      'Microsoft Outlook|https://outlook.live.com',
      'Airtable|https://airtable.com',
      'HubSpot|https://www.hubspot.com',
      'Salesforce|https://www.salesforce.com',
      'Intercom|https://www.intercom.com',
    ],
  ],
];

const catalogEntries = [
  ...VOICE_AI_DIRECTORY.map((entry) => ({ ...entry, category: 'comms', featured: false })),
  ...groups.flatMap(([category, items]) =>
    items.map((item) => {
      const [name, url] = item.split('|');
      return {
        name,
        url,
        logoUrl: genericLogo,
        category,
        featured: false,
      };
    }),
  ),
];

export function integrationSlug(name) {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
}

export function getIntegrationBySlug(slug) {
  return INTEGRATION_CATALOG.find((entry) => integrationSlug(entry.name) === slug);
}

// Curated rows supply bundled logos; category groups supply taxonomy. Reconcile
// them by the public route instead of rendering duplicate cards and React keys.
const catalogBySlug = new Map();
for (const entry of catalogEntries) {
  const slug = integrationSlug(entry.name);
  const previous = catalogBySlug.get(slug);
  catalogBySlug.set(slug, { ...entry, logoUrl: previous?.logoUrl ?? entry.logoUrl });
}
export const INTEGRATION_CATALOG = [...catalogBySlug.values()];
