import { createServer } from 'node:http';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import type { RepairAgentId } from '../preload/index.d';

export interface AgentCompletionRequest {
  agent: RepairAgentId;
  model: string;
  messages: Array<{ role: string; content: string }>;
  timeoutMs: number;
}

export function validateAgentCompletion(value: unknown): AgentCompletionRequest {
  const body = value as AgentCompletionRequest;
  if (
    !body ||
    !['codex', 'claude', 'pi', 'opencode'].includes(body.agent) ||
    typeof body.model !== 'string' ||
    body.model.length > 200 ||
    (body.model !== '' && !/^[a-zA-Z0-9][\w./:@+-]*$/.test(body.model)) ||
    !Array.isArray(body.messages) ||
    body.messages.length < 1 ||
    body.messages.length > 100 ||
    body.messages.some(
      (m) =>
        !m || !['system', 'user', 'assistant'].includes(m.role) || typeof m.content !== 'string',
    ) ||
    !Number.isFinite(body.timeoutMs) ||
    body.timeoutMs < 1000 ||
    body.timeoutMs > 600_000
  ) {
    throw new Error('Invalid agent completion request');
  }
  return body;
}

/** Backend-only capability; no browser CORS, no arbitrary commands or repair API access. */
export async function startLlmAgentBridge(
  complete: (request: AgentCompletionRequest) => Promise<string>,
) {
  const token = randomBytes(32).toString('hex');
  const expected = Buffer.from('Bearer ' + token);
  let tail = Promise.resolve();
  let queued = 0;
  let closed = false;
  const server = createServer(async (req, res) => {
    const supplied = Buffer.from(req.headers.authorization || '');
    const send = (status: number, data: unknown) => {
      res.writeHead(status, { 'content-type': 'application/json' });
      res.end(JSON.stringify(data));
    };
    if (
      supplied.length !== expected.length ||
      !timingSafeEqual(supplied, expected) ||
      req.headers.origin
    ) {
      send(403, { error: 'Forbidden' });
      return;
    }
    if (req.method !== 'POST' || req.url !== '/complete') {
      send(404, { error: 'Not found' });
      return;
    }
    const chunks: Buffer[] = [];
    let bytes = 0;
    try {
      for await (const chunk of req) {
        bytes += chunk.length;
        if (bytes > 500_000) {
          send(413, { error: 'Request too large' });
          return;
        }
        chunks.push(chunk);
      }
      let body: AgentCompletionRequest;
      try {
        body = validateAgentCompletion(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      } catch {
        send(400, { error: 'Invalid request' });
        return;
      }
      if (queued >= 8 || closed) {
        send(429, { error: 'Agent is busy' });
        return;
      }
      const deadline = Date.now() + body.timeoutMs;
      queued += 1;
      let started = false;
      let expired = false;
      let counted = true;
      const uncount = () => {
        if (counted) queued -= 1;
        counted = false;
      };
      const task = tail.then(async () => {
        const remaining = deadline - Date.now();
        if (expired || closed || res.destroyed || remaining < 1000)
          throw new Error('Agent request expired');
        started = true;
        return complete({ ...body, timeoutMs: remaining });
      });
      // The real task keeps its place so CLI runs never overlap; an expired
      // request that never started is skipped (and frees its slot) at once.
      tail = task
        .then(
          () => {},
          () => {},
        )
        .finally(uncount);
      task.catch(() => {});
      let timer: NodeJS.Timeout | undefined;
      const deadlineHit = new Promise<never>((_, reject) => {
        timer = setTimeout(
          () => {
            expired = true;
            if (!started) uncount();
            reject(new Error('Agent request expired'));
          },
          Math.max(0, deadline - Date.now()),
        );
      });
      deadlineHit.catch(() => {});
      let text: string;
      try {
        text = await Promise.race([task, deadlineHit]);
      } finally {
        clearTimeout(timer);
      }
      send(200, { text });
    } catch (error) {
      // Never expose subprocess output, login tokens, or source dialogue in errors.
      const status =
        error instanceof Error
          ? (
              {
                AgentAuthenticationError: 401,
                AgentRateLimitError: 429,
                AgentModelError: 404,
              } as Record<string, number>
            )[error.name]
          : undefined;
      send(status ?? 502, { error: 'Agent completion failed. Check CLI installation and login.' });
    }
  });
  server.requestTimeout = 30_000;
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Agent bridge did not bind');
  return {
    url: `http://127.0.0.1:${address.port}`,
    token,
    close: () => {
      closed = true;
      server.closeAllConnections();
      server.close();
    },
  };
}
