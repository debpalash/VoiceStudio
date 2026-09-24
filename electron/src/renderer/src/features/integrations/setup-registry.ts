import {
  backendEndpoint,
  MCP_CLIENT_ID_HEADER,
  MCP_CLIENTS,
  MCP_ENDPOINT,
  mcpSetup,
  remoteAuth,
} from './mcp-setup';
import { n8nSetup } from './n8n-setup';
import { openaiAgentsSetup } from './openai-agents-setup';
import type { ComponentType, ReactNode } from 'react';
import { TwilioSetup, TWILIO_DOCS } from './twilio-setup';

/**
 * Directory entries VoiceStudio actually works with, keyed by catalog slug.
 *
 * An entry here is the only thing that earns a card the "Works with
 * VoiceStudio" badge and capability chips; every other catalog entry is an
 * external link. Add a connector by adding one entry: its capabilities and
 * its copyable setup blocks for the current backend address.
 */
export type IntegrationCapability =
  | 'mcp'
  | 'speechApi'
  | 'transcriptionApi'
  | 'workflow'
  | 'selfHost'
  | 'localLlm'
  | 'phoneCalls';

export interface SetupBlock {
  id: string;
  /** i18n key for the block heading. */
  titleKey: string;
  /** i18n key (and interpolation values) for the block's instructions. */
  hintKey?: string;
  hintValues?: Record<string, string>;
  /** Code-block language, shown to screen readers and used as a CSS hook. */
  language: 'json' | 'toml' | 'shell' | 'powershell' | 'python' | 'yaml' | 'text';
  text: string;
  /** Offer "Save as…" with this file name (and MIME type). */
  download?: { file: string; type: string };
}

/** What an interactive panel receives to lay out a whole detail page. */
export interface IntegrationPanelProps {
  /** The page hero (logo, category, name, tagline) with the panel's status and primary action. */
  hero: (slots: { status?: ReactNode; action?: ReactNode }) => ReactNode;
  /** The page's generic rail cards (capabilities, website), placed below the panel's own. */
  rail: ReactNode;
}

export interface IntegrationSetup {
  capabilities: readonly IntegrationCapability[];
  /** i18n key for the one-line hero subtitle: what this integration does for the user. */
  taglineKey: string;
  docs: string;
  /** Link to Settings → Sharing for per-client MCP voice bindings. */
  voiceBindings?: boolean;
  /** Blocks for this backend, or null when the backend URL is unusable for export. */
  blocks: (baseUrl: string) => SetupBlock[] | null;
  /**
   * An interactive setup panel for connectors that are configured and run
   * inside VoiceStudio rather than exported as snippets (e.g. Twilio). An
   * entry needs copyable blocks, a panel, or both.
   */
  panel?: ComponentType<IntegrationPanelProps>;
}

const REPO_DOCS = 'https://github.com/debpalash/VoiceStudio/blob/main/docs';
const DOCKER_HUB_IMAGE = 'palashdeb/omnivoice-studio';
const GHCR_IMAGE = 'ghcr.io/debpalash/omnivoice-studio';

function mcpClient(slug: keyof typeof MCP_CLIENTS): IntegrationSetup {
  return {
    capabilities: ['mcp'],
    taglineKey: 'integrationCatalog.tagline.mcpClient',
    docs: MCP_CLIENTS[slug].docs,
    voiceBindings: true,
    blocks: (baseUrl) => {
      const setup = mcpSetup(slug, baseUrl);
      if (!setup) return null;
      return [
        {
          id: 'config',
          titleKey: 'integrationCatalog.block.clientConfig',
          hintKey: 'integrationCatalog.setupHint',
          hintValues: { file: setup.file },
          language: setup.format,
          text: setup.text,
        },
      ];
    },
  };
}

function dockerRun(image: string) {
  return [
    'export OMNIVOICE_API_KEY="$(python3 -c \'import secrets; print(secrets.token_urlsafe(32))\')"',
    '',
    'docker run -d --name omnivoice \\',
    '  -p 127.0.0.1:3900:3900 \\',
    '  -e OMNIVOICE_API_KEY="$OMNIVOICE_API_KEY" \\',
    '  -v omnivoice-data:/app/omnivoice_data \\',
    '  -v ~/.cache/huggingface:/root/.cache/huggingface \\',
    `  ${image}:stable`,
    '',
  ].join('\n');
}

/** Windows PowerShell (5.1 and 7): backtick continuations, a CSPRNG key. */
function dockerRunPowerShell(image: string) {
  return [
    '$bytes = New-Object byte[] 32',
    '[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)',
    "$env:OMNIVOICE_API_KEY = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')",
    '',
    'docker run -d --name omnivoice `',
    '  -p 127.0.0.1:3900:3900 `',
    '  -e OMNIVOICE_API_KEY="$env:OMNIVOICE_API_KEY" `',
    '  -v omnivoice-data:/app/omnivoice_data `',
    '  -v "${HOME}/.cache/huggingface:/root/.cache/huggingface" `',
    `  ${image}:stable`,
    '',
  ].join('\n');
}

function dockerCompose(image: string) {
  return [
    'services:',
    '  omnivoice:',
    `    image: ${image}:stable`,
    '    ports:',
    '      - "127.0.0.1:3900:3900"',
    '    environment:',
    '      - OMNIVOICE_BIND_HOST=0.0.0.0',
    '      - OMNIVOICE_API_KEY=${OMNIVOICE_API_KEY:?set a long random key}',
    '      - OMNIVOICE_DATA_DIR=/app/omnivoice_data',
    '      - HF_HOME=/app/omnivoice_data/huggingface',
    '    volumes:',
    '      - omnivoice-data:/app/omnivoice_data',
    '    restart: unless-stopped',
    'volumes:',
    '  omnivoice-data:',
    '',
  ].join('\n');
}

function container(image: string, docs: string): IntegrationSetup {
  return {
    capabilities: ['selfHost', 'speechApi', 'transcriptionApi', 'mcp'],
    taglineKey: 'integrationCatalog.tagline.container',
    docs,
    blocks: () => [
      {
        id: 'run',
        titleKey: 'integrationCatalog.block.dockerRun',
        hintKey: 'integrationCatalog.dockerHint',
        language: 'shell',
        text: dockerRun(image),
      },
      {
        id: 'run-powershell',
        titleKey: 'integrationCatalog.block.dockerRunPowerShell',
        language: 'powershell',
        text: dockerRunPowerShell(image),
      },
      {
        id: 'compose',
        titleKey: 'integrationCatalog.block.dockerCompose',
        hintKey: 'integrationCatalog.dockerGpuHint',
        language: 'yaml',
        text: dockerCompose(image),
      },
    ],
  };
}

export const INTEGRATION_SETUPS: Record<string, IntegrationSetup> = {
  'claude-code': mcpClient('claude-code'),
  cursor: mcpClient('cursor'),
  'codex-cli': mcpClient('codex-cli'),
  'model-context-protocol': {
    capabilities: ['mcp'],
    taglineKey: 'integrationCatalog.tagline.mcp',
    docs: `${REPO_DOCS}/mcp.md`,
    voiceBindings: true,
    blocks: (baseUrl) => {
      const url = backendEndpoint(baseUrl, MCP_ENDPOINT);
      const base = backendEndpoint(baseUrl, '');
      if (!url || !base) return null;
      const bearer = remoteAuth(baseUrl) === 'bearer';
      return [
        {
          id: 'http',
          titleKey: 'integrationCatalog.block.streamableHttp',
          hintKey: 'integrationCatalog.mcpHttpHint',
          hintValues: { header: MCP_CLIENT_ID_HEADER },
          language: 'text',
          text: [
            `URL: ${url}`,
            'Transport: Streamable HTTP',
            `Header: ${MCP_CLIENT_ID_HEADER}: <your-client-id>`,
            // Remote https: the key comes from the client's environment.
            ...(bearer ? ['Header: Authorization: Bearer $OMNIVOICE_API_KEY'] : []),
            '',
          ].join('\n'),
        },
        {
          id: 'stdio',
          titleKey: 'integrationCatalog.block.stdio',
          hintKey: 'integrationCatalog.mcpStdioHint',
          language: 'json',
          text: JSON.stringify(
            {
              mcpServers: {
                voicestudio: {
                  command: 'python',
                  args: ['-m', 'backend.mcp_shim'],
                  cwd: '/path/to/VoiceStudio',
                  // The full base keeps https and any reverse-proxy path prefix.
                  env: {
                    OMNIVOICE_URL: base,
                    OMNIVOICE_CLIENT_ID: '<your-client-id>',
                    ...(bearer ? { OMNIVOICE_API_KEY: '<backend API key>' } : {}),
                  },
                },
              },
            },
            null,
            2,
          ),
        },
      ];
    },
  },
  'voicestudio-api': {
    capabilities: ['speechApi', 'transcriptionApi'],
    taglineKey: 'integrationCatalog.tagline.api',
    docs: `${REPO_DOCS}/api-auth.md`,
    blocks: (baseUrl) => {
      const base = backendEndpoint(baseUrl, '');
      if (!base) return null;
      // A remote https backend may require its API key: read it from the
      // caller's environment so no credential is ever exported. Plain-http
      // remotes get no key at all (it would cross the network in clear text).
      const policy = remoteAuth(baseUrl);
      const auth =
        policy === 'bearer' ? ['  -H "Authorization: Bearer $OMNIVOICE_API_KEY" \\'] : [];
      return [
        {
          id: 'base',
          titleKey: 'integrationCatalog.block.baseUrl',
          // Credential guidance lives in translated hints, not in code comments.
          hintKey:
            policy === 'bearer'
              ? 'integrationCatalog.apiBearerHint'
              : policy === 'insecure'
                ? 'integrationCatalog.apiInsecureHint'
                : 'integrationCatalog.apiHint',
          language: 'text',
          text: `${base}/v1\n`,
        },
        {
          id: 'speech',
          titleKey: 'integrationCatalog.block.speechCurl',
          language: 'shell',
          text: [
            `curl ${base}/v1/audio/speech \\`,
            ...auth,
            '  -H "Content-Type: application/json" \\',
            '  -d \'{"model":"tts-1","voice":"default","input":"Hello from VoiceStudio.","response_format":"wav"}\' \\',
            '  --output speech.wav',
            '',
          ].join('\n'),
        },
        {
          id: 'transcription',
          titleKey: 'integrationCatalog.block.transcriptionCurl',
          language: 'shell',
          text: [
            `curl ${base}/v1/audio/transcriptions \\`,
            ...auth,
            '  -F file=@speech.wav \\',
            '  -F model=whisper-1',
            '',
          ].join('\n'),
        },
        {
          id: 'python',
          titleKey: 'integrationCatalog.block.openaiPython',
          language: 'python',
          text: [
            ...(policy === 'bearer' ? ['import os', ''] : []),
            'from openai import OpenAI',
            '',
            'client = OpenAI(',
            `    base_url="${base}/v1",`,
            policy === 'bearer'
              ? '    api_key=os.environ.get("OMNIVOICE_API_KEY", "not-needed"),'
              : '    api_key="voicestudio",',
            ')',
            '',
            'with client.audio.speech.with_streaming_response.create(',
            '    model="tts-1", voice="default", input="Hello from VoiceStudio.",',
            '    response_format="wav",',
            ') as response:',
            '    response.stream_to_file("speech.wav")',
            '',
            'with open("speech.wav", "rb") as audio:',
            '    print(client.audio.transcriptions.create(model="whisper-1", file=audio).text)',
            '',
          ].join('\n'),
        },
      ];
    },
  },
  docker: container(DOCKER_HUB_IMAGE, `${REPO_DOCS}/install/docker.md`),
  'github-container-registry': container(GHCR_IMAGE, `${REPO_DOCS}/install/docker.md`),
  'openai-agents': {
    // VoiceStudio serves the pipeline's speech-to-text and text-to-speech; the
    // agent's language model is a local OpenAI-compatible server of the user's.
    capabilities: ['speechApi', 'transcriptionApi', 'localLlm'],
    taglineKey: 'integrationCatalog.tagline.openaiAgents',
    docs: `${REPO_DOCS}/agentic-voice.md#openai-agents-sdk`,
    blocks: (baseUrl) => {
      const setup = openaiAgentsSetup('openai-agents', baseUrl);
      if (!setup) return null;
      return [
        {
          id: 'script',
          titleKey: 'integrationCatalog.block.openaiPython',
          // A keyed remote plain-http backend cannot be reached safely: say
          // "use https" instead of implying the key will be sent.
          hintKey:
            remoteAuth(baseUrl) === 'insecure'
              ? 'integrationCatalog.apiInsecureHint'
              : 'integrationCatalog.openaiAgentsHint',
          language: 'python',
          text: setup.text,
        },
      ];
    },
  },
  twilio: {
    capabilities: ['phoneCalls'],
    taglineKey: 'integrationCatalog.tagline.twilio',
    docs: TWILIO_DOCS,
    blocks: () => [],
    panel: TwilioSetup,
  },
  n8n: {
    capabilities: ['workflow', 'speechApi'],
    taglineKey: 'integrationCatalog.tagline.n8n',
    docs: `${REPO_DOCS}/integrations/n8n.md`,
    blocks: (baseUrl) => {
      const setup = n8nSetup('n8n', baseUrl);
      if (!setup) return null;
      return [
        {
          id: 'workflow',
          titleKey: 'integrationCatalog.block.workflow',
          hintKey: 'integrationCatalog.n8nHint',
          language: 'json',
          text: setup.text,
          download: { file: setup.file, type: 'application/json' },
        },
      ];
    },
  },
};

export function integrationSetup(slug: string): IntegrationSetup | undefined {
  return Object.hasOwn(INTEGRATION_SETUPS, slug) ? INTEGRATION_SETUPS[slug] : undefined;
}
