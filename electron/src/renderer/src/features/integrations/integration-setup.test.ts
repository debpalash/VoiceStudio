import { expect, it } from 'vitest';
import {
  INTEGRATION_CATALOG,
  integrationSlug,
  getIntegrationBySlug,
} from '../../../../../../frontend/src/config/integration-catalog';

it('gives every directory entry one stable route and its actual category', () => {
  const slugs = INTEGRATION_CATALOG.map((entry) => integrationSlug(entry.name));
  expect(new Set(slugs).size).toBe(slugs.length);
  expect(getIntegrationBySlug('n8n')?.category).toBe('automation');
  expect(getIntegrationBySlug('claude-code')?.category).toBe('agents');
});

import { mcpSetup } from './mcp-setup';
it('exports client-specific HTTP configuration for the actual backend and port', () => {
  const claude = mcpSetup('claude-code', 'http://127.0.0.1:3912');
  const cursor = mcpSetup('cursor', 'https://voice.example/backend/');
  expect(claude?.file).toBe('.mcp.json');
  expect(JSON.parse(claude!.text).mcpServers.voicestudio).toEqual({
    type: 'http',
    url: 'http://127.0.0.1:3912/mcp',
    headers: { 'X-OmniVoice-Client-Id': 'claude-code' },
  });
  expect(cursor?.file).toBe('.cursor/mcp.json');
  expect(JSON.parse(cursor!.text).mcpServers.voicestudio).toEqual({
    url: 'https://voice.example/backend/mcp',
    headers: { 'X-OmniVoice-Client-Id': 'cursor' },
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

import { openaiAgentsSetup } from './openai-agents-setup';
it('points the OpenAI Agents voice pipeline at the current backend without a stored key', () => {
  const setup = openaiAgentsSetup('openai-agents', 'https://voice.example/backend/');
  expect(setup?.file).toBe('voicestudio_agents.py');
  expect(setup!.text).toContain('base_url="https://voice.example/backend/v1"');
  expect(setup!.text).toContain('os.environ.get("OMNIVOICE_API_KEY", "not-needed-locally")');
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
});
