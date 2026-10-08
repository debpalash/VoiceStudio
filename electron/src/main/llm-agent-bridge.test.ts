import { describe, expect, it, vi } from 'vitest';
import {
  startLlmAgentBridge,
  validateAgentCompletion,
  type AgentCompletionRequest,
} from './llm-agent-bridge';

const request = {
  agent: 'codex' as const,
  model: '',
  messages: [{ role: 'user', content: 'Translate hello' }],
  timeoutMs: 5000,
};
describe('shared CLI completion bridge', () => {
  it('requires its private capability and refuses browser origins', async () => {
    const complete = vi.fn(async (_request: AgentCompletionRequest) => 'Bonjour');
    const bridge = await startLlmAgentBridge(complete);
    try {
      expect((await fetch(bridge.url + '/complete', { method: 'POST' })).status).toBe(403);
      expect(
        (
          await fetch(bridge.url + '/complete', {
            method: 'POST',
            headers: { Authorization: 'Bearer ' + bridge.token, Origin: 'http://localhost' },
          })
        ).status,
      ).toBe(403);
      expect(complete).not.toHaveBeenCalled();
      const response = await fetch(bridge.url + '/complete', {
        method: 'POST',
        headers: { Authorization: 'Bearer ' + bridge.token },
        body: JSON.stringify(request),
      });
      expect(await response.json()).toEqual({ text: 'Bonjour' });
      expect(complete).toHaveBeenCalledWith(
        expect.objectContaining({ agent: request.agent, messages: request.messages }),
      );
      expect(complete.mock.calls[0][0].timeoutMs).toBeLessThanOrEqual(request.timeoutMs);
    } finally {
      bridge.close();
    }
  });
  it('rejects arbitrary commands, flags, roles and unbounded execution', () => {
    for (const patch of [
      { agent: 'powershell' },
      { model: '--unsafe' },
      { timeoutMs: 0 },
      { timeoutMs: Infinity },
      { messages: [{ role: 'tool', content: 'x' }] },
    ]) {
      expect(() => validateAgentCompletion({ ...request, ...patch })).toThrow();
    }
  });
  it('serializes concurrent skill calls and preserves account error classification', async () => {
    let active = 0;
    let maximum = 0;
    const bridge = await startLlmAgentBridge(async (body) => {
      maximum = Math.max(maximum, ++active);
      await new Promise((resolve) => setTimeout(resolve, 10));
      active -= 1;
      if (body.model === 'rejected')
        throw Object.assign(new Error('private provider output'), {
          name: 'AgentAuthenticationError',
        });
      return 'ok';
    });
    const call = (model = '') =>
      fetch(bridge.url + '/complete', {
        method: 'POST',
        headers: { Authorization: 'Bearer ' + bridge.token },
        body: JSON.stringify({ ...request, model }),
      });
    try {
      const results = await Promise.all([call(), call(), call('rejected')]);
      expect(results.map((response) => response.status)).toEqual([200, 200, 401]);
      expect(maximum).toBe(1);
      expect(await results[2].text()).not.toContain('private provider output');
    } finally {
      bridge.close();
    }
  });
});

it('reports a shared agent runner busy state as retryable', async () => {
  const bridge = await startLlmAgentBridge(async () => {
    throw Object.assign(new Error('An agent is already running'), { name: 'AgentRateLimitError' });
  });
  try {
    const response = await fetch(bridge.url + '/complete', {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + bridge.token },
      body: JSON.stringify(request),
    });
    expect(response.status).toBe(429);
  } finally {
    bridge.close();
  }
});

describe('queued completion deadlines', () => {
  it('answers a queued request at its own deadline and never runs it', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'], shouldAdvanceTime: true });
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const seen: string[] = [];
    const bridge = await startLlmAgentBridge(async (body) => {
      seen.push(body.model);
      await gate;
      return 'ok';
    });
    const call = (model: string, timeoutMs: number) =>
      fetch(bridge.url + '/complete', {
        method: 'POST',
        headers: { Authorization: 'Bearer ' + bridge.token },
        body: JSON.stringify({ ...request, model, timeoutMs }),
      });
    try {
      const active = call('active', 600_000);
      await vi.waitFor(() => expect(seen).toEqual(['active']));
      const queued = call('queued', 1000);
      const status = queued.then((r) => r.status);
      await vi.advanceTimersByTimeAsync(1500);
      // Responds 502 while the earlier completion is still active.
      expect(await status).toBe(502);
      release();
      expect((await active).status).toBe(200);
      // Expired request freed its slot and was skipped by the runner.
      expect((await call('later', 5000)).status).toBe(200);
      expect(seen).toEqual(['active', 'later']);
    } finally {
      vi.useRealTimers();
      bridge.close();
    }
  });
});
