// Integration directory links, not sponsors, rankings, or connected accounts.
// Capabilities are not declared here: the Electron setup registry
// (electron/src/renderer/src/features/integrations/setup-registry.ts) is the
// only place that claims what an entry actually does with VoiceStudio.
// Official icon sources and verification links: docs/integration-directory.md.
import genericLogo from '../assets/integrations/integration-generic.svg';
import twilioLogo from '../assets/integrations/twilio.png';
import plivoLogo from '../assets/integrations/plivo.svg';
import telnyxLogo from '../assets/integrations/telnyx.ico';
import n8nLogo from '../assets/integrations/n8n.ico';
import zapierLogo from '../assets/integrations/zapier.ico';
import githubLogo from '../assets/integrations/github.png';
import dockerLogo from '../assets/integrations/docker.svg';
import mcpLogo from '../assets/integrations/mcp.png';
import claudeLogo from '../assets/integrations/claude.svg';
import cursorLogo from '../assets/integrations/cursor.svg';
import openaiLogo from '../assets/integrations/openai.svg';
import voiceStudioLogo from '../assets/integrations/voicestudio.svg';

export const VOICE_AI_DIRECTORY = [
  {
    name: 'Twilio',
    url: 'https://www.twilio.com',
    logoUrl: twilioLogo,
  },
  {
    name: 'Plivo',
    url: 'https://www.plivo.com',
    logoUrl: plivoLogo,
  },
  {
    name: 'Telnyx',
    url: 'https://telnyx.com',
    logoUrl: telnyxLogo,
  },
  {
    name: 'n8n',
    url: 'https://n8n.io',
    logoUrl: n8nLogo,
  },
  { name: 'Zapier', url: 'https://zapier.com', logoUrl: zapierLogo },
  { name: 'Make', url: 'https://www.make.com', logoUrl: genericLogo },
  {
    name: 'GitHub',
    url: 'https://github.com',
    logoUrl: githubLogo,
  },
  {
    name: 'GitHub Container Registry',
    url: 'https://ghcr.io',
    logoUrl: githubLogo,
  },
  {
    name: 'Docker',
    url: 'https://www.docker.com',
    logoUrl: dockerLogo,
  },
  {
    name: 'Model Context Protocol',
    url: 'https://modelcontextprotocol.io',
    logoUrl: mcpLogo,
  },
  {
    name: 'OpenAI Agents',
    url: 'https://platform.openai.com/docs/guides/agents',
    logoUrl: openaiLogo,
  },
  {
    name: 'Claude Code',
    url: 'https://docs.anthropic.com/en/docs/claude-code',
    logoUrl: claudeLogo,
  },
  {
    name: 'Cursor',
    url: 'https://www.cursor.com',
    logoUrl: cursorLogo,
  },
  {
    name: 'Codex CLI',
    url: 'https://github.com/openai/codex',
    logoUrl: openaiLogo,
  },
  {
    name: 'VoiceStudio API',
    url: 'https://github.com/debpalash/VoiceStudio',
    logoUrl: voiceStudioLogo,
  },
];
