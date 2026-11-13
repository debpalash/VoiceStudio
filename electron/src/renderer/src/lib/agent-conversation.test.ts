import { beforeEach, expect, it } from 'vitest';
import {
  agentConversation,
  beginAgentTurn,
  conversationHistory,
  receiveAgentEvent,
  restoreAgentTurn,
} from './agent-conversation';
beforeEach(() => agentConversation.setState(() => ({ messages: [], activeId: '', draft: '' })));
it('streams the active reply without mixing sessions and keeps history for follow-ups', () => {
  beginAgentTurn('Create a short audiobook');
  receiveAgentEvent({ type: 'output', sessionId: 'previous', text: 'Late output from stopped run' });
  receiveAgentEvent({ type: 'state', sessionId: 'one', status: 'running' });
  receiveAgentEvent({ type: 'output', sessionId: 'one', text: 'Created book-1.' });
  receiveAgentEvent({ type: 'output', sessionId: 'stale', text: 'Wrong session' });
  receiveAgentEvent({ type: 'state', sessionId: 'one', status: 'complete' });
  expect(conversationHistory()).toEqual([
    { role: 'user', content: 'Create a short audiobook' },
    { role: 'assistant', content: 'Created book-1.' },
  ]);
  expect(agentConversation.state.messages.at(-1)?.status).toBe('complete');
});
it('restores missed output after navigating without duplicating an existing turn', () => {
  beginAgentTurn('Inspect settings');
  receiveAgentEvent({ type: 'state', sessionId: 'one', status: 'running' });
  restoreAgentTurn({
    sessionId: 'one',
    output: 'All settings checked',
    status: 'complete',
    workspaceAvailable: false,
  });
  expect(agentConversation.state.messages).toHaveLength(2);
  expect(conversationHistory().at(-1)?.content).toBe('All settings checked');
});
it('bounds follow-up context and leaves a running partial reply out of it', () => {
  for (let i = 0; i < 8; i++) {
    beginAgentTurn('x'.repeat(5000));
    receiveAgentEvent({ type: 'state', sessionId: String(i), status: 'complete' });
  }
  beginAgentTurn('Continue');
  expect(conversationHistory().length).toBeLessThanOrEqual(12);
  expect(conversationHistory().every((message) => message.content.length <= 4000)).toBe(true);
});
