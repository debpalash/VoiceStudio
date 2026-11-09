import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  dub: {
    jobId: 'job' as string | null,
    filename: 'take.wav',
    project: null as null | { id: string; name: string },
    phase: 'editing',
    recovery: null,
  },
  api: vi.fn(async () => ({ id: 'saved' })),
  attach: vi.fn(),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/api/client', () => ({ apiJson: state.api, describeError: String }));
vi.mock('../longform/longform-session', () => ({ useLongformSession: () => ({ active: null }) }));
vi.mock('../projects/project-format', () => ({
  projectPayload: (_session: unknown, name: string) => ({ name }),
}));
vi.mock('./dub-session', () => ({
  useDubSession: () => state.dub,
  attachDubProject: state.attach,
}));
import { DubSaveProject } from './dub-save-project';

afterEach(cleanup);

const mount = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DubSaveProject />
    </QueryClientProvider>,
  );

it('saves the open dub as a project from the Dubbing page', async () => {
  mount();
  const name = screen.getByLabelText('sidebar.save_project');
  expect(name).toHaveValue('take.wav');
  fireEvent.change(name, { target: { value: 'Interview' } });
  fireEvent.click(screen.getByRole('button', { name: 'common.save' }));
  await waitFor(() =>
    expect(state.api).toHaveBeenCalledWith('/projects', {
      method: 'POST',
      body: JSON.stringify({ name: 'Interview' }),
    }),
  );
  expect(state.attach).toHaveBeenCalledWith(state.dub, { name: 'Interview', id: 'saved' });
  expect(await screen.findByRole('status')).toHaveTextContent('app.toast_project_saved');
});

it('stays out of the way until there is a dub to save', () => {
  state.dub.jobId = null;
  const { container } = mount();
  expect(container).toBeEmptyDOMElement();
  state.dub.jobId = 'job';
});
