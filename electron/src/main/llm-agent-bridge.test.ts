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


it('expires a queued completion at its own deadline while an earlier call is still running', async () => {
  let releaseFirst!: () => void;
  let enteredFirst!: () => void;
  const entered = new Promise<void>((resolve) => { enteredFirst = resolve; });
  const waiting = new Promise<void>((resolve) => { releaseFirst = resolve; });
  const complete = vi.fn(async () => { enteredFirst(); await waiting; return 'ok'; });
  const bridge = await startLlmAgentBridge(complete);
  const call = (timeoutMs: number) => fetch(bridge.url + '/complete', {
    method: 'POST', headers: { Authorization: 'Bearer ' + bridge.token },
    body: JSON.stringify({ ...request, timeoutMs }),
  });
  const first = call(5000);
  let second: Promise<Response> | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await entered;
    second = call(1000);
    const response = await Promise.race([
      second,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error('queued request ignored its deadline')), 1800);
      }),
    ]);
    expect(response.status).toBe(504);
    expect(complete).toHaveBeenCalledOnce();
  } finally {
    clearTimeout(timer);
    releaseFirst();
    await first;
    await second;
    bridge.close();
  }
});
