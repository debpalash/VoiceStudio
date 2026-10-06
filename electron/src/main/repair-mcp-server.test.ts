// @vitest-environment node
import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';

it.each([
  { name: 'unfinished ASCII body', body: 'x'.repeat(1_000_001), prefix: 'x'.repeat(1_000_000), unfinished: true, status: 200 },
  { name: 'unfinished multibyte body', body: 'é'.repeat(500_001), prefix: 'é'.repeat(500_000), unfinished: true, status: 200 },
  { name: 'UTF-8 sequence crossing the limit', body: 'x'.repeat(999_999) + 'é', prefix: 'x'.repeat(999_999), unfinished: true, status: 200 },
  { name: 'short UTF-8 error body', body: 'Unavailable — café', prefix: 'Unavailable — café', unfinished: false, status: 503 },
])('bounds native MCP diagnostics: $name', async ({ body, prefix, unfinished, status }) => {
  const directory = await mkdtemp(join(tmpdir(), 'voice-repair-mcp-'));
  let entered!: () => void;
  const streaming = new Promise<void>((resolve) => { entered = resolve; });
  let disconnected!: () => void;
  const closed = new Promise<void>((resolve) => { disconnected = resolve; });
  const server = createServer((_request, response) => {
    response.on('close', disconnected);
    response.writeHead(status, { 'Content-Type': 'text/plain' });
    response.write(body);
    if (!unfinished) response.end();
    entered();
    // Keep the body open: a diagnostic limit must not wait for this tail.
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('No test listener');
  const context = join(directory, 'context.json');
  await writeFile(context, JSON.stringify({ baseUrl: `http://127.0.0.1:${address.port}`, headers: {} }));
  const child = spawn(process.execPath, [fileURLToPath(new URL('./repair-mcp-server.ts', import.meta.url))], {
    env: { ...process.env, VOICESTUDIO_REPAIR_CONTEXT_FILE: context },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const exited = new Promise<void>((resolve) => child.once('exit', () => resolve()));
  child.stderr.resume();
  let output = '';
  const reply = new Promise<Record<string, any>>((resolve, reject) => {
    child.once('error', reject);
    child.stdout.on('data', (data) => {
      output += data;
      if (output.includes('\n')) resolve(JSON.parse(output.slice(0, output.indexOf('\n'))));
    });
  });
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: {
    name: 'api_request', arguments: { path: '/logs' },
  } }) + '\n');
  let deadline: ReturnType<typeof setTimeout> | undefined;
  try {
    await streaming;
    const result = await Promise.race([reply, new Promise<never>((_resolve, reject) => {
      deadline = setTimeout(() => reject(new Error('Bounded response waited for the unfinished tail')), 2000);
    })]);
    expect(result.id).toBe(1);
    expect(result.result.isError).toBe(status >= 400);
    expect(result.result.content[0].text).toBe(`HTTP ${status}\n${prefix}`);
    await closed;
  } finally {
    clearTimeout(deadline);
    child.kill();
    await exited;
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    await rm(directory, { recursive: true, force: true });
  }
}, 10_000);
