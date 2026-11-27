import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';

const task = vi.hoisted(() => ({
  api: vi.fn(),
  cancelDub: vi.fn(),
  resumeDub: vi.fn(),
  retryTake: vi.fn(),
  navigate: vi.fn(),
  session: {
    phase: 'generating',
    filename: 'Interview.mp4',
    event: { current: 2, total: 4 },
    error: null,
    failedPhase: undefined,
    recovery: null,
  } as {
    phase: string;
    filename: string;
    event: { current: number; total: number };
    error: string | null;
    failedPhase?: string;
    recovery: string | null;
  },
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => task.navigate }));
vi.mock('@/lib/api/client', () => ({ apiJson: task.api, describeError: String }));
vi.mock('@/hooks/use-generate', () => ({
  useGenerationActivity: () => ({
    queue: [{ id: 'take-1', label: 'Saved script', mode: 'design', status: 'failed' }],
    retryFailed: task.retryTake,
  }),
}));
vi.mock('@/features/dub/dub-session', () => ({
  useDubSession: () => task.session,
  useDubCancelling: () => false,
  cancelDub: task.cancelDub,
  resumeDub: task.resumeDub,
}));

import { TaskActivityList, useTaskActivity } from './task-activity';

function Panel({ enabled = false }: { enabled?: boolean }) {
  const { tasks } = useTaskActivity(enabled, true);
  return <TaskActivityList tasks={tasks} />;
}

it('shows a retryable batch query failure and clears it after retry', async () => {
  task.api.mockRejectedValueOnce(new Error('Unavailable')).mockResolvedValueOnce([]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <Panel enabled />
    </QueryClientProvider>,
  );
  const error = (await screen.findByText('nav.batch_dub')).closest('article')!;
  expect(within(error).getByText('common.error')).toBeVisible();
  fireEvent.click(within(error).getByRole('button', { name: 'common.retry' }));
  await waitFor(() => expect(error).not.toBeInTheDocument());
  expect(task.api.mock.calls.map(([path]) => path)).toEqual([
    '/batch/jobs?status=retryable&limit=100',
    '/batch/jobs?status=retryable&limit=100',
  ]);
});

it('shows live progress and lets the user cancel a dub and retry a failed take', () => {
  render(<Panel />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
    ),
  });
  const rows = screen.getAllByRole('article');
  expect(within(rows[1]).getByRole('progressbar')).toHaveValue(50);
  fireEvent.click(within(rows[1]).getByRole('button', { name: 'common.cancel' }));
  fireEvent.click(within(rows[0]).getByRole('button', { name: 'common.retry' }));
  expect(task.cancelDub).toHaveBeenCalledOnce();
  expect(task.retryTake).toHaveBeenCalledWith('take-1');
});

it('names the failed dub step and resumes its checkpoint', () => {
  Object.assign(task.session, {
    phase: 'editing',
    error: 'Lost connection',
    failedPhase: 'generating',
    recovery: 'generating',
  });
  render(<Panel />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
    ),
  });
  expect(
    screen.getByText(/dub_workflow.generating_dub.*generationQueue.status.failed/),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'common.resume' }));
  expect(task.resumeDub).toHaveBeenCalledOnce();
});
