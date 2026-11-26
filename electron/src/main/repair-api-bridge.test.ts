// @vitest-environment node
import { createServer } from 'node:http';
import { existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import { startRepairApiBridge, type RepairApiBridge } from './repair-api-bridge';
import { removeTempDir } from './test-support/remove-temp-dir';

const cleanup: Array<() => Promise<void> | void> = [];
afterEach(async () => {
  await Promise.allSettled(cleanup.splice(0).map((close) => close()));
});

async function upstream() {
  const requests: Array<{
    path: string;
    method: string;
    authorization?: string;
    capability?: string;
  }> = [];
  const server = createServer((request, response) => {
    requests.push({
      path: request.url || '',
      method: request.method || '',
      authorization: request.headers.authorization,
      capability: request.headers['x-voicestudio-repair-token'] as string | undefined,
    });
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end('{"ok":true}');
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  cleanup.push(() => new Promise<void>((resolve) => server.close(() => resolve())));
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('upstream did not bind');
  return { url: `http://127.0.0.1:${address.port}`, requests };
}

function context(bridge: RepairApiBridge) {
  return JSON.parse(readFileSync(bridge.contextFile, 'utf8')) as {
    baseUrl: string;
    headers: Record<string, string>;
    electronControls?: Record<string, unknown>;
  };
}

it('keeps the real backend credential in main and forwards an authorized fix request', async () => {
  const target = await upstream();
  const root = mkdtempSync(join(tmpdir(), 'vs-repair-bridge-test-'));
  cleanup.push(() => removeTempDir(root));
  const bridge = await startRepairApiBridge(
    () => target.url,
    () => ({ Authorization: 'Bearer real-remote-session' }),
    root,
    'fix',
  );
  cleanup.push(bridge.close);
  const capability = context(bridge);

  const response = await fetch(`${capability.baseUrl}/engines/select`, {
    method: 'POST',
    headers: {
      ...capability.headers,
      Authorization: 'Bearer forged-agent-value',
      'Content-Type': 'application/json',
    },
    body: '{"family":"tts","backend_id":"omnivoice"}',
  });

  expect(response.status).toBe(200);
  expect(target.requests).toEqual([
    {
      path: '/engines/select',
      method: 'POST',
      authorization: 'Bearer real-remote-session',
      capability: undefined,
    },
  ]);
  expect(readFileSync(bridge.contextFile, 'utf8')).not.toContain('real-remote-session');
});

it('makes diagnose sessions read-only and rejects requests without the capability', async () => {
  const target = await upstream();
  const root = mkdtempSync(join(tmpdir(), 'vs-repair-bridge-test-'));
  cleanup.push(() => removeTempDir(root));
  const bridge = await startRepairApiBridge(
    () => target.url,
    () => ({}),
    root,
    'diagnose',
  );
  cleanup.push(bridge.close);
  const capability = context(bridge);

  expect((await fetch(`${capability.baseUrl}/health`)).status).toBe(401);
  expect(
    (
      await fetch(`${capability.baseUrl}/engines/select`, {
        method: 'POST',
        headers: capability.headers,
      })
    ).status,
  ).toBe(405);
  expect(target.requests).toHaveLength(0);
});

it('blocks authentication routes and removes the capability file when closed', async () => {
  const target = await upstream();
  const root = mkdtempSync(join(tmpdir(), 'vs-repair-bridge-test-'));
  cleanup.push(() => removeTempDir(root));
  const bridge = await startRepairApiBridge(
    () => target.url,
    () => ({}),
    root,
    'fix',
  );
  const capability = context(bridge);

  expect(
    (
      await fetch(`${capability.baseUrl}/api/%61uth/session`, {
        method: 'POST',
        headers: capability.headers,
      })
    ).status,
  ).toBe(403);
  await bridge.close();
  expect(() => readFileSync(bridge.contextFile, 'utf8')).toThrow();
  expect(target.requests).toHaveLength(0);
});

it('restores Electron-owned backend state even while the Python backend is unavailable', async () => {
  const root = mkdtempSync(join(tmpdir(), 'vs-repair-bridge-test-'));
  cleanup.push(() => removeTempDir(root));
  let restarts = 0;
  let setups = 0;
  const bridge = await startRepairApiBridge(
    () => {
      throw new Error('backend unavailable');
    },
    () => ({}),
    root,
    'fix',
    {
      status: () => ({ stage: 'crashed', managed: true }),
      restartBackend: async () => {
        restarts += 1;
      },
      setupRuntime: () =>
        new Promise<void>(() => {
          setups += 1;
        }),
    },
  );
  cleanup.push(bridge.close);
  const capability = context(bridge);

  const status = await fetch(`${capability.baseUrl}/__voicestudio/status`, {
    headers: capability.headers,
  });
  expect(status.status).toBe(200);
  expect(await status.json()).toMatchObject({ stage: 'crashed' });
  expect(
    (
      await fetch(`${capability.baseUrl}/__voicestudio/backend/restart`, {
        method: 'POST',
        headers: capability.headers,
      })
    ).status,
  ).toBe(202);
  expect(
    (
      await fetch(`${capability.baseUrl}/__voicestudio/backend/setup-runtime`, {
        method: 'POST',
        headers: capability.headers,
      })
    ).status,
  ).toBe(202);
  expect(
    (
      await fetch(`${capability.baseUrl}/__voicestudio/backend/clean-setup`, {
        method: 'POST',
        headers: capability.headers,
      })
    ).status,
  ).toBe(404);
  expect({ restarts, setups }).toEqual({ restarts: 1, setups: 1 });
  expect(capability.electronControls).toBeDefined();
});

it('keeps Electron recovery mutations read-only in diagnose mode', async () => {
  const root = mkdtempSync(join(tmpdir(), 'vs-repair-bridge-test-'));
  cleanup.push(() => removeTempDir(root));
  let restarts = 0;
  const bridge = await startRepairApiBridge(
    () => 'http://127.0.0.1:1',
    () => ({}),
    root,
    'diagnose',
    {
      status: () => ({ stage: 'failed' }),
      restartBackend: async () => {
        restarts += 1;
      },
      setupRuntime: async () => undefined,
    },
  );
  cleanup.push(bridge.close);
  const capability = context(bridge);

  expect(
    (
      await fetch(`${capability.baseUrl}/__voicestudio/backend/restart`, {
        method: 'POST',
        headers: capability.headers,
      })
    ).status,
  ).toBe(405);
  expect(restarts).toBe(0);
});

it('creates locked-down Claude and OpenCode MCP configs and removes them on close', async () => {
  const root = mkdtempSync(join(tmpdir(), 'vs-repair-bridge-test-'));
  cleanup.push(() => removeTempDir(root));
  const bridge = await startRepairApiBridge(
    () => 'http://127.0.0.1:1',
    () => ({}),
    root,
    'fix',
    undefined,
    'C:\\VoiceStudio\\repair-mcp-server.js',
  );
  const capability = context(bridge);
  expect(bridge.mcpConfigFile).toBeTruthy();
  expect(bridge.openCodeConfigFile).toBeTruthy();

  const claude = JSON.parse(readFileSync(bridge.mcpConfigFile!, 'utf8')) as {
    mcpServers: { voicestudio: { args: string[]; env: Record<string, string> } };
  };
  const openCode = JSON.parse(readFileSync(bridge.openCodeConfigFile!, 'utf8')) as {
    permission: Record<string, string>;
    mcp: { voicestudio: { command: string[]; environment: Record<string, string> } };
  };

  expect(claude.mcpServers.voicestudio.args).toEqual(['C:\\VoiceStudio\\repair-mcp-server.js']);
  expect(openCode.permission).toEqual({ '*': 'deny', 'voicestudio_*': 'allow' });
  expect(openCode.mcp.voicestudio.command).toEqual([
    process.execPath,
    'C:\\VoiceStudio\\repair-mcp-server.js',
  ]);
  expect(claude.mcpServers.voicestudio.env.VOICESTUDIO_REPAIR_CONTEXT_FILE).toBe(
    bridge.contextFile,
  );
  expect(openCode.mcp.voicestudio.environment.VOICESTUDIO_REPAIR_CONTEXT_FILE).toBe(
    bridge.contextFile,
  );
  expect(readFileSync(bridge.mcpConfigFile!, 'utf8')).not.toContain(
    capability.headers['X-VoiceStudio-Repair-Token'],
  );
  expect(readFileSync(bridge.openCodeConfigFile!, 'utf8')).not.toContain(
    capability.headers['X-VoiceStudio-Repair-Token'],
  );

  const files = [bridge.contextFile, bridge.mcpConfigFile!, bridge.openCodeConfigFile!];
  await bridge.close();
  expect(files.every((file) => !existsSync(file))).toBe(true);
});
