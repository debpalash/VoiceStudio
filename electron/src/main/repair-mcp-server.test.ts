import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readFileSync } from 'node:fs';
import { transformWithOxc } from 'vite';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const LIMIT = 1_000_000;
let dir: string;
let server: Server;
let baseUrl: string;
let streamClosed: Promise<void>;
let markClosed: () => void;

beforeAll(async () => {
  dir = mkdtempSync(join(tmpdir(), 'vs-repair-mcp-'));
  const source = readFileSync(join(__dirname, 'repair-mcp-server.ts'), 'utf8');
  const { code } = await transformWithOxc(source, 'repair-mcp-server.ts');
  writeFileSync(join(dir, 'server.mjs'), code);
  server = createServer((req, res) => {
    if (req.url === '/endless') {
      // Never finishes: three-byte characters so the byte cap lands mid-character.
      res.writeHead(200, { 'content-type': 'text/plain; charset=utf-8' });
      const chunk = Buffer.from('€'.repeat(40_000));
      const timer = setInterval(() => res.write(chunk), 1);
      res.on('close', () => {
        clearInterval(timer);
        markClosed();
      });
      return;
    }
    res.writeHead(200).end('hello €');
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  writeFileSync(join(dir, 'context.json'), JSON.stringify({ baseUrl, headers: {} }));
  streamClosed = new Promise<void>((resolve) => {
    markClosed = resolve;
  });
});

afterAll(() => {
  server.closeAllConnections();
  server.close();
  rmSync(dir, { recursive: true, force: true });
});

function call(lines: string[]): Promise<Array<Record<string, any>>> {
  const child = spawn(process.execPath, [join(dir, 'server.mjs')], {
    env: { ...process.env, VOICESTUDIO_REPAIR_CONTEXT_FILE: join(dir, 'context.json') },
    stdio: ['pipe', 'pipe', 'ignore'],
  });
  const expected = lines.length;
  const messages: Array<Record<string, any>> = [];
  let out = '';
  return new Promise((resolve, reject) => {
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (data: string) => {
      out += data;
      const parts = out.split('\n');
      out = parts.pop() ?? '';
      for (const part of parts) if (part) messages.push(JSON.parse(part));
      if (messages.length >= expected) {
        child.kill();
        resolve(messages);
      }
    });
    child.on('error', reject);
    child.stdin.write(lines.map((line) => line + '\n').join(''));
  });
}

const apiCall = (path: string) =>
  JSON.stringify({
    jsonrpc: '2.0',
    id: 1,
    method: 'tools/call',
    params: { name: 'api_request', arguments: { path } },
  });

describe('repair MCP api_request', () => {
  it('returns small responses untouched', async () => {
    const [reply] = await call([apiCall('/small')]);
    expect(reply.result.content[0].text).toBe('HTTP 200\nhello €');
  });

  it('stops reading an endless response at the byte cap and cancels the stream', async () => {
    const [reply] = await call([apiCall('/endless')]);
    const text: string = reply.result.content[0].text;
    const body = text.slice('HTTP 200\n'.length);
    expect(Buffer.byteLength(body)).toBeLessThanOrEqual(LIMIT);
    expect(Buffer.byteLength(body)).toBeGreaterThan(LIMIT - 3);
    expect(body).not.toContain('�');
    await streamClosed;
  }, 20_000);

  it('refuses oversized request lines and keeps serving afterwards', async () => {
    const huge = 'x'.repeat(9_000_000);
    const replies = await call([huge, apiCall('/small')]);
    expect(replies[0].error.code).toBe(-32600);
    expect(replies[1].result.content[0].text).toBe('HTTP 200\nhello €');
  }, 20_000);

  it('caps request lines in UTF-8 bytes, not UTF-16 units', async () => {
    // 3M three-byte characters: 9 MB of input, but only 3M string units.
    const wide = '€'.repeat(3_000_000);
    expect(wide.length).toBeLessThan(8_000_000);
    const replies = await call([wide, apiCall('/small')]);
    expect(replies[0].error.code).toBe(-32600);
    expect(replies[1].result.content[0].text).toBe('HTTP 200\nhello €');
  }, 20_000);
});
