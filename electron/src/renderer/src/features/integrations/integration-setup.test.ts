import { expect, it } from 'vitest';
import {
  INTEGRATION_CATALOG,
  integrationSlug,
  getIntegrationBySlug,
} from '../../../../../../frontend/src/config/integration-catalog';
import {
  INTEGRATION_CATEGORIES,
  integrationCategoryKey,
  isIntegrationCategory,
} from './integration-categories';

it('gives every catalog category its own label key, never one borrowed from another feature', () => {
  const labels = en.integrationCatalog.category as Record<string, string>;
  const used = new Set(INTEGRATION_CATALOG.map((entry) => entry.category));
  for (const category of used) {
    expect(isIntegrationCategory(category), category).toBe(true);
    expect(integrationCategoryKey(category)).toBe(`integrationCatalog.category.${category}`);
  }
  for (const category of INTEGRATION_CATEGORIES) expect(labels[category], category).toBeTruthy();
  const shown = INTEGRATION_CATEGORIES.map((category) => labels[category]);
  expect(new Set(shown).size).toBe(shown.length);
  expect(labels.comms).toBe('Calling & voice agents');
  expect(integrationCategoryKey('unknown')).toBe('integrationCatalog.category.other');
});

it('gives every directory entry one stable route and its actual category', () => {
  const slugs = INTEGRATION_CATALOG.map((entry) => integrationSlug(entry.name));
  expect(new Set(slugs).size).toBe(slugs.length);
  expect(getIntegrationBySlug('n8n')?.category).toBe('automation');
  expect(getIntegrationBySlug('claude-code')?.category).toBe('agents');
});

import { mcpSetup, remoteAuth } from './mcp-setup';
it('exports client-specific HTTP configuration for the actual backend and port', () => {
  const claude = mcpSetup('claude-code', 'http://127.0.0.1:3912');
  const cursor = mcpSetup('cursor', 'https://voice.example/backend/');
  expect(claude?.file).toBe('.mcp.json');
  expect(JSON.parse(claude!.text).mcpServers.voicestudio).toEqual({
    type: 'http',
    url: 'http://127.0.0.1:3912/mcp/',
    headers: { 'X-OmniVoice-Client-Id': 'claude-code' },
  });
  expect(cursor?.file).toBe('.cursor/mcp.json');
  expect(JSON.parse(cursor!.text).mcpServers.voicestudio).toEqual({
    url: 'https://voice.example/backend/mcp/',
    headers: {
      'X-OmniVoice-Client-Id': 'cursor',
      Authorization: 'Bearer ${env:OMNIVOICE_API_KEY}',
    },
  });
});
it('never exports credentials, unsafe URLs, or unsupported client configurations', () => {
  for (const url of [
    '',
    'file:///tmp/backend',
    'http://secret:password@localhost:3900',
    'https://host/?key=secret',
    'https://host/#secret',
  ]) {
    expect(mcpSetup('claude-code', url)).toBeNull();
  }
  expect(mcpSetup('constructor', 'http://localhost:3900')).toBeNull();
  expect(mcpSetup('twilio', 'http://localhost:3900')).toBeNull();
});

import { n8nSetup } from './n8n-setup';
it('exports a manual n8n workflow to the current backend without credentials', () => {
  const setup = n8nSetup('n8n', 'https://voice.example/backend/');
  const workflow = JSON.parse(setup!.text);
  expect(workflow.active).toBe(false);
  expect(workflow.id).toMatch(/^[a-f0-9]{20}$/);
  expect(JSON.parse(n8nSetup('n8n', 'https://voice.example/backend/')!.text).id).not.toBe(
    workflow.id,
  );
  expect(workflow.nodes[0].type).toBe('n8n-nodes-base.manualTrigger');
  const request = workflow.nodes[1];
  expect(request.parameters.url).toBe('https://voice.example/backend/v1/audio/speech');
  expect(request.parameters.method).toBe('POST');
  expect(JSON.parse(request.parameters.jsonBody)).toEqual({
    model: 'tts-1',
    input: 'VoiceStudio',
    voice: 'default',
    response_format: 'wav',
  });
  expect(request.parameters.options.response.response).toEqual({
    responseFormat: 'file',
    outputPropertyName: 'audio',
  });
  expect(request.parameters.options.redirect.redirect.followRedirects).toBe(false);
  expect(request.credentials).toBeUndefined();
  expect(workflow.connections.Start.main[0][0].node).toBe(request.name);
  expect(JSON.parse(n8nSetup('n8n', 'http://127.0.0.1:3912')!.text).nodes[1].parameters.url).toBe(
    'http://127.0.0.1:3912/v1/audio/speech',
  );
});
it('does not leak credentials or turn unrelated directory cards into connectors', () => {
  for (const url of [
    '',
    'file:///tmp/backend',
    'https://secret:password@host',
    'https://host?token=secret',
    'https://host/#secret',
  ]) {
    expect(n8nSetup('n8n', url)).toBeNull();
  }
  expect(n8nSetup('twilio', 'http://localhost:3900')).toBeNull();
});

import en from '../../i18n/locales/en.json';
import { INTEGRATION_SETUPS, integrationSetup } from './setup-registry';
it('exports Codex CLI as a config.toml Streamable HTTP server table', () => {
  const codex = mcpSetup('codex-cli', 'http://127.0.0.1:3912');
  expect(codex?.file).toBe('~/.codex/config.toml');
  expect(codex?.format).toBe('toml');
  expect(codex?.text).toBe(
    [
      '[mcp_servers.voicestudio]',
      'url = "http://127.0.0.1:3912/mcp/"',
      'http_headers = { "X-OmniVoice-Client-Id" = "codex-cli" }',
      '',
    ].join('\n'),
  );
  expect(mcpSetup('codex-cli', 'https://secret:pw@host')).toBeNull();
});

function lookup(key: string) {
  return key
    .split('.')
    .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);
}
it('backs every "Works with VoiceStudio" entry with a real catalog route, blocks and strings', () => {
  const base = 'http://127.0.0.1:3912';
  for (const [slug, setup] of Object.entries(INTEGRATION_SETUPS)) {
    expect(getIntegrationBySlug(slug), slug).toBeDefined();
    const blocks = setup.blocks(base);
    // A connector offers copyable blocks, an interactive panel, or both.
    expect((blocks?.length ?? 0) > 0 || Boolean(setup.panel), slug).toBe(true);
    for (const capability of setup.capabilities)
      expect(typeof lookup(`integrationCatalog.capability.${capability}`)).toBe('string');
    for (const block of blocks!) {
      expect(typeof lookup(block.titleKey), block.titleKey).toBe('string');
      if (block.hintKey) expect(typeof lookup(block.hintKey), block.hintKey).toBe('string');
    }
    expect(setup.docs).toMatch(/^https:\/\//);
  }
  // Prose belongs in translated hints: config and API snippets carry no
  // natural-language comments. The OpenAI Agents entry is a runnable script
  // whose inline comments document its code, so it is exempt.
  for (const url of [base, 'https://gpu.example/vs', 'http://192.168.1.5:3900'])
    for (const [slug, setup] of Object.entries(INTEGRATION_SETUPS))
      if (slug !== 'openai-agents')
        for (const block of setup.blocks(url) ?? [])
          expect(block.text, block.id).not.toMatch(/^\s*(#|\/\/)/m);
  expect(integrationSetup('zapier')).toBeUndefined();
  expect(integrationSetup('constructor')).toBeUndefined();
});
it('gives the API and container cards runnable snippets for the right endpoints and images', () => {
  const api = INTEGRATION_SETUPS['voicestudio-api'].blocks('http://127.0.0.1:3912/')!;
  const text = api.map((block) => block.text).join('\n');
  expect(text).toContain('curl http://127.0.0.1:3912/v1/audio/speech');
  expect(text).toContain('curl http://127.0.0.1:3912/v1/audio/transcriptions');
  expect(text).toContain('base_url="http://127.0.0.1:3912/v1"');
  expect(INTEGRATION_SETUPS['voicestudio-api'].blocks('https://u:p@host')).toBeNull();
  const docker = INTEGRATION_SETUPS.docker
    .blocks('')!
    .map((block) => block.text)
    .join('\n');
  expect(docker).toContain('palashdeb/omnivoice-studio:stable');
  const ghcr = INTEGRATION_SETUPS['github-container-registry'].blocks('')!;
  expect(ghcr.map((block) => block.text).join('\n')).toContain(
    'ghcr.io/debpalash/omnivoice-studio:stable',
  );
  const mcp = INTEGRATION_SETUPS['model-context-protocol'].blocks('http://127.0.0.1:3912')!;
  expect(mcp[0].text).toContain('URL: http://127.0.0.1:3912/mcp/\n');
  const local = JSON.parse(mcp[1].text).mcpServers.voicestudio;
  expect(local.args).toEqual(['-m', 'backend.mcp_shim']);
  expect(local.env).toEqual({
    OMNIVOICE_URL: 'http://127.0.0.1:3912',
    OMNIVOICE_CLIENT_ID: '<your-client-id>',
  });
  expect(text).not.toContain('Authorization');
});
it('keeps remote scheme, path prefix and credential placeholders without exporting secrets', () => {
  const remote = 'https://gpu.example/voicestudio/';
  const [http, stdio] = INTEGRATION_SETUPS['model-context-protocol'].blocks(remote)!;
  expect(http.text).toContain('Header: Authorization: Bearer $OMNIVOICE_API_KEY');
  expect(INTEGRATION_SETUPS['voicestudio-api'].blocks(remote)![0].hintKey).toBe(
    'integrationCatalog.apiBearerHint',
  );
  expect(JSON.parse(stdio.text).mcpServers.voicestudio.env).toEqual({
    OMNIVOICE_URL: 'https://gpu.example/voicestudio',
    OMNIVOICE_CLIENT_ID: '<your-client-id>',
    OMNIVOICE_API_KEY: '<backend API key>',
  });
  const api = INTEGRATION_SETUPS['voicestudio-api'].blocks(remote)!;
  for (const id of ['speech', 'transcription'])
    expect(api.find((block) => block.id === id)!.text).toContain(
      '-H "Authorization: Bearer $OMNIVOICE_API_KEY"',
    );
  expect(api.find((block) => block.id === 'python')!.text).toContain(
    'api_key=os.environ.get("OMNIVOICE_API_KEY", "not-needed")',
  );
  // Each MCP client references the key through its own env interpolation.
  const header = (slug: string) =>
    JSON.parse(mcpSetup(slug, remote)!.text).mcpServers.voicestudio.headers;
  expect(header('claude-code')).toEqual({
    'X-OmniVoice-Client-Id': 'claude-code',
    Authorization: 'Bearer ${OMNIVOICE_API_KEY}',
  });
  expect(header('cursor').Authorization).toBe('Bearer ${env:OMNIVOICE_API_KEY}');
  expect(mcpSetup('codex-cli', remote)!.text).toContain(
    'bearer_token_env_var = "OMNIVOICE_API_KEY"',
  );
});
it('never sends an API key to a remote plain-http backend', () => {
  const insecure = 'http://192.168.1.5:3900';
  for (const slug of ['claude-code', 'cursor', 'codex-cli'])
    expect(mcpSetup(slug, insecure)!.text).not.toMatch(/Authorization|bearer_token/);
  const stdio = INTEGRATION_SETUPS['model-context-protocol'].blocks(insecure)![1];
  expect(JSON.parse(stdio.text).mcpServers.voicestudio.env.OMNIVOICE_API_KEY).toBeUndefined();
  const api = INTEGRATION_SETUPS['voicestudio-api']
    .blocks(insecure)!
    .map((block) => block.text)
    .join('\n');
  expect(api).not.toContain('Authorization');
  expect(api).not.toContain('os.environ');
  expect(INTEGRATION_SETUPS['voicestudio-api'].blocks(insecure)![0].hintKey).toBe(
    'integrationCatalog.apiInsecureHint',
  );
  expect(INTEGRATION_SETUPS['model-context-protocol'].blocks(insecure)![0].text).not.toContain(
    'Authorization',
  );
  expect(remoteAuth('http://localhost:3900')).toBe('none');
  expect(remoteAuth('http://[::1]:3900')).toBe('none');
});

import { openaiAgentsSetup } from './openai-agents-setup';
it('points the OpenAI Agents voice pipeline at the current backend without a stored key', () => {
  const setup = openaiAgentsSetup('openai-agents', 'https://voice.example/backend/');
  expect(setup?.file).toBe('voicestudio_agents.py');
  expect(setup!.text).toContain('base_url="https://voice.example/backend/v1"');
  expect(setup!.text).toContain(
    'api_key=os.environ.get("OMNIVOICE_API_KEY", "not-needed-locally")',
  );
  expect(setup!.text).toContain('tts_model="gpt-4o-mini-tts"');
  expect(setup!.text).toContain('stt_model="gpt-4o-transcribe"');
  expect(setup!.text).toContain('set_tracing_disabled(True)');
  // The agent's LLM is explicit and user-chosen: no silent hosted default.
  expect(setup!.text).toContain(
    'model=OpenAIChatCompletionsModel(model=os.environ["AGENT_LLM_MODEL"]',
  );
  expect(setup!.text).toContain('base_url=os.environ["AGENT_LLM_BASE_URL"]');
  for (const url of [
    '',
    'file:///tmp/backend',
    'https://secret:password@host',
    'https://host?k=1',
  ]) {
    expect(openaiAgentsSetup('openai-agents', url)).toBeNull();
  }
  expect(openaiAgentsSetup('n8n', 'http://localhost:3900')).toBeNull();
  // Registered like every other connector: badge, real capabilities, one block.
  const entry = integrationSetup('openai-agents')!;
  expect(entry.capabilities).toEqual(['speechApi', 'transcriptionApi', 'localLlm']);
  expect(entry.blocks('http://127.0.0.1:3912')![0].text).toContain(
    'base_url="http://127.0.0.1:3912/v1"',
  );
});

it('gives Windows a PowerShell docker run with a CSPRNG key and backtick continuations', () => {
  for (const slug of ['docker', 'github-container-registry']) {
    const blocks = INTEGRATION_SETUPS[slug].blocks('')!;
    const ps = blocks.find((block) => block.id === 'run-powershell')!;
    expect(ps.language).toBe('powershell');
    expect(ps.text).not.toMatch(/^export |\\$/m);
    expect(ps.text).toContain('RandomNumberGenerator');
    expect(ps.text).toContain('-e OMNIVOICE_API_KEY="$env:OMNIVOICE_API_KEY" `');
    expect(ps.text.trimEnd()).toMatch(/omnivoice-studio:stable$/);
  }
});
it('reads the Agents SDK key only for a remote https backend', () => {
  for (const url of ['http://192.168.1.5:3900', 'http://127.0.0.1:3900']) {
    const text = openaiAgentsSetup('openai-agents', url)!.text;
    expect(text).toContain('api_key="not-needed-locally"');
    expect(text).not.toContain('OMNIVOICE_API_KEY');
  }
  expect(integrationSetup('openai-agents')!.blocks('http://192.168.1.5:3900')![0].hintKey).toBe(
    'integrationCatalog.apiInsecureHint',
  );
});
