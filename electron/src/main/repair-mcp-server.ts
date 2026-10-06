import { readFileSync } from 'node:fs';

interface JsonRpcRequest {
  jsonrpc?: string;
  id?: string | number | null;
  method?: string;
  params?: Record<string, unknown>;
}

interface RepairContext {
  baseUrl: string;
  headers: Record<string, string>;
}

const MAX_RESPONSE = 1_000_000;
const configuredContextPath = process.env.VOICESTUDIO_REPAIR_CONTEXT_FILE;
if (!configuredContextPath) throw new Error('VOICESTUDIO_REPAIR_CONTEXT_FILE is required');
const contextPath: string = configuredContextPath;

function send(message: unknown): void {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function result(id: JsonRpcRequest['id'], value: unknown): void {
  send({ jsonrpc: '2.0', id, result: value });
}

function error(id: JsonRpcRequest['id'], code: number, message: string): void {
  send({ jsonrpc: '2.0', id, error: { code, message } });
}

/** Stop reading diagnostics at the limit, including a body that never finishes. */
async function boundedResponseText(response: Response): Promise<string> {
  if (!response.body) return '';
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = '';
  let remaining = MAX_RESPONSE;
  try {
    while (remaining > 0) {
      const { done, value } = await reader.read();
      if (done) return text + decoder.decode();
      const prefix = value.subarray(0, remaining);
      text += decoder.decode(prefix, { stream: true });
      remaining -= prefix.byteLength;
    }
    // Leave an incomplete UTF-8 sequence at the boundary out of the prefix.
    return text;
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

async function callApi(args: Record<string, unknown>) {
  const path = typeof args.path === 'string' ? args.path : '';
  if (!path.startsWith('/') || path.startsWith('//')) throw new Error('path must start with /');
  const method = typeof args.method === 'string' ? args.method.toUpperCase() : 'GET';
  if (!['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(method))
    throw new Error('unsupported method');
  const context = JSON.parse(readFileSync(contextPath, 'utf8')) as RepairContext;
  const headers: Record<string, string> = { ...context.headers };
  const hasBody = args.body !== undefined && !['GET', 'HEAD'].includes(method);
  if (hasBody) headers['Content-Type'] = 'application/json';
  const response = await fetch(`${context.baseUrl}${path}`, {
    method,
    headers,
    body: hasBody ? JSON.stringify(args.body) : undefined,
    signal: AbortSignal.timeout(120_000),
  });
  const text = await boundedResponseText(response);
  return {
    content: [
      {
        type: 'text',
        text: `HTTP ${response.status}${text ? `\n${text}` : ''}`,
      },
    ],
    isError: !response.ok,
  };
}

async function handle(request: JsonRpcRequest): Promise<void> {
  if (request.method === 'notifications/initialized') return;
  if (request.method === 'initialize') {
    result(request.id, {
      protocolVersion:
        typeof request.params?.protocolVersion === 'string'
          ? request.params.protocolVersion
          : '2025-06-18',
      capabilities: { tools: { listChanged: false } },
      serverInfo: { name: 'voicestudio-repair', version: '1.0.0' },
    });
    return;
  }
  if (request.method === 'ping') {
    result(request.id, {});
    return;
  }
  if (request.method === 'tools/list') {
    result(request.id, {
      tools: [
        {
          name: 'api_request',
          title: 'VoiceStudio app API',
          description:
            'Read or update the running VoiceStudio app through its session-scoped repair bridge. Use /openapi.json to discover backend routes and /__voicestudio/status plus the Electron controls listed in the repair context for backend recovery.',
          inputSchema: {
            type: 'object',
            properties: {
              path: { type: 'string', description: 'Absolute bridge path beginning with /' },
              method: {
                type: 'string',
                enum: ['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'],
                default: 'GET',
              },
              body: { description: 'Optional JSON request body' },
            },
            required: ['path'],
            additionalProperties: false,
          },
        },
      ],
    });
    return;
  }
  if (request.method === 'tools/call') {
    if (request.params?.name !== 'api_request') {
      error(request.id, -32602, 'Unknown VoiceStudio repair tool');
      return;
    }
    try {
      result(
        request.id,
        await callApi((request.params.arguments as Record<string, unknown> | undefined) ?? {}),
      );
    } catch (reason) {
      result(request.id, {
        content: [
          {
            type: 'text',
            text: reason instanceof Error ? reason.message : 'VoiceStudio API request failed',
          },
        ],
        isError: true,
      });
    }
    return;
  }
  if (request.id !== undefined) error(request.id, -32601, 'Method not found');
}

let pending = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk: string) => {
  pending += chunk;
  const lines = pending.split(/\r?\n/);
  pending = lines.pop() ?? '';
  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      const request = JSON.parse(line) as JsonRpcRequest;
      void handle(request).catch(() => error(request.id, -32603, 'Internal error'));
    } catch {
      error(null, -32700, 'Parse error');
    }
  }
});
