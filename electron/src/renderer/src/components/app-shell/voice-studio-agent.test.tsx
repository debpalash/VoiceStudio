import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { VoiceStudioAgent } from './voice-studio-agent';
import { agentConversation, receiveAgentEvent } from '@/lib/agent-conversation';
const mocks = vi.hoisted(() => ({
  repair: {
    list: vi.fn(),
    getState: vi.fn(),
    onEvent: vi.fn(() => () => {}),
    start: vi.fn(),
    stop: vi.fn(async () => ({})),
    chooseWorkspace: vi.fn(),
  },
  app: { platform: 'win32' },
}));
vi.mock('@/components/bridge', () => ({ getBridge: () => mocks }));
vi.mock('@/hooks/use-backend-status', () => ({
  useBackendStatus: () => ({ stage: 'ready', logTail: [] }),
}));
vi.mock('@tanstack/react-router', () => ({ useRouterState: () => '/' }));
vi.mock('./agent-dock-frame', () => ({
  AgentDockFrame: ({ children }: { children: ReactNode }) => <section>{children}</section>,
}));
vi.mock('@shared/utils/consoleBuffer', () => ({ getFrontendLogs: () => [] }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, args?: { feature?: string }) =>
      args?.feature ? `${key}: ${args.feature}` : key,
  }),
}));
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  agentConversation.setState(() => ({ messages: [], draft: '', activeId: '' }));
  mocks.repair.list.mockResolvedValue([
    { id: 'codex', label: 'Codex', available: true },
    { id: 'claude', label: 'Claude Code', available: false },
  ]);
  mocks.repair.getState.mockResolvedValue({
    status: 'idle',
    output: '',
    workspaceAvailable: false,
  });
  mocks.repair.start.mockResolvedValue({ sessionId: 'one' });
});
afterEach(cleanup);
async function open() {
  render(<VoiceStudioAgent />);
  fireEvent.click(screen.getByRole('button', { name: 'repairAgent.title' }));
  await screen.findByRole('button', { name: 'Codex' });
}
it('runs a feature preset without a checkout and keeps the reply for follow-up chat', async () => {
  await open();
  fireEvent.click(screen.getByRole('button', { name: 'audiobook.title' }));
  fireEvent.click(screen.getByRole('button', { name: 'agentWorkspace.send' }));
  await waitFor(() =>
    expect(mocks.repair.start).toHaveBeenCalledWith(
      expect.objectContaining({
        workspace: 'app',
        features: ['audiobook'],
        mode: 'fix',
        history: [],
      }),
    ),
  );
  act(() => {
    receiveAgentEvent({ type: 'output', sessionId: 'one', text: 'Created book-123' });
    receiveAgentEvent({ type: 'state', sessionId: 'one', status: 'complete' });
  });
  expect(screen.getByText('Created book-123')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'common.close' }));
  fireEvent.click(screen.getByRole('button', { name: 'repairAgent.title' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Render that preview' } });
  fireEvent.click(screen.getByRole('button', { name: 'agentWorkspace.send' }));
  await waitFor(() => expect(mocks.repair.start).toHaveBeenCalledTimes(2));
  expect(mocks.repair.start.mock.calls[1][0].history).toContainEqual({
    role: 'assistant',
    content: 'Created book-123',
  });
});
it('offers read-only planning, disables unavailable harnesses, and stops a running request', async () => {
  await open();
  expect(screen.getByRole('button', { name: 'Claude Code' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'agentWorkspace.plan' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Check my models' } });
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
  await waitFor(() =>
    expect(mocks.repair.start).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'diagnose', workspace: 'app' }),
    ),
  );
  fireEvent.click(screen.getByRole('button', { name: 'common.stop' }));
  expect(mocks.repair.stop).toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'agentWorkspace.new_chat' })).toBeDisabled();
});
it('requires an attached checkout only for explicitly selected source repair', async () => {
  await open();
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Fix the code' } });
  expect(screen.getByRole('button', { name: 'agentWorkspace.send' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'agentWorkspace.features' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'agentWorkspace.source' }));
  expect(screen.getByRole('button', { name: 'agentWorkspace.send' })).toBeDisabled();
  mocks.repair.chooseWorkspace.mockResolvedValue({
    workspaceAvailable: true,
    workspacePath: '/source',
  });
  fireEvent.click(screen.getByRole('button', { name: 'settings.models_dir_choose' }));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'agentWorkspace.send' })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'agentWorkspace.send' }));
  await waitFor(() =>
    expect(mocks.repair.start).toHaveBeenCalledWith(
      expect.objectContaining({ workspace: 'source' }),
    ),
  );
});


it.each([
  ['Renderer crashed unexpectedly', 'source'],
  ['ACTION_REQUEST: Restart the backend', 'app'],
])('routes automatic repair to the appropriate workspace: %s', async (report, workspace) => {
  const { DEFAULT_REPAIR_AGENT_KEY, openRepairAgent } = await import('@/lib/repair-agent-events');
  localStorage.setItem(DEFAULT_REPAIR_AGENT_KEY, 'codex');
  mocks.repair.getState.mockResolvedValue({
    status: 'idle', output: '', workspaceAvailable: workspace === 'source',
  });
  render(<VoiceStudioAgent />);
  act(() => openRepairAgent(report, true));
  await waitFor(() => expect(mocks.repair.start).toHaveBeenCalledWith(
    expect.objectContaining({ workspace, report }),
  ));
});


it('keeps crash repair ready for Send until a source checkout is attached', async () => {
  const { DEFAULT_REPAIR_AGENT_KEY, openRepairAgent } = await import('@/lib/repair-agent-events');
  localStorage.setItem(DEFAULT_REPAIR_AGENT_KEY, 'codex');
  render(<VoiceStudioAgent />);
  act(() => openRepairAgent('Renderer crashed unexpectedly', true));
  const choose = await screen.findByRole('button', { name: 'settings.models_dir_choose' });
  expect(screen.getByRole('textbox')).toHaveValue('Renderer crashed unexpectedly');
  expect(screen.getByRole('button', { name: 'agentWorkspace.send' })).toBeDisabled();
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
  expect(mocks.repair.start).not.toHaveBeenCalled();
  mocks.repair.chooseWorkspace.mockResolvedValue({ workspaceAvailable: true, workspacePath: '/source' });
  fireEvent.click(choose);
  await waitFor(() => expect(screen.getByRole('button', { name: 'agentWorkspace.send' })).toBeEnabled());
  expect(mocks.repair.start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'agentWorkspace.send' }));
  await waitFor(() => expect(mocks.repair.start).toHaveBeenCalledWith(
    expect.objectContaining({ workspace: 'source', report: 'Renderer crashed unexpectedly' }),
  ));
});
it('keeps the newest CLI scan when an older one answers last', async () => {
  let answerMount!: (agents: unknown) => void;
  mocks.repair.list.mockImplementation((options?: { refresh?: boolean }) =>
    options?.refresh
      ? Promise.resolve([
          { id: 'codex', label: 'Codex', available: true },
          { id: 'claude', label: 'Claude Code', available: true },
        ])
      : new Promise((resolve) => {
          answerMount = resolve;
        }),
  );
  render(<VoiceStudioAgent />);
  fireEvent.click(screen.getByRole('button', { name: 'repairAgent.title' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Claude Code' })).toBeEnabled());
  // The mount scan started first but answers after the refresh: it is stale.
  await act(async () => {
    answerMount([
      { id: 'codex', label: 'Codex', available: true },
      { id: 'claude', label: 'Claude Code', available: false },
    ]);
  });
  expect(screen.getByRole('button', { name: 'Claude Code' })).toBeEnabled();
});
