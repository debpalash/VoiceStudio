// @vitest-environment node
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';

type DiagnosticCase = { body: string; unfinished: boolean; status: number };

async function nativeDiagnostic(
  { body, unfinished, status }: DiagnosticCase,
  fixtureSource?: string,
  timeoutMs = 2000,
) {
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
  let child: ChildProcessWithoutNullStreams | undefined;
  let exited: Promise<void> | undefined;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  try {
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('No test listener');
    const context = join(directory, 'context.json');
    await writeFile(context, JSON.stringify({ baseUrl: `http://127.0.0.1:${address.port}`, headers: {} }));
    let script = fileURLToPath(new URL('./repair-mcp-server.ts', import.meta.url));
    if (fixtureSource !== undefined) {
      script = join(directory, 'fixture.mjs');
      await writeFile(script, fixtureSource);
    }
    child = spawn(process.execPath, [script], {
      env: { ...process.env, VOICESTUDIO_REPAIR_CONTEXT_FILE: context },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    exited = new Promise<void>((resolve) => child!.once('close', () => resolve()));
    const failed = new Promise<never>((_resolve, reject) => {
      child!.once('error', reject);
      child!.once('close', (code) => reject(new Error(`Diagnostic child exited before completion (${code})`)));
      child!.stdin.once('error', reject);
    });
    child.stderr.resume();
    child.stdout.setEncoding('utf8');
    let output = '';
    const reply = new Promise<Record<string, any>>((resolve, reject) => {
      child!.stdout.on('data', (data) => {
        output += data;
        if (output.includes('\n')) {
          try {
            resolve(JSON.parse(output.slice(0, output.indexOf('\n'))));
          } catch (error) {
            reject(error);
          }
        }
      });
    });
    const timedOut = new Promise<never>((_resolve, reject) => {
      deadline = setTimeout(() => reject(new Error('Diagnostic fixture timed out')), timeoutMs);
    });
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: {
      name: 'api_request', arguments: { path: '/logs' },
    } }) + '\n');
    // Entry, reply and disconnect share one deadline. Child exit rejects even
    // before a request reaches the server, so cleanup is always reachable.
    const [, result] = await Promise.race([Promise.all([streaming, reply, closed]), failed, timedOut]);
    return result;
  } finally {
    clearTimeout(deadline);
    if (child && child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    let cleanupDeadline: ReturnType<typeof setTimeout> | undefined;
    try {
      if (exited) await Promise.race([exited, new Promise<never>((_resolve, reject) => {
        cleanupDeadline = setTimeout(() => reject(new Error('Diagnostic child cleanup timed out')), 1000);
      })]);
    } finally {
      clearTimeout(cleanupDeadline);
      await rm(directory, { recursive: true, force: true });
    }
  }
}

it.each([
  { name: 'unfinished ASCII body', body: 'x'.repeat(1_000_001), prefix: 'x'.repeat(1_000_000), unfinished: true, status: 200 },
  { name: 'unfinished multibyte body', body: 'é'.repeat(500_001), prefix: 'é'.repeat(500_000), unfinished: true, status: 200 },
  { name: 'UTF-8 sequence crossing the limit', body: 'x'.repeat(999_999) + 'é', prefix: 'x'.repeat(999_999), unfinished: true, status: 200 },
  { name: 'short UTF-8 error body', body: 'Unavailable — café', prefix: 'Unavailable — café', unfinished: false, status: 503 },
])('bounds native MCP diagnostics: $name', async ({ body, prefix, unfinished, status }) => {
  const result = await nativeDiagnostic({ body, unfinished, status });
  expect(result.id).toBe(1);
  expect(result.result.isError).toBe(status >= 400);
  expect(result.result.content[0].text).toBe(`HTTP ${status}\n${prefix}`);
}, 5000);

it('decodes diagnostic stdout across a native split UTF-8 write', async () => {
  const result = await nativeDiagnostic({ body: 'café', unfinished: false, status: 200 }, String.raw`
import { readFileSync } from 'node:fs';
process.stdin.once('data', async () => {
  const { baseUrl } = JSON.parse(readFileSync(process.env.VOICESTUDIO_REPAIR_CONTEXT_FILE, 'utf8'));
  const response = await fetch(baseUrl + '/logs');
  const body = await response.text();
  const output = Buffer.from(JSON.stringify({ id: 1, result: { content: [{ text: body }] } }) + '\n');
  const split = output.indexOf(Buffer.from('é')) + 1;
  process.stdout.write(output.subarray(0, split));
  setTimeout(() => process.stdout.write(output.subarray(split)), 50);
});
`);
  expect(result.result.content[0].text).toBe('café');
}, 5000);

it('cleans up when the native diagnostic child exits before server entry', async () => {
  await expect(nativeDiagnostic({ body: '', unfinished: false, status: 200 },
    `process.stdin.once('data', () => process.exit(7));`)).rejects.toThrow('Diagnostic child exited before completion (7)');
}, 5000);

it('bounds server entry and cleans up a native child that never requests', async () => {
  await expect(nativeDiagnostic({ body: '', unfinished: false, status: 200 },
    `process.stdin.resume();`, 100)).rejects.toThrow('Diagnostic fixture timed out');
}, 5000);
